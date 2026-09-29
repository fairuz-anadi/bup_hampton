"""
FuelGuard Greedy Fallback Policy (greedy-v1)
Deterministic, dependency-free rule-based dispatcher.
Acts as P0 fallback and counterfactual benchmark in the Policy Gauntlet.
"""

from __future__ import annotations

from app.contracts import (
    AllocationLeg,
    DepotStatus,
    FuelType,
    NetworkSnapshot,
    RouteStatus,
    StationStatus,
    StockoutRisk,
)


class GreedyPolicy:
    def __init__(self, horizon_ticks: int = 24, depot_reserve_pct: float = 0.15):
        self.horizon_ticks = horizon_ticks
        self.depot_reserve_pct = depot_reserve_pct

    def plan_allocations(
        self,
        snapshot: NetworkSnapshot,
        risks: list[StockoutRisk],
    ) -> list[AllocationLeg]:
        legs: list[AllocationLeg] = []

        # Find disrupted routes (current or scheduled for next tick)
        disrupted_route_ids = {
            r.id for r in snapshot.route_map.values() if r.status != RouteStatus.AVAILABLE
        }
        for ev in snapshot.events:
            if ev.type == "route_disruption" and ev.status != "RESOLVED":
                if ev.start_tick <= snapshot.tick + 1 and ev.end_tick > snapshot.tick:
                    r_ids = ev.parameters.get("route_ids") or list(snapshot.route_map.keys())
                    disrupted_route_ids.update(r_ids)

        # Track remaining depot capacities and available dispatch in this decision step
        depot_stock: dict[tuple[str, str], float] = {}
        depot_reserves: dict[tuple[str, str], float] = {}
        dispatch_left: dict[str, float] = {}

        for d_id, depot in snapshot.depot_map.items():
            already_dispatched = snapshot.dispatched_this_tick.get(d_id, 0.0)
            if depot.status == DepotStatus.CLOSED:
                dispatch_left[d_id] = 0.0
            else:
                dispatch_left[d_id] = max(0.0, depot.dispatch_capacity_per_tick - already_dispatched)

            for fuel_str, amt in depot.inventory.items():
                cap = depot.capacity.get(fuel_str, 100000.0)
                res = cap * self.depot_reserve_pct
                depot_reserves[(d_id, fuel_str)] = res
                depot_stock[(d_id, fuel_str)] = max(0.0, amt - res)

        # Calculate current tank headroom including in-transit
        station_headroom: dict[tuple[str, str], float] = {}
        for s_id, station in snapshot.station_map.items():
            for f in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                cap = station.capacity.get(f.value, 15000.0)
                inv = station.inventory.get(f.value, 0.0)
                # Count in-transit already on the way
                in_transit = sum(
                    leg.quantity for leg in snapshot.in_transit
                    if leg.station_id == s_id and leg.fuel == f
                )
                station_headroom[(s_id, f.value)] = max(0.0, cap - (inv + in_transit))

        # Sort risks: critical first, soonest stockout first
        sorted_risks = sorted(
            [r for r in risks if r.projected_shortage_liters > 0 or r.time_to_stockout_ticks <= self.horizon_ticks],
            key=lambda r: (r.time_to_stockout_ticks, -r.projected_shortage_liters)
        )

        for risk in sorted_risks:
            s_id = risk.station_id
            fuel = risk.fuel
            station = snapshot.station_map.get(s_id)

            # Skip stations in outage
            if not station or station.status != StationStatus.OPEN:
                continue

            needed = risk.projected_shortage_liters
            if needed <= 0:
                continue

            headroom = station_headroom.get((s_id, fuel.value), 0.0)
            if headroom <= 100.0:
                continue

            # Find matching available routes connecting any depot to this station (excluding scheduled disruptions)
            matching_routes = [
                r for r in snapshot.route_map.values()
                if r.station_id == s_id and r.id not in disrupted_route_ids
            ]
            # Prefer fastest route (least transit_ticks)
            matching_routes.sort(key=lambda r: r.transit_ticks)

            for route in matching_routes:
                d_id = route.depot_id
                avail_stock = depot_stock.get((d_id, fuel.value), 0.0)
                avail_dispatch = dispatch_left.get(d_id, 0.0)

                if avail_stock <= 100.0 or avail_dispatch <= 100.0:
                    continue

                # Quantity = min(needed, headroom, route.max_shipment, avail_stock, avail_dispatch)
                qty = min(needed, headroom, route.max_shipment, avail_stock, avail_dispatch)
                # Round down to nearest 50 liters for clean truck dispatch
                qty = int(qty // 50) * 50.0

                if qty >= 200.0:
                    legs.append(AllocationLeg(
                        route_id=route.id,
                        depot_id=d_id,
                        station_id=s_id,
                        fuel=fuel,
                        quantity_liters=qty,
                        transit_ticks=route.transit_ticks,
                        max_shipment=route.max_shipment,
                    ))

                    # Deduct
                    depot_stock[(d_id, fuel.value)] -= qty
                    dispatch_left[d_id] -= qty
                    station_headroom[(s_id, fuel.value)] -= qty
                    needed -= qty
                    if needed <= 0 or station_headroom[(s_id, fuel.value)] <= 100.0:
                        break

        return legs
