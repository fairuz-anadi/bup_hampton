"""FuelGuard RL State Space Representation.

Constructs normalized, fixed-length state feature vectors from live or simulated
NetworkSnapshot instances, incorporating depot/station inventories, fuel demands,
forecasts, route availabilities, in-transit volumes, disruptions, and temporal factors.
"""
from __future__ import annotations

from typing import Any
import numpy as np

from app.contracts import (
    FUELS,
    FuelType,
    NetworkSnapshot,
    RouteStatus,
    StationStatus,
)

# Standardized topology sizes for fixed observation representation
MAX_DEPOTS = 2
MAX_STATIONS = 4
MAX_ROUTES = 8
FUELS_COUNT = 3  # DIESEL, PETROL, OCTANE


class RLStateExtractor:
    """Vectorizes NetworkSnapshot into a normalized numerical observation vector for RL."""

    def __init__(
        self,
        max_depots: int = MAX_DEPOTS,
        max_stations: int = MAX_STATIONS,
        max_routes: int = MAX_ROUTES,
    ):
        self.max_depots = max_depots
        self.max_stations = max_stations
        self.max_routes = max_routes

        # Feature dimensions:
        # 1. Depot inventories: max_depots * 3 (normalized by depot capacity)
        # 2. Depot dispatch remaining: max_depots (normalized by dispatch_capacity)
        # 3. Station inventories: max_stations * 3 (normalized by station capacity)
        # 4. Station demand estimates: max_stations * 3 (normalized by 5000L)
        # 5. Station forecasts: max_stations * 3 (normalized by 5000L)
        # 6. Station operational status: max_stations (1.0 = OPEN, 0.0 = OUTAGE)
        # 7. Route availability: max_routes (1.0 = AVAILABLE, 0.0 = DISRUPTED)
        # 8. Route in-transit load: max_routes (normalized by max_shipment)
        # 9. Route transit ticks: max_routes (normalized by 10.0)
        # 10. Active crisis indicators: 5 binary flags (spike, disruption, constraint, outage, delay)
        # 11. Time & tick indicators: 2 values (diurnal cycle phase, tick progress)
        self.dim = (
            (self.max_depots * FUELS_COUNT)
            + self.max_depots
            + (self.max_stations * FUELS_COUNT)
            + (self.max_stations * FUELS_COUNT)
            + (self.max_stations * FUELS_COUNT)
            + self.max_stations
            + self.max_routes
            + self.max_routes
            + self.max_routes
            + 5
            + 2
        )

    def extract(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], Any] | None = None,
    ) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        idx = 0

        # Sort entities deterministically by ID
        depots = sorted(snapshot.depots, key=lambda d: d.id)[: self.max_depots]
        stations = sorted(snapshot.stations, key=lambda s: s.id)[: self.max_stations]
        routes = sorted(snapshot.routes, key=lambda r: r.id)[: self.max_routes]

        # 1. Depot Inventories (normalized by capacity per fuel)
        for d_idx in range(self.max_depots):
            if d_idx < len(depots):
                depot = depots[d_idx]
                for f in FUELS:
                    cap = depot.capacity.get(f, 90000.0) or 90000.0
                    inv = depot.inventory.get(f, 0.0)
                    vec[idx] = np.clip(inv / max(1.0, cap), 0.0, 2.0)
                    idx += 1
            else:
                idx += FUELS_COUNT

        # 2. Depot Dispatch Capacity Remaining This Tick
        for d_idx in range(self.max_depots):
            if d_idx < len(depots):
                depot = depots[d_idx]
                committed = snapshot.dispatched_this_tick.get(depot.id, 0.0)
                max_cap = depot.dispatch_capacity_per_tick or 12000.0
                remaining = max(0.0, max_cap - committed)
                vec[idx] = np.clip(remaining / max(1.0, max_cap), 0.0, 1.0)
                idx += 1
            else:
                idx += 1

        # 3. Station Inventories (normalized by capacity per fuel)
        for s_idx in range(self.max_stations):
            if s_idx < len(stations):
                station = stations[s_idx]
                for f in FUELS:
                    cap = station.capacity.get(f, 25000.0) or 25000.0
                    inv = station.inventory.get(f, 0.0)
                    vec[idx] = np.clip(inv / max(1.0, cap), 0.0, 2.0)
                    idx += 1
            else:
                idx += FUELS_COUNT

        # 4. Station Demand Observations / Current Needs
        for s_idx in range(self.max_stations):
            if s_idx < len(stations):
                station = stations[s_idx]
                for f in FUELS:
                    # In-transit total heading to this station for this fuel
                    in_transit_val = 0.0
                    if snapshot.in_transit_totals and station.id in snapshot.in_transit_totals:
                        in_transit_val = snapshot.in_transit_totals[station.id].get(f, 0.0)
                    inv = station.inventory.get(f, 0.0)
                    cap = station.capacity.get(f, 25000.0) or 25000.0
                    # Net need = capacity - (inventory + in_transit)
                    net_need = max(0.0, cap - (inv + in_transit_val))
                    vec[idx] = np.clip(net_need / 10000.0, 0.0, 3.0)
                    idx += 1
            else:
                idx += FUELS_COUNT

        # 5. Forecasted Demand
        for s_idx in range(self.max_stations):
            if s_idx < len(stations):
                station = stations[s_idx]
                for f in FUELS:
                    fc_val = 1200.0  # default expected demand per tick
                    if forecasts and (station.id, f.value) in forecasts:
                        fc = forecasts[(station.id, f.value)]
                        if hasattr(fc, "series") and fc.series:
                            fc_val = float(np.mean(fc.series[0].mean[:4])) if fc.series[0].mean else 1200.0
                        elif hasattr(fc, "bands") and fc.bands:
                            fc_val = float(np.mean([b.mean for b in fc.bands[:4]])) if fc.bands else 1200.0
                    vec[idx] = np.clip(fc_val / 5000.0, 0.0, 3.0)
                    idx += 1
            else:
                idx += FUELS_COUNT

        # 6. Station Operational Status (1.0 = OPEN, 0.0 = OUTAGE)
        for s_idx in range(self.max_stations):
            if s_idx < len(stations):
                st = stations[s_idx].status
                vec[idx] = 1.0 if st in ("OPEN", StationStatus.OPEN) else 0.0
                idx += 1
            else:
                idx += 1

        # 7. Route Availability (1.0 = AVAILABLE, 0.0 = DISRUPTED)
        for r_idx in range(self.max_routes):
            if r_idx < len(routes):
                r = routes[r_idx]
                vec[idx] = 1.0 if r.status in ("AVAILABLE", RouteStatus.AVAILABLE) else 0.0
                idx += 1
            else:
                idx += 1

        # 8. Route In-Transit Load (normalized by max_shipment)
        # Sum in_transit legs per route
        route_loads: dict[str, float] = {}
        for leg in snapshot.in_transit:
            route_loads[leg.route_id] = route_loads.get(leg.route_id, 0.0) + leg.quantity

        for r_idx in range(self.max_routes):
            if r_idx < len(routes):
                r = routes[r_idx]
                load = route_loads.get(r.id, 0.0)
                max_shipment = r.max_shipment or 5000.0
                vec[idx] = np.clip(load / max(1.0, max_shipment), 0.0, 5.0)
                idx += 1
            else:
                idx += 1

        # 9. Route Transit Ticks (normalized by 10.0)
        for r_idx in range(self.max_routes):
            if r_idx < len(routes):
                r = routes[r_idx]
                vec[idx] = np.clip(float(r.transit_ticks) / 10.0, 0.1, 1.0)
                idx += 1
            else:
                idx += 1

        # 10. Active Crisis Indicators (5 binary flags)
        events = snapshot.events or []
        active_types = {e.type for e in events if getattr(e, "status", "ACTIVE") == "ACTIVE"}
        vec[idx] = 1.0 if any("spike" in t or "anomaly" in t for t in active_types) else 0.0
        vec[idx + 1] = 1.0 if any("disruption" in t or "route" in t for t in active_types) else 0.0
        vec[idx + 2] = 1.0 if any("depot" in t or "constraint" in t for t in active_types) else 0.0
        vec[idx + 3] = 1.0 if any("outage" in t or "station" in t for t in active_types) else 0.0
        vec[idx + 4] = 1.0 if any("delay" in t or "shortfall" in t for t in active_types) else 0.0
        idx += 5

        # 11. Time & Tick Indicators
        tick = snapshot.tick or 0
        vec[idx] = float((tick % 96) / 96.0)  # 15-min ticks in 24 hours
        vec[idx + 1] = min(1.0, float(tick / 500.0))
        idx += 2

        return vec

    def to_feature_dict(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], Any] | None = None,
    ) -> dict[str, Any]:
        """Human-readable dictionary summary of the state vector for inspection and UI."""
        vec = self.extract(snapshot, forecasts)
        return {
            "tick": snapshot.tick,
            "dimension": len(vec),
            "depots_count": len(snapshot.depots),
            "stations_count": len(snapshot.stations),
            "routes_count": len(snapshot.routes),
            "in_transit_count": len(snapshot.in_transit),
            "active_events": [e.type for e in snapshot.events if getattr(e, "status", "ACTIVE") == "ACTIVE"],
            "raw_vector_preview": [round(float(v), 3) for v in vec[:15]],
        }
