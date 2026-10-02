"""Tests for Copilot, LangGraph StateGraph, and Fallback Templates."""

from pathlib import Path

import pytest
from app.contracts import (
    AutonomyMode,
    Depot,
    DepotStatus,
    NetworkSnapshot,
    Route,
    RouteStatus,
    Station,
    StationStatus,
)
from app.copilot import CopilotService, DeterministicCopilot
from app.intel import IntelligenceService
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@pytest.fixture
def sample_rec_and_snapshot():
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
            inventory={"DIESEL": 1500, "PETROL": 1200, "OCTANE": 800},
            demand_multiplier=1.5, status=StationStatus.OPEN
        ),
    }
    routes = {
        "route-gazipur-mirpur": Route(
            id="route-gazipur-mirpur", depot_id="depot-gazipur", station_id="station-mirpur",
            transit_ticks=2, max_shipment=7000, status=RouteStatus.AVAILABLE
        ),
    }
    snapshot = NetworkSnapshot(tick=10, sim_time="Day 1, 02:30", depots=depots, stations=stations, routes=routes)
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(snapshot)
    return rec, snapshot


def test_deterministic_copilot_fallback(sample_rec_and_snapshot):
    rec, snapshot = sample_rec_and_snapshot
    fallback = DeterministicCopilot()
    resp = fallback.explain(rec, snapshot)
    assert resp.is_fallback is True
    assert "Operational Rationale" in resp.text
    assert len(resp.cited_facts) > 0


def test_copilot_service_execution(sample_rec_and_snapshot):
    rec, snapshot = sample_rec_and_snapshot
    copilot = CopilotService()
    resp = copilot.explain(rec, snapshot)
    assert resp is not None
    assert len(resp.text) > 50
    assert len(resp.cited_facts) > 0


def test_decision_record_audit_generation(sample_rec_and_snapshot):
    rec, snapshot = sample_rec_and_snapshot
    copilot = CopilotService()
    record = copilot.build_decision_record(
        recommendation=rec,
        snapshot=snapshot,
        mode=AutonomyMode.SUPERVISED,
        operator_approval={"decision": "approved", "by": "turjo", "tick": 10},
    )
    assert record.decision_id == rec.id
    assert record.mode == AutonomyMode.SUPERVISED
    assert record.approval["by"] == "turjo"
    assert "candidates" in record.model_dump()
    assert len(record.candidates) >= 3
