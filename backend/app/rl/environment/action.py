"""FuelGuard RL Action Space Representation and Deterministic Validator.

Translates discrete/multi-discrete agent outputs into valid AllocationLeg contracts,
and strictly validates allocations against route disruptions, depot reserve floors,
dispatch limits, and station tank headroom before any action can reach the writer.
"""
from __future__ import annotations

from typing import Any
import numpy as np

from app.contracts import (
    FUELS,
    AllocationLeg,
    FuelType,
    NetworkSnapshot,
    RouteStatus,
    StationStatus,
)
from app.decisions.gate import Guardrails

QUANTITY_LEVELS = [0.0, 1000.0, 2500.0, 4000.0, 5000.0]


class RLActionSpace:
    """Configurable discrete action space for fuel allocation and dispatch."""

    def __init__(self, quantity_levels: list[float] | None = None):
        self.quantity_levels = quantity_levels or QUANTITY_LEVELS
        # Non-zero dispatch quantities
        self.dispatch_quantities = [q for q in self.quantity_levels if q > 0.0]

    def get_action_combinations(
        self, snapshot: NetworkSnapshot
    ) -> list[dict[str, Any]]:
        """Enumerates possible (depot, station, fuel, quantity, route) tuples."""
        depots = sorted(snapshot.depots, key=lambda d: d.id)
        stations = sorted(snapshot.stations, key=lambda s: s.id)
        routes = snapshot.route_map

        # Action 0 is always NO-OP (ship nothing)
        combos: list[dict[str, Any]] = [{"type": "noop", "quantity": 0.0}]

        for depot in depots:
            for station in stations:
                # Find available route connecting this depot to station
                connecting_routes = [
                    r
                    for r in routes.values()
                    if (r.source_depot_id == depot.id or r.depot_id == depot.id)
                    and (r.destination_station_id == station.id or r.station_id == station.id)
                ]
                if not connecting_routes:
                    continue
                # Primary route
                route = connecting_routes[0]

                for fuel in FUELS:
                    for qty in self.dispatch_quantities:
                        combos.append(
                            {
                                "type": "dispatch",
                                "depot_id": depot.id,
                                "station_id": station.id,
                                "fuel_type": fuel,
                                "quantity": qty,
                                "route_id": route.id,
                                "transit_ticks": route.transit_ticks,
                            }
                        )
        return combos

    def num_actions(self, snapshot: NetworkSnapshot) -> int:
        return len(self.get_action_combinations(snapshot))

    def decode_action(
        self, action_idx: int, snapshot: NetworkSnapshot
    ) -> AllocationLeg | None:
        """Translates discrete action index into an AllocationLeg or None (no-op)."""
        combos = self.get_action_combinations(snapshot)
        if action_idx <= 0 or action_idx >= len(combos):
            return None

        chosen = combos[action_idx]
        if chosen["type"] == "noop":
            return None

        return AllocationLeg(
            route_id=chosen["route_id"],
            source_depot_id=chosen["depot_id"],
            station_id=chosen["station_id"],
            fuel_type=chosen["fuel_type"],
            quantity=float(chosen["quantity"]),
            transit_ticks=chosen.get("transit_ticks", 2),
        )


def validate_action(
    leg: AllocationLeg | None,
    snapshot: NetworkSnapshot,
    rails: Guardrails | None = None,
) -> tuple[bool, str | None, AllocationLeg | None]:
    """Strictly validates an RL allocation against physical and policy constraints.

    Invalid actions MUST be rejected before they can reach the allocation writer.
    Returns: (is_valid, rejection_reason, validated_leg)
    """
    if leg is None or leg.quantity <= 0:
        # Valid No-Op
        return True, None, None

    rails = rails or Guardrails()
    routes = snapshot.route_map
    stations = snapshot.station_map
    depots = snapshot.depot_map

    # 1. Route validation
    route = routes.get(leg.route_id)
    if route is None:
        return False, f"Route {leg.route_id} does not exist in network", None

    if route.status != RouteStatus.AVAILABLE:
        return False, f"Route {leg.route_id} is currently {route.status}", None

    # Check connection topology
    depot_id = leg.source_depot_id or route.depot_id
    station_id = leg.station_id or route.station_id
    if route.depot_id != depot_id or route.station_id != station_id:
        return False, f"Route {leg.route_id} connects {route.depot_id}->{route.station_id}, not {depot_id}->{station_id}", None

    # 2. Station validation
    station = stations.get(station_id)
    if station is None:
        return False, f"Station {station_id} does not exist", None

    if station.status != StationStatus.OPEN:
        return False, f"Station {station_id} is in {station.status} status", None

    # 3. Route capacity limit
    max_shipment = route.max_shipment or rails.max_auto_leg_litres
    if leg.quantity > max_shipment + 1e-6:
        return False, f"Quantity {leg.quantity:,.0f} L exceeds route maximum {max_shipment:,.0f} L", None

    # 4. Depot capacity & reserve constraints
    depot = depots.get(depot_id)
    if depot is None:
        return False, f"Depot {depot_id} does not exist", None

    depot_cap = depot.capacity.get(leg.fuel_type, 90000.0) or 90000.0
    depot_inv = depot.inventory.get(leg.fuel_type, 0.0)
    reserve_floor = rails.depot_reserve_fraction * depot_cap

    if (depot_inv - leg.quantity) < (reserve_floor - 1e-6):
        return False, f"Dispatch would draw {depot_id} {leg.fuel_type.value} inventory ({depot_inv:,.0f} L) below required 10% reserve ({reserve_floor:,.0f} L)", None

    # 5. Depot dispatch rate limit per tick
    committed = snapshot.dispatched_this_tick.get(depot_id, 0.0)
    max_dispatch = depot.dispatch_capacity_per_tick or 12000.0
    if committed + leg.quantity > max_dispatch + 1e-6:
        return False, f"Dispatch would exceed {depot_id} per-tick capacity ({committed + leg.quantity:,.0f} L > {max_dispatch:,.0f} L)", None

    # 6. Station tank headroom validation
    station_cap = station.capacity.get(leg.fuel_type, 25000.0) or 25000.0
    station_inv = station.inventory.get(leg.fuel_type, 0.0)
    in_transit_val = 0.0
    if snapshot.in_transit_totals and station_id in snapshot.in_transit_totals:
        in_transit_val = snapshot.in_transit_totals[station_id].get(leg.fuel_type, 0.0)

    # Overflow limit: inventory + in_transit + new shipment should not exceed 105% of capacity
    if station_inv + in_transit_val + leg.quantity > (station_cap * 1.05):
        return False, f"Station {station_id} tank would overflow ({station_inv + in_transit_val + leg.quantity:,.0f} L > {station_cap:,.0f} L)", None

    return True, None, leg
