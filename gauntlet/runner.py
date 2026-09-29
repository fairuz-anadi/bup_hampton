"""
FuelGuard Policy Gauntlet Replay Runner (gauntlet/runner.py)
Executes deterministic simulation replays comparing Candidate (lp-v2)
against Baseline (greedy-v1) and No-Op across standardized crisis scenarios.
"""

import os
import yaml
from copy import deepcopy
from typing import Dict, List, Any, Tuple
from backend.app.contracts import (
    NetworkSnapshot,
    Depot,
    Station,
    Route,
    SupplyArrival,
    InTransitLeg,
    SimulatorEvent,
    FuelType,
    RouteStatus,
    StationStatus,
    DepotStatus,
    SupplyStatus,
)
from backend.app.intel import IntelligenceService


def create_initial_network() -> NetworkSnapshot:
    """Builds standard initial network per §8 of simulator interaction guide."""
    depots = {
        "depot-gazipur": Depot(
            id="depot-gazipur", region="region-dhaka",
            capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            dispatch_capacity_per_tick=12000, status=DepotStatus.OPEN,
        ),
        "depot-patiya": Depot(
            id="depot-patiya", region="region-chattogram",
            capacity={"DIESEL": 85000, "PETROL": 65000, "OCTANE": 40000},
            inventory={"DIESEL": 55000, "PETROL": 42000, "OCTANE": 24000},
            dispatch_capacity_per_tick=11000, status=DepotStatus.OPEN,
        ),
    }

    stations = {
        "station-mirpur": Station(
            id="station-mirpur", region="region-dhaka", demand_profile="urban_high",
            capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            inventory={"DIESEL": 9000, "PETROL": 9000, "OCTANE": 5000},
            demand_multiplier=1.0, status=StationStatus.OPEN,
        ),
        "station-tongi": Station(
            id="station-tongi", region="region-dhaka", demand_profile="industrial",
            capacity={"DIESEL": 18000, "PETROL": 9000, "OCTANE": 6000},
            inventory={"DIESEL": 11000, "PETROL": 6000, "OCTANE": 3500},
            demand_multiplier=1.0, status=StationStatus.OPEN,
        ),
        "station-karnaphuli": Station(
            id="station-karnaphuli", region="region-chattogram", demand_profile="highway",
            capacity={"DIESEL": 14000, "PETROL": 15000, "OCTANE": 9000},
            inventory={"DIESEL": 8500, "PETROL": 9500, "OCTANE": 5200},
            demand_multiplier=1.0, status=StationStatus.OPEN,
        ),
        "station-coxsbazar": Station(
            id="station-coxsbazar", region="region-chattogram", demand_profile="regional",
            capacity={"DIESEL": 12000, "PETROL": 12000, "OCTANE": 7000},
            inventory={"DIESEL": 7500, "PETROL": 7500, "OCTANE": 4200},
            demand_multiplier=1.0, status=StationStatus.OPEN,
        ),
    }

    routes = {
        "route-gazipur-mirpur": Route(id="route-gazipur-mirpur", depot_id="depot-gazipur", station_id="station-mirpur", transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE),
        "route-gazipur-tongi": Route(id="route-gazipur-tongi", depot_id="depot-gazipur", station_id="station-tongi", transit_ticks=2, max_shipment=6500, status=RouteStatus.AVAILABLE),
        "route-patiya-karnaphuli": Route(id="route-patiya-karnaphuli", depot_id="depot-patiya", station_id="station-karnaphuli", transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE),
        "route-patiya-coxsbazar": Route(id="route-patiya-coxsbazar", depot_id="depot-patiya", station_id="station-coxsbazar", transit_ticks=3, max_shipment=6000, status=RouteStatus.AVAILABLE),
        "route-gazipur-karnaphuli": Route(id="route-gazipur-karnaphuli", depot_id="depot-gazipur", station_id="station-karnaphuli", transit_ticks=4, max_shipment=5000, status=RouteStatus.AVAILABLE),
        "route-patiya-mirpur": Route(id="route-patiya-mirpur", depot_id="depot-patiya", station_id="station-mirpur", transit_ticks=4, max_shipment=5000, status=RouteStatus.AVAILABLE),
    }

    # Supply arrivals (§8.7): 22-arrival schedule
    supply_arrivals = [
        SupplyArrival(id="sup-1", depot_id="depot-gazipur", fuel=FuelType.DIESEL, planned_tick=12, arrival_tick=12, quantity=15000, status=SupplyStatus.SCHEDULED),
        SupplyArrival(id="sup-2", depot_id="depot-gazipur", fuel=FuelType.PETROL, planned_tick=14, arrival_tick=14, quantity=15000, status=SupplyStatus.SCHEDULED),
        SupplyArrival(id="sup-3", depot_id="depot-patiya", fuel=FuelType.DIESEL, planned_tick=16, arrival_tick=16, quantity=14000, status=SupplyStatus.SCHEDULED),
        SupplyArrival(id="sup-4", depot_id="depot-patiya", fuel=FuelType.PETROL, planned_tick=18, arrival_tick=18, quantity=14000, status=SupplyStatus.SCHEDULED),
    ]

    return NetworkSnapshot(
        tick=0, sim_time="Day 1, 00:00", status="RUNNING",
        depots=depots, stations=stations, routes=routes,
        supply_arrivals=supply_arrivals, in_transit=[]
    )


