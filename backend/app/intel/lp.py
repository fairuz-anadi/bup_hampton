"""
FuelGuard Linear Programming Optimizer (lp-v2)
Formulated as a linear program and solved with SciPy HiGHS.
Minimizes weighted projected shortages while balancing route transit times
and tank overfill penalties. Supports crisis containment mode.
"""

from typing import List, Dict, Tuple, Optional
import numpy as np
from scipy.optimize import linprog

from backend.app.contracts import (
    NetworkSnapshot,
    StockoutRisk,
    AllocationLeg,
    StationStatus,
    RouteStatus,
    DepotStatus,
    FuelType,
)
from backend.app.intel.greedy import GreedyPolicy


class LPOptimizer:
    def __init__(
        self,
        depot_reserve_pct: float = 0.15,
        lambda_transit: float = 0.05,
        mu_overfill: float = 50.0,
    ):
        self.depot_reserve_pct = depot_reserve_pct
        self.lambda_transit = lambda_transit
        self.mu_overfill = mu_overfill
        self.greedy_fallback = GreedyPolicy(depot_reserve_pct=depot_reserve_pct)

    def optimize_allocations(
        self,
        snapshot: NetworkSnapshot,
        risks: List[StockoutRisk],
        containment_mode: bool = False,
    ) -> Tuple[List[AllocationLeg], bool, str]:
        """
        Executes HiGHS LP solver. Returns (legs, is_fallback, policy_name).
        Falls back seamlessly to greedy-v1 on infeasibility or solver exception.
        """
        try:
            legs = self._solve_lp(snapshot, risks, containment_mode)
            return legs, False, "lp-v2" if not containment_mode else "containment"
        except Exception:
            # Fallback to greedy-v1
            fallback_legs = self.greedy_fallback.plan_allocations(snapshot, risks)
            return fallback_legs, True, "greedy-v1"

    def _solve_lp(
        self,
        snapshot: NetworkSnapshot,
        risks: List[StockoutRisk],
        containment_mode: bool,
    ) -> List[AllocationLeg]:
        routes = list(snapshot.route_map.values())
        fuels = [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]
        stations = list(snapshot.station_map.values())
        depots = list(snapshot.depot_map.values())

        # Decision variables:
        # 1. x[r, f]: allocation in liters on route r for fuel f (len = num_routes * 3)
        # 2. short[s, f]: shortage slack variable (len = num_stations * 3)
        # 3. over[s, f]: overfill slack variable (len = num_stations * 3)

        num_routes = len(routes)
        num_fuels = len(fuels)
        num_stations = len(stations)
        num_depots = len(depots)

        n_x = num_routes * num_fuels
        n_short = num_stations * num_fuels
        n_over = num_stations * num_fuels
        total_vars = n_x + n_short + n_over

        def x_idx(r_i: int, f_i: int) -> int:
            return r_i * num_fuels + f_i

        def short_idx(s_i: int, f_i: int) -> int:
            return n_x + s_i * num_fuels + f_i

        def over_idx(s_i: int, f_i: int) -> int:
            return n_x + n_short + s_i * num_fuels + f_i

        # Objective vector c:
        c = np.zeros(total_vars)

        # Transit cost on x[r, f]
        for r_i, route in enumerate(routes):
            for f_i in range(num_fuels):
                c[x_idx(r_i, f_i)] = self.lambda_transit * route.transit_ticks

        # Shortage penalty on short[s, f]
        for s_i, station in enumerate(stations):
            for f_i, fuel in enumerate(fuels):
                # Weight by fuel importance and crisis containment
                base_w = 100.0 if fuel == FuelType.DIESEL else 80.0
                if containment_mode:
                    # In containment mode, single-route stations (Tongi, Cox's Bazar) receive boosted priority
                    if station.id in ["station-tongi", "station-coxsbazar"]:
                        base_w *= 2.5
                c[short_idx(s_i, f_i)] = base_w

        # Overfill penalty on over[s, f]
        for s_i in range(num_stations):
            for f_i in range(num_fuels):
                c[over_idx(s_i, f_i)] = self.mu_overfill

        # Bounds: all variables >= 0
        bounds = [(0, None) for _ in range(total_vars)]

        # Specific upper bounds on x[r, f]
        for r_i, route in enumerate(routes):
            st = snapshot.station_map.get(route.station_id)
            station_outage = (st and st.status == StationStatus.OUTAGE)
            route_disrupted = (route.status != RouteStatus.AVAILABLE)

            for f_i in range(num_fuels):
                var_i = x_idx(r_i, f_i)
                if route_disrupted or station_outage:
                    bounds[var_i] = (0, 0)
                else:
                    # Allow up to route.max_shipment
                    bounds[var_i] = (0, route.max_shipment)

        # Constraints matrix A_ub * var <= b_ub
        A_ub = []
        b_ub = []

        # Risk mapping for station shortages
        shortage_map: Dict[Tuple[str, str], float] = {}
        for r in risks:
            shortage_map[(r.station_id, r.fuel.value)] = r.projected_shortage_liters

        # In-transit sum per station/fuel
        in_transit_map: Dict[Tuple[str, str], float] = {}
        for l in snapshot.in_transit:
            key = (l.station_id, l.fuel.value)
            in_transit_map[key] = in_transit_map.get(key, 0.0) + l.quantity

        # 1. Shortage constraint:
        # short[s, f] >= Need - inTransit - sum_r(x[r, f])
        # <=> -short[s, f] - sum_r(x[r, f]) <= -(Need - inTransit)
        for s_i, station in enumerate(stations):
            for f_i, fuel in enumerate(fuels):
                row = np.zeros(total_vars)
                need = shortage_map.get((station.id, fuel.value), 0.0)
                intrans = in_transit_map.get((station.id, fuel.value), 0.0)
                net_need = max(0.0, need - intrans)

                row[short_idx(s_i, f_i)] = -1.0
                for r_i, route in enumerate(routes):
                    if route.station_id == station.id:
                        row[x_idx(r_i, f_i)] = -1.0

                A_ub.append(row)
                b_ub.append(-net_need)

        # 2. Tank capacity & headroom constraint:
        # inv[s, f] + inTransit[s, f] + sum_r(x[r, f]) <= cap[s, f] + over[s, f]
        # <=> sum_r(x[r, f]) - over[s, f] <= cap[s, f] - inv[s, f] - inTransit[s, f]
        for s_i, station in enumerate(stations):
            for f_i, fuel in enumerate(fuels):
                row = np.zeros(total_vars)
                cap = station.capacity.get(fuel.value, 15000.0)
                inv = station.inventory.get(fuel.value, 0.0)
                intrans = in_transit_map.get((station.id, fuel.value), 0.0)
                headroom = max(0.0, cap - (inv + intrans))

                for r_i, route in enumerate(routes):
                    if route.station_id == station.id:
                        row[x_idx(r_i, f_i)] = 1.0
                row[over_idx(s_i, f_i)] = -1.0

                A_ub.append(row)
                b_ub.append(headroom)

        # 3. Depot inventory & reserve constraint:
        # sum_{r: d->*, f} x[r, f] <= inv[d, f] - reserve[d, f]
        for d_i, depot in enumerate(depots):
            if depot.status == DepotStatus.CLOSED:
                for f_i in range(num_fuels):
                    for r_i, route in enumerate(routes):
                        if route.depot_id == depot.id:
                            bounds[x_idx(r_i, f_i)] = (0, 0)
                continue

            for f_i, fuel in enumerate(fuels):
                row = np.zeros(total_vars)
                inv = depot.inventory.get(fuel.value, 0.0)
                cap = depot.capacity.get(fuel.value, 100000.0)
                res = cap * self.depot_reserve_pct
                avail_stock = max(0.0, inv - res)

                for r_i, route in enumerate(routes):
                    if route.depot_id == depot.id:
                        row[x_idx(r_i, f_i)] = 1.0

                A_ub.append(row)
                b_ub.append(avail_stock)

        # 4. Depot dispatch capacity per tick constraint:
        # sum_{r: d->*, all f} x[r, f] <= dispatchCap[d]
        for d_i, depot in enumerate(depots):
            row = np.zeros(total_vars)
            cap_tick = depot.dispatch_capacity_per_tick if depot.status != DepotStatus.CLOSED else 0.0

            for r_i, route in enumerate(routes):
                if route.depot_id == depot.id:
                    for f_i in range(num_fuels):
                        row[x_idx(r_i, f_i)] = 1.0

            A_ub.append(row)
            b_ub.append(cap_tick)

        # Solve via SciPy HiGHS
        res = linprog(
            c,
            A_ub=np.array(A_ub),
            b_ub=np.array(b_ub),
            bounds=bounds,
            method="highs",
        )

        if not res.success:
            raise RuntimeError(f"HiGHS solver failed: {res.message}")

        # Extract allocations and split into legs <= max_shipment
        legs: List[AllocationLeg] = []
        for r_i, route in enumerate(routes):
            for f_i, fuel in enumerate(fuels):
                qty = res.x[x_idx(r_i, f_i)]
                if qty >= 100.0:
                    # Clean truck sizing (multiples of 50)
                    qty = int(qty // 50) * 50.0
                    if qty >= 100.0:
                        legs.append(AllocationLeg(
                            route_id=route.id,
                            depot_id=route.depot_id,
                            station_id=route.station_id,
                            fuel=fuel,
                            quantity_liters=qty,
                            transit_ticks=route.transit_ticks,
                            max_shipment=route.max_shipment,
                        ))

        return legs
