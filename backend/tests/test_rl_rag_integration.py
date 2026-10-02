"""Integration tests for FuelGuard RAG + RL + LangGraph + Decision Lifecycle."""
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.contracts import (
    FUELS,
    Depot,
    FuelType,
    NetworkSnapshot,
    Route,
    Station,
)
from app.explain.service import Explainer
from app.intel.service import IntelligenceService
from app.main import create_app
from app.rag.pipeline import get_rag_pipeline
from app.rl.inference.predictor import get_rl_predictor


@pytest.fixture
def integrated_snapshot():
    return NetworkSnapshot(
        tick=16,
        depots=[
            Depot(id="Gazipur", dispatch_capacity_per_tick=12000.0, capacity={f: 90000.0 for f in FUELS}, inventory={f: 80000.0 for f in FUELS}),
            Depot(id="Patiya", dispatch_capacity_per_tick=11000.0, capacity={f: 80000.0 for f in FUELS}, inventory={f: 65000.0 for f in FUELS}),
        ],
        stations=[
            Station(id="Tongi", capacity={f: 25000.0 for f in FUELS}, inventory={f: 4000.0 for f in FUELS}),
            Station(id="Airport", capacity={f: 30000.0 for f in FUELS}, inventory={f: 15000.0 for f in FUELS}),
            Station(id="Chittagong Port", capacity={f: 35000.0 for f in FUELS}, inventory={f: 20000.0 for f in FUELS}),
            Station(id="Agrabad", capacity={f: 25000.0 for f in FUELS}, inventory={f: 10000.0 for f in FUELS}),
        ],
        routes=[
            Route(id="Route_1", source_depot_id="Gazipur", destination_station_id="Tongi", transit_ticks=1, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_2", source_depot_id="Gazipur", destination_station_id="Airport", transit_ticks=2, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_3", source_depot_id="Patiya", destination_station_id="Chittagong Port", transit_ticks=1, max_shipment=5000.0, status="AVAILABLE"),
            Route(id="Route_4", source_depot_id="Patiya", destination_station_id="Agrabad", transit_ticks=2, max_shipment=5000.0, status="AVAILABLE"),
        ],
        in_transit=[],
        events=[],
    )


def test_rag_policy_retrieval_and_source_attribution():
    rag = get_rag_pipeline()
    if len(rag.store.get_all_chunks()) < 5:
        rag.ingest(force=True)
    # Query rules
    results = rag.search("depot reserve policy 10%", category="rules_policies", top_k=3)
    assert len(results) > 0
    top = results[0]
    assert top.source.endswith(".md")
    assert top.category == "rules_policies"
    assert "reserve" in top.content.lower()

    ask_res = rag.ask("What is the depot minimum reserve policy?")
    assert len(ask_res["sources"]) > 0
    assert ask_res["sources"][0]["category"] == "rules_policies"


def test_intel_service_includes_rl_candidate_and_twin_projection(integrated_snapshot):
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(integrated_snapshot, enable_multiagent=False)

    candidate_ids = [c.id for c in rec.candidates]
    assert "noop" in candidate_ids
    assert "greedy-v1" in candidate_ids
    assert "lp-v2" in candidate_ids
    assert "rl-ppo" in candidate_ids

    # Verify RL twin future exists
    future_ids = [f.candidate_id for f in rec.twin_futures]
    assert "rl-ppo" in future_ids
    f_rl = next(f for f in rec.twin_futures if f.candidate_id == "rl-ppo")
    assert f_rl.horizon_ticks == 24


def test_rl_fallback_behavior_when_unhealthy(integrated_snapshot, monkeypatch):
    intel = IntelligenceService()

    # Force RL predictor to report failure
    def mock_predict(*args, **kwargs):
        return {"is_valid": False, "rejection_reason": "Simulated hardware/model fault"}

    monkeypatch.setattr(intel.rl_predictor, "predict_recommendation", mock_predict)

    rec = intel.evaluate_and_recommend(integrated_snapshot, enable_multiagent=False)
    # The application remains operational and seamlessly selects LP optimizer
    assert rec.selected_candidate_id in ("lp-v2", "greedy-v1")
    assert "rl_policy" in rec.fallback_used


def test_copilot_explanation_with_grounded_rag_context(integrated_snapshot):
    explainer = Explainer()
    intel = IntelligenceService()
    rec = intel.evaluate_and_recommend(integrated_snapshot, enable_multiagent=False)

    resp = explainer.explain(rec, integrated_snapshot)
    assert resp.text
    # Template or LLM explanation must be grounded
    assert len(resp.cited_facts) > 0


def test_full_decision_lifecycle_with_rl(integrated_snapshot):
    settings = Settings(database_url="", sse_enabled=False)
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Ask RAG for policy verification
        r_rag = client.post("/api/rag/ask", json={"query": "minimum reserve requirement"})
        assert r_rag.status_code == 200

        # 2. Get RL candidate recommendation
        r_rl = client.post("/api/rl/recommend", json={})
        assert r_rl.status_code == 200
        rl_data = r_rl.json()
        assert rl_data["status"] == "pending_human_review"

        # 3. Check State and Health includes RL and RAG
        r_health = client.get("/api/health")
        assert r_health.status_code == 200
        health_data = r_health.json()
        comp_names = [c["name"] for c in health_data.get("components", [])]
        assert any("RL" in name for name in comp_names)
        assert any("RAG" in name for name in comp_names)