class PolicyGauntletRunner:
    def __init__(self):
        self.intel = IntelligenceService()

    def load_scenario(self, scenario_path: str) -> Dict[str, Any]:
        with open(scenario_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def run_simulation(
        self, scenario: Dict[str, Any], policy_mode: str
    ) -> Dict[str, Any]:
        """
        Executes a deterministic multi-tick simulation under policy_mode:
        - 'noop': Zero allocations
        - 'greedy-v1': Greedy baseline
        - 'lp-v2': Full candidate optimizer
        """
        snapshot = create_initial_network()
        events_spec = scenario.get("events", [])
        duration_ticks = scenario.get("duration_ticks", 96)

        total_demand = 0.0
        total_unmet = 0.0
        allocation_failures = 0
        fallback_activations = 0

        in_transit_ledger: List[InTransitLeg] = []

        for current_tick in range(duration_ticks):
            snapshot.tick = current_tick

            # 1. Update Active Crisis Events
            active_events = []
            for ev in events_spec:
                start = ev.get("start_tick", 0)
                dur = ev.get("duration_ticks", 0)
                if start <= current_tick < (start + dur):
                    active_events.append(SimulatorEvent(
                        type=ev["type"],
                        start_tick=start,
                        duration_ticks=dur,
                        parameters=ev.get("parameters", {}),
                    ))

            snapshot.active_events = active_events

            # Apply event mutations
            # Reset defaults
            for s in snapshot.station_map.values():
                s.demand_multiplier = 1.0
                s.status = StationStatus.OPEN
            for r in snapshot.route_map.values():
                r.status = RouteStatus.AVAILABLE
            for d in snapshot.depot_map.values():
                d.status = DepotStatus.OPEN
                d.dispatch_capacity_per_tick = 12000.0 if "gazipur" in d.id else 11000.0

            for ev in active_events:
                if ev.type == "demand_spike":
                    target_regions = ev.parameters.get("region_ids", [])
                    mult = ev.parameters.get("multiplier", 1.8)
                    for s in snapshot.station_map.values():
                        if s.region in target_regions:
                            s.demand_multiplier = mult

                elif ev.type == "route_disruption":
                    disrupted_routes = ev.parameters.get("route_ids", [])
                    for r_id in disrupted_routes:
                        if r_id in snapshot.route_map:
                            snapshot.route_map[r_id].status = RouteStatus.DISRUPTED

                elif ev.type == "station_outage":
                    out_stations = ev.parameters.get("station_ids", [])
                    for s_id in out_stations:
                        if s_id in snapshot.station_map:
                            snapshot.station_map[s_id].status = StationStatus.OUTAGE

                elif ev.type == "depot_constraint":
                    depots_constrained = ev.parameters.get("depot_ids", [])
                    cap = ev.parameters.get("max_dispatch_per_tick", 3000.0)
                    for d_id in depots_constrained:
                        if d_id in snapshot.depot_map:
                            snapshot.depot_map[d_id].status = DepotStatus.CONSTRAINED
                            snapshot.depot_map[d_id].dispatch_capacity_per_tick = cap

                elif ev.type == "shipment_delay":
                    d_id = ev.parameters.get("depot_id")
                    fuel_name = ev.parameters.get("fuel")
                    delay = ev.parameters.get("delay_ticks", 8)
                    for arr in snapshot.supply_arrivals:
                        if arr.depot_id == d_id and arr.fuel.value == fuel_name:
                            arr.status = SupplyStatus.DELAYED
                            arr.arrival_tick = arr.planned_tick + delay

            # 2. Arrive scheduled supply
            for arr in snapshot.supply_arrivals:
                if arr.arrival_tick == current_tick:
                    depot = snapshot.depot_map.get(arr.depot_id)
                    if depot:
                        curr = depot.inventory.get(arr.fuel.value, 0.0)
                        depot.inventory[arr.fuel.value] = curr + arr.quantity

            # 3. Arrive in-transit shipments
            remaining_in_transit = []
            for leg in in_transit_ledger:
                if leg.arrival_tick <= current_tick:
                    station = snapshot.station_map.get(leg.station_id)
                    if station:
                        curr = station.inventory.get(leg.fuel.value, 0.0)
                        cap = station.capacity.get(leg.fuel.value, 15000.0)
                        station.inventory[leg.fuel.value] = min(cap, curr + leg.quantity)
                else:
                    remaining_in_transit.append(leg)
            in_transit_ledger = remaining_in_transit
            snapshot.in_transit = in_transit_ledger

            # 4. Consume Station Demand
            for s_id, station in snapshot.station_map.items():
                if station.status == StationStatus.OUTAGE:
                    continue  # Station closed, no sales

                for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                    base_rate = self.intel.detector.forecaster.compute_base_demand_for_tick(
                        station.demand_profile, fuel.value, current_tick, station.demand_multiplier
                    )
                    total_demand += base_rate
                    curr_inv = station.inventory.get(fuel.value, 0.0)
                    if curr_inv >= base_rate:
                        station.inventory[fuel.value] = curr_inv - base_rate
                    else:
                        unmet = base_rate - curr_inv
                        station.inventory[fuel.value] = 0.0
                        total_unmet += unmet

            # 5. Policy Decision
            if policy_mode != "noop":
                rec = self.intel.evaluate_and_recommend(snapshot)
                if rec.policy == "greedy-v1" and policy_mode == "lp-v2":
                    fallback_activations += 1

                chosen_legs = rec.legs if policy_mode == "lp-v2" else rec.twin_futures[1].legs
                for leg in chosen_legs:
                    route = snapshot.route_map.get(leg.route_id)
                    depot = snapshot.depot_map.get(leg.depot_id)
                    station = snapshot.station_map.get(leg.station_id)

                    # Validation checks (simulating simulator /v1/allocations)
                    if not route or route.status != RouteStatus.AVAILABLE:
                        allocation_failures += 1
                        continue
                    if not station or station.status == StationStatus.OUTAGE:
                        allocation_failures += 1
                        continue
                    if not depot or depot.inventory.get(leg.fuel.value, 0.0) < leg.quantity_liters:
                        allocation_failures += 1
                        continue

                    # Deduct from depot
                    depot.inventory[leg.fuel.value] -= leg.quantity_liters
                    in_transit_ledger.append(InTransitLeg(
                        route_id=leg.route_id,
                        depot_id=leg.depot_id,
                        station_id=leg.station_id,
                        fuel=leg.fuel,
                        quantity=leg.quantity_liters,
                        depart_tick=current_tick,
                        arrival_tick=current_tick + leg.transit_ticks,
                    ))

        service_level = (1.0 - (total_unmet / max(total_demand, 1.0))) if total_demand > 0 else 1.0

        return {
            "policy": policy_mode,
            "duration_ticks": duration_ticks,
            "total_demand_liters": round(total_demand, 1),
            "unmet_demand_liters": round(total_unmet, 1),
            "service_level": round(service_level, 4),
            "allocation_failures": allocation_failures,
            "fallback_activations": fallback_activations,
        }

    def evaluate_scenario(self, scenario_path: str) -> Dict[str, Any]:
        scenario = self.load_scenario(scenario_path)

        res_noop = self.run_simulation(scenario, "noop")
        res_baseline = self.run_simulation(scenario, "greedy-v1")
        res_candidate = self.run_simulation(scenario, "lp-v2")

        # Acceptance Gate Criteria (§10):
        # 1. Service level drop <= 0.5 pp (0.005)
        # 2. Unmet demand increase <= 2% (0.02)
        # 3. Allocation failures <= baseline failures
        sl_delta = res_candidate["service_level"] - res_baseline["service_level"]
        unmet_delta_pct = (
            (res_candidate["unmet_demand_liters"] - res_baseline["unmet_demand_liters"])
            / max(res_baseline["unmet_demand_liters"], 1.0)
        )
        failures_delta = res_candidate["allocation_failures"] - res_baseline["allocation_failures"]

        passed = (
            sl_delta >= -0.005
            and unmet_delta_pct <= 0.02
            and failures_delta <= 0
        )

        return {
            "scenario_id": scenario.get("id"),
            "scenario_name": scenario.get("name"),
            "passed": passed,
            "metrics": {
                "noop": res_noop,
                "baseline": res_baseline,
                "candidate": res_candidate,
            },
            "deltas": {
                "service_level_delta": round(sl_delta, 4),
                "unmet_pct_change": round(unmet_delta_pct * 100, 2),
                "failures_delta": failures_delta,
            }
        }
