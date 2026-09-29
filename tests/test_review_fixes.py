"""
Dedicated verification tests for review feedback fixes (Anadi's Review).
Tests Docker blockers, calculation bugs, and architectural alignment.
"""

import time

import pytest
from app.contracts import (
    Depot,
    DepotStatus,
    Freshness,
    FuelType,
    NetworkSnapshot,
    ResourceFreshness,
    Route,
    RouteStatus,
    Signal,
    SignalSeverity,
    SimEvent,
    Station,
    StationStatus,
)
from app.intel import DetectionEngine, GreedyPolicy, IntelligenceService, LPOptimizer
from app.intel.baseline import BaselineForecaster, get_station_region_factor
from fastapi.testclient import TestClient

from forecaster.main import app as forecaster_app


@pytest.fixture
def review_snapshot():
    depots = [
        Depot(
            id="depot-gazipur",
            region_id="region-dhaka",
            capacity={"DIESEL": 90000, "PETROL": 70000, "OCTANE": 45000},
            inventory={"DIESEL": 60000, "PETROL": 45000, "OCTANE": 26000},
            dispatch_capacity_per_tick=12000,
            status=DepotStatus.OPEN,
        ),
        Depot(
            id="depot-patiya",
            region_id="region-chattogram",
            capacity={"DIESEL": 85000, "PETROL": 65000, "OCTANE": 40000},
            inventory={"DIESEL": 55000, "PETROL": 42000, "OCTANE": 24000},
            dispatch_capacity_per_tick=11000,
            status=DepotStatus.OPEN,
        ),
    ]
    stations = [
        Station(
            id="station-mirpur",
            region_id="region-dhaka",
            demand_profile="urban_high",
            capacity={"DIESEL": 15000, "PETROL": 14000, "OCTANE": 9000},
            inventory={"DIESEL": 1000, "PETROL": 800, "OCTANE": 600},
            demand_multiplier=1.0,
            status=StationStatus.OPEN,
        ),
        Station(
            id="station-tongi",
            region_id="region-dhaka",
            demand_profile="industrial",
            capacity={"DIESEL": 18000, "PETROL": 9000, "OCTANE": 6000},
            inventory={"DIESEL": 8000, "PETROL": 4000, "OCTANE": 2500},
            demand_multiplier=1.0,
            status=StationStatus.OPEN,
        ),
        Station(
            id="station-karnaphuli",
            region_id="region-chattogram",
            demand_profile="highway",
            capacity={"DIESEL": 14000, "PETROL": 15000, "OCTANE": 9000},
            inventory={"DIESEL": 7000, "PETROL": 8000, "OCTANE": 4500},
            demand_multiplier=1.0,
            status=StationStatus.OPEN,
        ),
    ]
    routes = [
        Route(
            id="route-gazipur-mirpur", source_depot_id="depot-gazipur", destination_station_id="station-mirpur",
            transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE
        ),
        Route(
            id="route-gazipur-tongi", source_depot_id="depot-gazipur", destination_station_id="station-tongi",
            transit_ticks=2, max_shipment=6500, status=RouteStatus.AVAILABLE
        ),
        Route(
            id="route-patiya-karnaphuli", source_depot_id="depot-patiya", destination_station_id="station-karnaphuli",
            transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE
        ),
    ]
    return NetworkSnapshot(
        tick=40,
        sim_time="Day 1, 10:00",
        depots=depots,
        stations=stations,
        routes=routes,
        dispatched_this_tick={"depot-gazipur": 2000.0},
    )


def test_b1_severity_check_containment_mode(review_snapshot):
    intel = IntelligenceService()
    # Add critical route disruption signal (severity="crit")
    sig = Signal(
        id="sig-test-1",
        kind="route_disrupted",
        severity=SignalSeverity.CRITICAL,
        message="Critical route disruption",
    )
    assert sig.severity == "crit"

    # Route disruption makes containment trigger
    review_snapshot.route_map["route-gazipur-mirpur"].status = RouteStatus.DISRUPTED
    rec = intel.evaluate_and_recommend(review_snapshot)
    assert rec.mode == "containment"
    assert rec.confidence < 0.95


def test_b2_signal_types_validation():
    # Verify depot_constrained and supply_delayed pass validation
    sig1 = Signal(type="depot_constrained", severity="warn", message="Depot constrained")
    assert sig1.kind == "depot_constrained"

    sig2 = Signal(type="supply_delayed", severity="warn", message="Supply delayed")
    assert sig2.kind == "supply_delayed"

    # Also verify legacy strings are accepted without ValidationError
    sig3 = Signal(type="depot_constraint", severity="warn", message="Depot constraint")
    assert sig3.kind == "depot_constrained"

    sig4 = Signal(type="shipment_delay", severity="warn", message="Shipment delay")
    assert sig4.kind == "supply_delayed"


