"""Operational state: the last-known-good copy of the simulator world.

The dashboard and the intelligence layer read from here, never from the simulator directly.
Each resource is refreshed independently: if one call fails, the others still update and the
failed one keeps its previous value, marked stale with its age.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.contracts import FUELS, Allocation, Freshness, InTransitLeg, NetworkSnapshot, ResourceFreshness
from app.obs.logging import log_event
from app.obs.metrics import SERVICE_LEVEL, SNAPSHOT_AGE, SNAPSHOT_STALE, SNAPSHOT_TICK
from app.sim.client import SimulatorClient
from app.sim.errors import CircuitOpenError, SimulatorError

# Resource name -> client method. All of these go into the snapshot.
RESOURCES = ("instance", "regions", "depots", "stations", "routes", "supply_arrivals", "events",
             "allocations", "metrics")
REQUIRED = ("instance", "depots", "stations", "routes", "metrics")
LIVE = ("PENDING", "IN_TRANSIT")
RECENT_TTL = 15.0  # seconds; the poller sees a new allocation within ~1 s, so this is generous


@dataclass
class _Resource:
    data: Any = None
    fetched_at: datetime | None = None
    stale: bool = True
    last_error: str | None = None
    started: float = 0.0  # monotonic start of the fetch that produced the current value


@dataclass
class StateStore:
    client: SimulatorClient
    _res: dict[str, _Resource] = field(default_factory=lambda: {r: _Resource() for r in RESOURCES})
    _snapshot: NetworkSnapshot | None = None
    _was_stale: bool = False
    # Allocations we posted that a fetched list may not show yet (the fetch started before the POST).
    _recent: dict[str, tuple[float, Allocation]] = field(default_factory=dict)

    @property
    def snapshot(self) -> NetworkSnapshot | None:
        """The latest snapshot, or None before the first successful refresh."""
        if self._snapshot is not None:
            # Ages move on even when nothing is refreshed; recompute them on read.
            self._snapshot = self._snapshot.model_copy(update={"freshness": self._freshness()})
        return self._snapshot

    def allocations_by_key(self) -> dict[str, Allocation]:
        """Every allocation the simulator knows about, by idempotency key (includes finished ones)."""
        return {a.idempotency_key: a for a in self._allocations()}

    def record_posted(self, alloc: Allocation) -> None:
        """Called by the writer right after a successful POST.

        GET /v1/allocations returns the whole, ever-growing list and gets slow (140 ms at ~800 rows), so
        the writer doesn't re-fetch it before every submission. Instead the allocations it posted are
        merged into every snapshot until a fetched list contains them.
        """
        self._recent[alloc.idempotency_key] = (time.monotonic(), alloc)
        if self._snapshot is not None and all(self._res[r].data is not None for r in REQUIRED):
            self._snapshot = self._build(datetime.now(UTC))

    def _allocations(self) -> list[Allocation]:
        fetched: list[Allocation] = self._res["allocations"].data or []
        if not self._recent:
            return fetched
        seen = {a.idempotency_key for a in fetched}
        now = time.monotonic()
        # Drop entries the simulator now lists, and any older than RECENT_TTL (e.g. after a reset).
        for key in [k for k, (at, _) in self._recent.items() if k in seen or now - at > RECENT_TTL]:
            del self._recent[key]
        return [a for _, a in self._recent.values()] + fetched

    async def refresh(self, only: tuple[str, ...] | None = None) -> NetworkSnapshot | None:
        """Fetch resources concurrently and rebuild the snapshot. `only` limits the fetch.

        Fetches are not serialized: a caller that needs fresh data (the allocation writer) never waits
        behind the poller's slow allocation list. A result is applied only if its fetch started after
        the one currently stored, so an older response can never overwrite a newer one.
        """
        names = only or RESOURCES
        started = time.monotonic()
        results = await asyncio.gather(*(getattr(self.client, name)() for name in names), return_exceptions=True)
        now = datetime.now(UTC)
        for name, result in zip(names, results, strict=True):
            res = self._res[name]
            if started < res.started:
                continue  # a fetch that began later has already been applied
            res.started = started
            if isinstance(result, BaseException):
                if not isinstance(result, SimulatorError):
                    log_event("state.refresh_bug", resource=name, error=repr(result))
                res.stale = True
                res.last_error = "circuit open" if isinstance(result, CircuitOpenError) else str(result)[:200]
            else:
                res.data, res.fetched_at, res.stale, res.last_error = result.data, now, result.stale, None
        if all(self._res[r].data is not None for r in REQUIRED):
            self._snapshot = self._build(now)
            self._publish_metrics()
        return self._snapshot

    # ------------------------------------------------------------------ building

    def _build(self, now: datetime) -> NetworkSnapshot:
        inst = self._res["instance"].data
        allocations = self._allocations()
        legs = [InTransitLeg(allocation_id=a.id, route_id=a.route_id, source_depot_id=a.source_depot_id,
                             station_id=a.destination_station_id, fuel_type=a.fuel_type, quantity=a.quantity,
                             status=a.status, expected_arrival_tick=a.expected_arrival_tick)
                for a in allocations if a.status in LIVE]
        totals: dict[str, dict] = defaultdict(lambda: {f: 0.0 for f in FUELS})
        for leg in legs:
            totals[leg.station_id][leg.fuel_type] += leg.quantity
        # Verified against the simulator: only allocations created this tick count against a depot's
        # dispatch capacity; shipments from earlier ticks that are still on the road do not.
        dispatched: dict[str, float] = defaultdict(float)
        for a in allocations:
            if a.created_tick == inst.tick and a.status not in ("CANCELLED", "FAILED"):
                dispatched[a.source_depot_id] += a.quantity
        return NetworkSnapshot(
            tick=inst.tick, sim_time=inst.sim_time, tick_minutes=inst.tick_minutes, sim_status=inst.status,
            scenario_id=inst.scenario_id, seed=inst.seed, built_at=now, freshness=self._freshness(now),
            regions=self._res["regions"].data or [], depots=self._res["depots"].data,
            stations=self._res["stations"].data, routes=self._res["routes"].data,
            supply_arrivals=self._res["supply_arrivals"].data or [], events=self._res["events"].data or [],
            in_transit=legs, metrics=self._res["metrics"].data,
            in_transit_totals={s: dict(v) for s, v in totals.items()}, dispatched_this_tick=dict(dispatched))

    def _freshness(self, now: datetime | None = None) -> Freshness:
        now = now or datetime.now(UTC)
        resources, reasons = {}, []
        for name, res in self._res.items():
            age = (now - res.fetched_at).total_seconds() if res.fetched_at else None
            resources[name] = ResourceFreshness(fetched_at=res.fetched_at, age_seconds=age, stale=res.stale,
                                                last_error=res.last_error)
            if res.stale:
                reasons.append(f"{name}: {res.last_error or 'X-Simulator-Stale'}")
        circuit = self.client.breaker.state
        if circuit != "CLOSED":
            reasons.insert(0, f"simulator circuit {circuit}")
        return Freshness(stale=bool(reasons), reasons=reasons, circuit=circuit, resources=resources)

    def _publish_metrics(self) -> None:
        snap = self._snapshot
        ages = [r.age_seconds for r in snap.freshness.resources.values() if r.age_seconds is not None]
        SNAPSHOT_AGE.set(max(ages) if ages else 0)
        SNAPSHOT_STALE.set(1 if snap.freshness.stale else 0)
        SNAPSHOT_TICK.set(snap.tick)
        SERVICE_LEVEL.set(snap.metrics.service_level)
        if snap.freshness.stale != self._was_stale:
            log_event("sim.stale_detected" if snap.freshness.stale else "sim.stale_cleared",
                      tick=snap.tick, reasons=snap.freshness.reasons[:5])
            self._was_stale = snap.freshness.stale
