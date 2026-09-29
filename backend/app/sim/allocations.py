"""Allocation writer: the only code path that changes the simulated world.

Before posting, every leg is checked against the current snapshot. Some of these checks are
stricter than the simulator's own, on purpose. We verified in hour one that:
  - the simulator's capacity check ignores fuel already in transit, and on arrival the tank is
    clipped at capacity: the excess is lost for good;
  - a FAILED allocation (route disrupted at departure) does not refund the depot.
So we count in-transit fuel against tank headroom and refuse to post over a route that has a
disruption scheduled at departure time. Both mistakes would destroy fuel.

Idempotency keys are deterministic (fg-{decision_id}-{leg}), so a retried submission never creates a
second shipment.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from app.contracts import AllocationLeg, NetworkSnapshot, SubmitAllocationsResponse, SubmittedAllocation
from app.obs.logging import log_event
from app.obs.metrics import ALLOCATIONS
from app.sim.client import SimulatorClient
from app.sim.errors import CircuitOpenError, SimulatorAPIError, SimulatorUnavailable
from app.state.store import StateStore

EPS = 1e-6
# Resources the pre-check re-reads. Allocations come from the last poll plus our own recent posts.
WRITE_INPUTS = ("instance", "depots", "stations", "routes", "events")
# A disruption that starts this close to "now" would catch a shipment at departure.
DEPARTURE_MARGIN_TICKS = 1


def idempotency_key(decision_id: str, leg_index: int) -> str:
    return f"fg-{decision_id}-{leg_index}"


def split_leg(leg: AllocationLeg, max_shipment: float) -> list[AllocationLeg]:
    """Split a leg into chunks of at most max_shipment litres."""
    if leg.quantity <= max_shipment + EPS:
        return [leg]
    parts, left = [], leg.quantity
    while left > EPS:
        q = min(left, max_shipment)
        parts.append(leg.model_copy(update={"quantity": round(q, 3)}))
        left -= q
    return parts


def precheck(snap: NetworkSnapshot, legs: list[AllocationLeg]) -> list[str | None]:
    """Return one error code (or None) per leg, simulating the effect of earlier legs in the batch."""
    routes = {r.id: r for r in snap.routes}
    depots = {d.id: d for d in snap.depots}
    stations = {s.id: s for s in snap.stations}
    depot_used: dict[tuple[str, str], float] = defaultdict(float)
    dispatch_used: dict[str, float] = defaultdict(float, snap.dispatched_this_tick)
    arriving: dict[tuple[str, str], float] = defaultdict(float)
    for station_id, per_fuel in snap.in_transit_totals.items():
        for fuel, q in per_fuel.items():
            arriving[(station_id, fuel)] += q
    disrupted_soon = {rid for e in snap.events if e.type == "route_disruption" and e.status != "RESOLVED"
                      and e.start_tick <= snap.tick + DEPARTURE_MARGIN_TICKS and e.end_tick > snap.tick
                      for rid in (e.parameters.get("route_ids") or list(routes))}
    outage_soon = {sid for e in snap.events if e.type == "station_outage" and e.status != "RESOLVED"
                   and e.start_tick <= snap.tick + DEPARTURE_MARGIN_TICKS and e.end_tick > snap.tick
                   for sid in (e.parameters.get("station_ids") or list(stations))}

    out: list[str | None] = []
    for leg in legs:
        route, depot, station = routes.get(leg.route_id), depots.get(leg.source_depot_id), stations.get(leg.station_id)
        fuel = leg.fuel_type
        if route is None or depot is None or station is None:
            code = "PRECHECK_NOT_FOUND"
        elif route.source_depot_id != depot.id or route.destination_station_id != station.id:
            code = "PRECHECK_ROUTE_MISMATCH"
        elif route.status != "AVAILABLE" or route.id in disrupted_soon:
            code = "PRECHECK_ROUTE_DISRUPTED"
        elif station.status != "OPEN" or station.id in outage_soon:
            code = "PRECHECK_STATION_CLOSED"
        elif leg.quantity > route.max_shipment + EPS:
            code = "PRECHECK_ROUTE_CAPACITY"
        elif depot.inventory.get(fuel, 0) - depot_used[(depot.id, fuel)] < leg.quantity - EPS:
            code = "PRECHECK_INSUFFICIENT_INVENTORY"
        elif dispatch_used[depot.id] + leg.quantity > depot.dispatch_capacity_per_tick + EPS:
            code = "PRECHECK_DISPATCH_CAPACITY"
        elif (station.inventory.get(fuel, 0) + arriving[(station.id, fuel)] + leg.quantity
              > station.capacity.get(fuel, 0) + EPS):
            code = "PRECHECK_TANK_HEADROOM"
        else:
            code = None
            depot_used[(depot.id, fuel)] += leg.quantity
            dispatch_used[depot.id] += leg.quantity
            arriving[(station.id, fuel)] += leg.quantity
        out.append(code)
    return out


class AllocationWriter:
    def __init__(self, client: SimulatorClient, store: StateStore):
        self.client = client
        self.store = store
        self._lock = asyncio.Lock()  # one submission at a time, so batch accounting stays correct

    async def submit(self, decision_id: str, legs: list[AllocationLeg]) -> SubmitAllocationsResponse:
        async with self._lock:
            # Re-read what the pre-check depends on, fetched after this moment. Our own earlier posts
            # are merged in by the store (record_posted), so the slow allocation list isn't re-fetched.
            first = self.store.snapshot is None
            snap = await self.store.refresh(None if first else WRITE_INPUTS) or self.store.snapshot
            results: list[SubmittedAllocation] = []
            if snap is None or snap.freshness.circuit == "OPEN":
                for i, leg in enumerate(legs):
                    results.append(self._record(leg, idempotency_key(decision_id, i), None, "held",
                                                "SIMULATOR_UNAVAILABLE", "No fresh simulator state; held for review."))
                return SubmitAllocationsResponse(decision_id=decision_id, submissions=results)

            max_ship = {r.id: r.max_shipment for r in snap.routes}
            expanded = [part for leg in legs for part in split_leg(leg, max_ship.get(leg.route_id, leg.quantity))]
            keys = [idempotency_key(decision_id, i) for i in range(len(expanded))]
            # Legs already posted under the same key are replays: the snapshot already counts them, so
            # they skip the pre-check and go straight to the simulator, which returns the original.
            existing = self.store.allocations_by_key()
            fresh = [i for i, k in enumerate(keys) if k not in existing]
            checks: list[str | None] = [None] * len(expanded)
            for i, code in zip(fresh, precheck(snap, [expanded[i] for i in fresh]), strict=True):
                checks[i] = code
            for leg, key, check in zip(expanded, keys, checks, strict=True):
                if check:
                    results.append(self._record(leg, key, None, "skipped", check, "Blocked by FuelGuard pre-check."))
                    continue
                results.append(await self._post(leg, key))
            # No refresh here: the next submission re-reads before its pre-check, and the poller
            # updates the UI within a second.
            return SubmitAllocationsResponse(decision_id=decision_id, submissions=results)

    async def _post(self, leg: AllocationLeg, key: str) -> SubmittedAllocation:
        try:
            alloc = await self.client.create_allocation(
                idempotency_key=key, source_depot_id=leg.source_depot_id, destination_station_id=leg.station_id,
                route_id=leg.route_id, fuel_type=leg.fuel_type.value, quantity=leg.quantity)
            # The simulator answers 201 for both a new allocation and an idempotent replay.
            self.store.record_posted(alloc)
            return self._record(leg, key, 201, "accepted", None, None, alloc.id)
        except SimulatorAPIError as exc:
            return self._record(leg, key, exc.status, "rejected", exc.code, exc.message)
        except (SimulatorUnavailable, CircuitOpenError) as exc:
            status = getattr(exc, "status", None)
            return self._record(leg, key, status, "held", "SIMULATOR_UNAVAILABLE", str(exc)[:200])

    async def cancel(self, allocation_id: int):
        return await self.client.cancel_allocation(allocation_id)

    async def cancel_pending_on_disrupted_routes(self) -> list[int]:
        """Cancel PENDING legs on routes that are disrupted, so their depot stock is refunded."""
        snap = self.store.snapshot
        if snap is None:
            return []
        bad = {r.id for r in snap.routes if r.status != "AVAILABLE"}
        cancelled = []
        for leg in snap.in_transit:
            if leg.status == "PENDING" and leg.route_id in bad:
                try:
                    await self.client.cancel_allocation(leg.allocation_id)
                    cancelled.append(leg.allocation_id)
                    log_event("allocation.cancelled", allocation_id=leg.allocation_id, route=leg.route_id,
                              reason="route disrupted")
                except (SimulatorAPIError, SimulatorUnavailable, CircuitOpenError) as exc:
                    log_event("allocation.cancel_failed", allocation_id=leg.allocation_id, error=str(exc)[:200])
        return cancelled

    @staticmethod
    def _record(leg, key, status, result, code, message, sim_id=None) -> SubmittedAllocation:
        ALLOCATIONS.labels(result, code or "OK").inc()
        log_event(f"allocation.{result}", idempotency_key=key, route=leg.route_id, fuel=leg.fuel_type.value,
                  litres=leg.quantity, sim_status=status, code=code, sim_allocation_id=sim_id)
        return SubmittedAllocation(leg=leg, idempotency_key=key, http_status=status, sim_allocation_id=sim_id,
                                   result=result, error_code=code, message=message)
