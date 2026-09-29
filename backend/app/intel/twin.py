"""
FuelGuard Decision Twin (backend/app/intel/twin.py)
Projects side-by-side futures for candidate decisions (No-op, Greedy-v1, LP-v2),
computes counterfactual unmet demand avoided, and implements the self-checking
verification loop against official simulator outcomes.
"""

from typing import Any

from backend.app.contracts import (
    AllocationLeg,
    ForecastResponse,
    FuelType,
    NetworkSnapshot,
    StationFuture,
    TwinFuture,
)


class DecisionTwin:
    def __init__(self, horizon_ticks: int = 24):
        self.horizon_ticks = horizon_ticks
        self.recorded_projections: dict[str, TwinFuture] = {}

    def project_candidate_future(
        self,
        candidate_id: str,
        name: str,
        legs: list[AllocationLeg],
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], ForecastResponse],
        notes: str = "",
    ) -> TwinFuture:
        """
        Projects a decision candidate forward over the horizon, simulating
        inventory levels, in-transit arrivals, and unmet customer demand.
        """
        current_tick = snapshot.tick
        horizon = self.horizon_ticks

        # Initialize station inventories
        sim_inv: dict[tuple[str, str], float] = {}
        min_inv: dict[tuple[str, str], float] = {}
        unmet_by_station: dict[tuple[str, str], float] = {}

        for s_id, station in snapshot.station_map.items():
            for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                init_val = station.inventory.get(fuel.value, 0.0)
                sim_inv[(s_id, fuel.value)] = init_val
                min_inv[(s_id, fuel.value)] = init_val
                unmet_by_station[(s_id, fuel.value)] = 0.0

        # Existing in-transit shipments
        arrivals_schedule: dict[tuple[str, str, int], float] = {}
        for leg in snapshot.in_transit:
            k = (leg.station_id, leg.fuel.value, leg.arrival_tick)
            arrivals_schedule[k] = arrivals_schedule.get(k, 0.0) + leg.quantity

        # Proposed candidate shipments
        for proposed_leg in legs:
            arr_tick = current_tick + proposed_leg.transit_ticks
            k = (proposed_leg.station_id, proposed_leg.fuel.value, arr_tick)
            arrivals_schedule[k] = arrivals_schedule.get(k, 0.0) + proposed_leg.quantity_liters

        # Step forward tick by tick
        total_network_unmet = 0.0

        for step in range(1, horizon + 1):
            t = current_tick + step
            for s_id, station in snapshot.station_map.items():
                for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                    # 1. Fuel arriving at this tick
                    inflow = arrivals_schedule.get((s_id, fuel.value, t), 0.0)
                    sim_inv[(s_id, fuel.value)] += inflow

                    # Cap at tank capacity
                    cap = station.capacity.get(fuel.value, 15000.0)
                    if sim_inv[(s_id, fuel.value)] > cap:
                        sim_inv[(s_id, fuel.value)] = cap

                    # 2. Demand consumed at this tick
                    fc = forecasts.get((s_id, fuel.value))
                    demand = 0.0
                    if fc and len(fc.bands) >= step:
                        demand = fc.bands[step - 1].mean
                    else:
                        demand = 80.0  # sensible fallback

                    level = sim_inv[(s_id, fuel.value)] - demand
                    if level < 0.0:
                        short = abs(level)
                        unmet_by_station[(s_id, fuel.value)] += short
                        total_network_unmet += short
                        sim_inv[(s_id, fuel.value)] = 0.0
                    else:
                        sim_inv[(s_id, fuel.value)] = level

                    if sim_inv[(s_id, fuel.value)] < min_inv[(s_id, fuel.value)]:
                        min_inv[(s_id, fuel.value)] = sim_inv[(s_id, fuel.value)]

        # Compile station outcomes
        station_outcomes: list[StationFuture] = []
        for s_id in snapshot.station_map.keys():
            for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                station_outcomes.append(StationFuture(
                    station_id=s_id,
                    fuel=fuel,
                    unmet_liters=round(unmet_by_station[(s_id, fuel.value)], 1),
                    min_inventory=round(min_inv[(s_id, fuel.value)], 1),
                    final_inventory=round(sim_inv[(s_id, fuel.value)], 1),
                ))

        future = TwinFuture(
            candidate_id=candidate_id,
            name=name,
            legs=legs,
            network_unmet_liters=round(total_network_unmet, 1),
            station_outcomes=station_outcomes,
            notes=notes,
        )
        return future

    def project_three_futures(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], ForecastResponse],
        greedy_legs: list[AllocationLeg],
        lp_legs: list[AllocationLeg],
    ) -> list[TwinFuture]:
        """
        Projects three futures side by side:
        1. noop: Do nothing (zero allocations)
        2. greedy-v1: Deterministic rule-based baseline
        3. lp-v2: Constrained linear program allocation
        """
        f_noop = self.project_candidate_future(
            candidate_id="noop",
            name="No-Op (Do Nothing)",
            legs=[],
            snapshot=snapshot,
            forecasts=forecasts,
            notes="Counterfactual baseline without intervention",
        )

        f_greedy = self.project_candidate_future(
            candidate_id="greedy-v1",
            name="Greedy-v1 Baseline",
            legs=greedy_legs,
            snapshot=snapshot,
            forecasts=forecasts,
            notes="Heuristic greedy dispatch",
        )

        f_lp = self.project_candidate_future(
            candidate_id="lp-v2",
            name="LP-v2 Optimized Plan",
            legs=lp_legs,
            snapshot=snapshot,
            forecasts=forecasts,
            notes="Linear program optimized over multi-depot network",
        )

        return [f_noop, f_greedy, f_lp]

    def record_prediction(self, decision_id: str, future: TwinFuture):
        self.recorded_projections[decision_id] = future

    def verify_twin_outcome(
        self,
        decision_id: str,
        actual_snapshot: NetworkSnapshot,
        actual_unmet_liters: float,
    ) -> dict[str, Any]:
        """
        Self-checking Twin verification loop (§5):
        Evaluates predicted network unmet liters against actual simulator outcome.
        Exports error and confidence adjustment.
        """
        predicted_future = self.recorded_projections.get(decision_id)
        if not predicted_future:
            return {"verified": False, "reason": "No recorded projection for this decision_id"}

        predicted_l = predicted_future.network_unmet_liters
        actual_l = actual_unmet_liters
        error_l = abs(predicted_l - actual_l)

        # Confidence penalty if error is high relative to scale
        confidence_factor = max(0.60, 1.0 - (error_l / max(actual_l, 1000.0) * 0.2))

        return {
            "verified": True,
            "decision_id": decision_id,
            "predicted_unmet_liters": round(predicted_l, 1),
            "actual_unmet_liters": round(actual_l, 1),
            "error_liters": round(error_l, 1),
            "confidence_factor": round(confidence_factor, 3),
        }
