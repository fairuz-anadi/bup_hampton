"""Tests for Decision Intelligence (Detection, Risk, LP, Greedy, Decision Twin)."""

import pytest
from backend.app.contracts import (
    NetworkSnapshot, Depot, Station, Route, FuelType,
    RouteStatus, StationStatus, DepotStatus, SupplyArrival, SupplyStatus
)
from backend.app.intel import (
    DetectionEngine, RiskEngine, LPOptimizer, GreedyPolicy, DecisionTwin, IntelligenceService
)


@pytest.fixture
def sample_snapshot():
    depots = {
        "depot-gazipur": Depot(
            id="depot-gazipur", region="region-dhaka",
            capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            dispatch_capacity_per_tick=12000, status=DepotStatus.OPEN
        ),
    }
    stations = {
        "station-mirpur": Station(
            id="station-mirpur", region="region-dhaka", demand_profile="urban_high",
            capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            inventory={"DIESEL": 1000, "PETROL": 800, "OCTANE": 600},  # Imminent stockout
            demand_multiplier=1.0, status=StationStatus.OPEN
        ),
        "station-tongi": Station(
            id="station-tongi", region="region-dhaka", demand_profile="industrial",
            capacity={"DIESEL": 18000, "PETROL": 9000, "OCTANE": 6000},
            inventory={"DIESEL": 10000, "PETROL": 5000, "OCTANE": 3000},
            demand_multiplier=1.0, status=StationStatus.OPEN
        ),
    }
    routes = {
        "route-gazipur-mirpur": Route(id="route-gazipur-mirpur", depot_id="depot-gazipur", station_id="station-mirpur", transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE),
        "route-gazipur-tongi": Route(id="route-gazipur-tongi", depot_id="depot-gazipur", station_id="station-tongi", transit_ticks=2, max_shipment=6500, status=RouteStatus.AVAILABLE),
    }
    return NetworkSnapshot(
        tick=50, sim_time="Day 1, 12:30", status="RUNNING",
        depots=depots, stations=stations, routes=routes
    )


def test_detection_engine_disruption_and_spike(sample_snapshot):
    detector = DetectionEngine()
    sample_snapshot.route_map["route-gazipur-mirpur"].status = RouteStatus.DISRUPTED
    sample_snapshot.station_map["station-tongi"].demand_multiplier = 1.8

    signals = detector.detect_signals(sample_snapshot)
    sig_types = [s.type for s in signals]
    assert "route_disruption" in sig_types
    assert "demand_spike" in sig_types


def test_risk_engine_critical_ranking(sample_snapshot):
    intel = IntelligenceService()
    # Mirpur has very low fuel -> should be flagged as critical
    rec = intel.evaluate_and_recommend(sample_snapshot)
    assert len(rec.risks) > 0
    top_risk = rec.risks[0]
    assert top_risk.station_id == "station-mirpur"
    assert top_risk.time_to_stockout_hours < 6.0


def test_lp_optimizer_allocates_within_constraints(sample_snapshot):
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(sample_snapshot)
    assert len(rec.legs) > 0
    for leg in rec.legs:
        # Check route limits
        assert leg.quantity_liters <= 7000
        # Check depot has inventory
        depot = sample_snapshot.depot_map[leg.depot_id]
        assert depot.inventory[leg.fuel.value] >= leg.quantity_liters


def test_greedy_fallback(sample_snapshot):
    greedy = GreedyPolicy()
    intel = IntelligenceService()
    forecasts = {
        (s.id, f.value): intel._get_forecast(s.id, f, 50, None, 1.0)
        for s in sample_snapshot.stations
        for f in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]
    }
    risks = intel.risk_engine.evaluate_risks(sample_snapshot, forecasts)
    legs = greedy.plan_allocations(sample_snapshot, risks)
    assert len(legs) > 0
    for leg in legs:
        assert leg.quantity_liters > 0


def test_decision_twin_counterfactual_futures(sample_snapshot):
    twin = DecisionTwin(horizon_ticks=24)
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(sample_snapshot)

    assert len(rec.twin_futures) == 3
    noop = rec.twin_futures[0]
    greedy = rec.twin_futures[1]
    lp = rec.twin_futures[2]

    assert noop.candidate_id == "noop"
    assert greedy.candidate_id == "greedy-v1"
    assert lp.candidate_id == "lp-v2"
    # LP should result in equal or lower unmet demand than No-Op
    assert lp.network_unmet_liters <= noop.network_unmet_liters
    assert rec.projected_unmet_avoided >= 0.0


def test_twin_verification_loop(sample_snapshot):
    twin = DecisionTwin(horizon_ticks=24)
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(sample_snapshot)

    # Verify prediction against mock outcome
    check = intel.twin.verify_twin_outcome(
        decision_id=rec.id,
        actual_snapshot=sample_snapshot,
        actual_unmet_liters=rec.after_projected_unmet + 15.0
    )
    assert check["verified"] is True
    assert "error_liters" in check
    assert check["confidence_factor"] > 0.60