def test_b3_demand_history_fields_and_ordering(review_snapshot):
    detector = DetectionEngine()
    # Mock newest-first history with demand_liters and fuel_type
    mock_history = [
        {"tick": 39, "station_id": "station-mirpur", "fuel_type": "PETROL", "demand_liters": 150.0},
        {"tick": 38, "station_id": "station-mirpur", "fuel_type": "PETROL", "demand_liters": 140.0},
        {"tick": 20, "station_id": "station-mirpur", "fuel_type": "PETROL", "demand_liters": 30.0},
    ]
    signals = detector.detect_signals(review_snapshot, mock_history)
    assert isinstance(signals, list)


def test_b5_dispatch_capacity_constraint(review_snapshot):
    greedy = GreedyPolicy()
    lp = LPOptimizer()
    intel = IntelligenceService()

    # Already dispatched 11,500 L this tick from Gazipur (capacity 12,000)
    review_snapshot.dispatched_this_tick = {"depot-gazipur": 11500.0}
    forecasts = {
        (s.id, f.value): intel._get_forecast(s.id, f, 40, None, 1.0)
        for s in review_snapshot.stations
        for f in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]
    }
    risks = intel.risk_engine.evaluate_risks(review_snapshot, forecasts)

    # Greedy allocations must not exceed remaining 500 L
    greedy_legs = greedy.plan_allocations(review_snapshot, risks)
    gazipur_greedy = sum(leg.quantity_liters for leg in greedy_legs if leg.depot_id == "depot-gazipur")
    assert gazipur_greedy <= 500.0

    # LP allocations must not exceed remaining 500 L
    lp_legs, _, _ = lp.optimize_allocations(review_snapshot, risks)
    gazipur_lp = sum(leg.quantity_liters for leg in lp_legs if leg.depot_id == "depot-gazipur")
    assert gazipur_lp <= 500.0


def test_b6_stale_data_flagging(review_snapshot):
    intel = IntelligenceService()
    rf = ResourceFreshness(fetched_at=None, age_seconds=30.0, stale=True)
    review_snapshot.freshness = Freshness(
        stale=True,
        reasons=["Circuit breaker open"],
        circuit="OPEN",
        resources={"stations": rf},
    )
    rec = intel.evaluate_and_recommend(review_snapshot)
    assert rec.built_on_stale_data is True
    assert rec.confidence <= 0.60
    assert rec.human_review_required is True


def test_c3_twin_future_fields(review_snapshot):
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(review_snapshot)
    assert len(rec.futures) == 3

    for f in rec.futures:
        assert f.label != ""
        assert isinstance(f.unmet_by_station, dict)
        assert 0.0 <= f.service_level <= 1.0
        assert isinstance(f.notes, list)


def test_c4_recommendation_candidates(review_snapshot):
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(review_snapshot)

    assert len(rec.candidates) == 3
    cand_ids = [c.id for c in rec.candidates]
    assert "noop" in cand_ids
    assert "greedy-v1" in cand_ids
    assert "lp-v2" in cand_ids
    assert rec.selected_candidate_id in ["lp-v2", "greedy-v1"]
    assert "policy" in rec.versions
    assert "forecast_model" in rec.versions


def test_c6_region_factor():
    assert get_station_region_factor("station-karnaphuli") == 1.08
    assert get_station_region_factor("station-coxsbazar") == 1.08
    assert get_station_region_factor("station-mirpur") == 1.0

    fc = BaselineForecaster()
    base_mirpur = fc.compute_base_demand_for_tick("highway", "DIESEL", tick=10, station_id="station-mirpur")
    base_karna = fc.compute_base_demand_for_tick("highway", "DIESEL", tick=10, station_id="station-karnaphuli")
    assert round(base_karna / base_mirpur, 2) == 1.08


def test_c6_scheduled_route_disruption_skipped(review_snapshot):
    # Route scheduled to disrupt at tick 41 (next tick)
    review_snapshot.events = [
        SimEvent(
            id=101,
            type="route_disruption",
            start_tick=41,
            end_tick=50,
            status="SCHEDULED",
            parameters={"route_ids": ["route-gazipur-mirpur"]},
        )
    ]
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(review_snapshot)
    # Neither LP nor Greedy should dispatch onto route-gazipur-mirpur
    for leg in rec.legs:
        assert leg.route_id != "route-gazipur-mirpur"


def test_forecaster_service_endpoints():
    client = TestClient(forecaster_app)

    # 1. Health
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["port"] == 8090

    # 2. Metrics (Prometheus)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "fuelguard_forecast_error" in r.text

    # 3. Chaos disable for 1 second
    r = client.post("/chaos/disable", json={"seconds": 1.0})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"

    # Immediately subsequent /forecast should return 503
    r = client.post("/forecast", json={
        "station_id": "station-mirpur",
        "fuel": "PETROL",
        "horizon_ticks": 4,
        "current_tick": 0,
    })
    assert r.status_code == 503

    # Wait for chaos disable to expire
    time.sleep(1.1)
    r = client.get("/health")
    assert r.status_code == 200
