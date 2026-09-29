"""Confidence gate, autonomy, decision engine, copilot templates and their API (Samprity's lane)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.contracts import AllocationLeg, DecisionRecord, NetworkSnapshot, Recommendation
from app.decisions.engine import DecisionEngine, _fixture_recommender, apply_policy, scoreboard
from app.decisions.gate import AutonomyController, compute_factors, confidence, evaluate, target_mode, twin_accuracy
from app.explain import templates as T
from app.explain.graph import faithfulness_problem, unsupported_numbers
from app.explain.service import Explainer
from app.main import build_services, create_app

FIX = Path(__file__).resolve().parents[2] / "fixtures"
HEALTHY = {"fit": 0.92, "twin": 0.88, "fresh": 1.0, "normal": 1.0, "health": 1.0, "crisis": 1.0}
KEY = {"X-Operator-Key": "test-key"}


@pytest.fixture
def snap():
    return NetworkSnapshot.model_validate(json.loads((FIX / "snapshot.json").read_text()))


@pytest.fixture
def rec():
    return Recommendation.model_validate(json.loads((FIX / "recommendation.json").read_text()))


# ---------------------------------------------------------------- gate + autonomy

def test_confidence_weights_and_modes():
    assert confidence(HEALTHY) == pytest.approx(0.956, abs=1e-3)
    assert target_mode(HEALTHY) == "AUTONOMOUS"
    assert target_mode({**HEALTHY, "crisis": 0.6}) == "SUPERVISED"
    assert target_mode({**HEALTHY, "fresh": 0.0}) == "MANUAL"


def test_autonomy_drops_at_once_and_climbs_slowly():
    a = AutonomyController(mode="AUTONOMOUS", armed=True)
    a.observe(1, HEALTHY)
    assert a.mode == "AUTONOMOUS"
    a.observe(2, {**HEALTHY, "fresh": 0.0})
    assert a.mode == "MANUAL" and not a.armed
    for t in (3, 4):
        a.observe(t, HEALTHY)
        assert a.mode == "MANUAL"
    a.observe(4, HEALTHY)  # the same tick again does not count
    a.observe(5, HEALTHY)
    assert a.mode == "SUPERVISED"
    for t in range(6, 12):
        a.observe(t, HEALTHY)
    assert a.mode == "SUPERVISED"  # Autonomous needs an operator
    ok, _ = a.rearm()
    assert ok and a.mode == "AUTONOMOUS"


def test_rearm_refused_when_unhealthy():
    a = AutonomyController()
    a.observe(1, {**HEALTHY, "crisis": 0.6})
    ok, msg = a.rearm()
    assert not ok and "refused" in msg


def test_gate_blocks_disrupted_route_and_stale(snap, rec):
    leg = AllocationLeg(route_id="route-gazipur-tongi", source_depot_id="depot-gazipur", station_id="station-tongi",
                        fuel_type="PETROL", quantity=1000)
    g = evaluate(rec.model_copy(update={"legs": [leg]}), snap, "AUTONOMOUS", 0.95)
    assert not g.executable and g.requires_human and "DISRUPTED" in g.blocked_legs[0]["reason"]
    stale = snap.model_copy(update={"freshness": snap.freshness.model_copy(update={"stale": True})})
    g = evaluate(rec, stale, "AUTONOMOUS", 0.95)
    assert not g.executable and not g.auto_execute


def test_gate_human_review_rules(snap, rec):
    g = evaluate(rec, snap, "SUPERVISED", 0.74)
    assert g.requires_human and any("0.74 < 0.80" in r for r in g.reasons)
    g = evaluate(rec, snap, "AUTONOMOUS", 0.9)
    assert g.auto_execute and not g.requires_human  # 3,000 L on an available route
    assert evaluate(rec.model_copy(update={"mode": "containment"}), snap, "AUTONOMOUS", 0.9).requires_human


def test_factors_react_to_signals_and_twin_error(snap, rec):
    f = compute_factors(snap, rec)
    assert f["normal"] < 1 and f["crisis"] < 1  # demand anomaly + two ACTIVE events in the fixture
    f2 = compute_factors(snap, rec.model_copy(update={"fallback_used": ["forecaster"]}))
    assert f2["fit"] <= 0.62 and f2["health"] <= 0.5
    good = DecisionRecord(decision_id="a", sim_tick=1, twin_check={"predicted_l": 200, "actual_l": 240, "error_l": 40})
    bad = DecisionRecord(decision_id="b", sim_tick=2, twin_check={"predicted_l": 0, "actual_l": 5000, "error_l": 5000})
    assert twin_accuracy([good]) == pytest.approx(0.92, abs=1e-3)
    assert twin_accuracy([good, bad]) < 0.5 and twin_accuracy([]) is None


def test_policy_switch_selects_the_twin_candidate():
    legs = [AllocationLeg(route_id="r", source_depot_id="d", station_id="s", fuel_type="PETROL", quantity=q)
            for q in (5000, 3000)]
    rec = Recommendation(id="x", tick=1, created_at="2026-01-01T00:00:00Z", policy="lp-v2", legs=[legs[1]],
                         twin_futures=[{"candidate_id": "noop", "network_unmet_liters": 900.0},
                                       {"candidate_id": "greedy-v1", "network_unmet_liters": 300.0, "legs": [legs[0]]},
                                       {"candidate_id": "lp-v2", "network_unmet_liters": 200.0, "legs": [legs[1]]}])
    g = apply_policy(rec, "greedy-v1")
    assert g.selected_candidate_id == "greedy-v1" and g.legs[0].quantity == 5000 and len(g.candidates) == 3
    assert apply_policy(rec, "unknown").selected_candidate_id == "lp-v2"


# ---------------------------------------------------------------- copilot

def test_template_explanation_covers_section_9(snap, rec):
    out = T.explain_decision(T.decision_facts(rec, snap, {"requires_human": True, "executable": True,
                                                          "reasons": ["confidence 0.74 < 0.80"]}))
    for part in ("Recommend.", "Why.", "Binding constraints.", "Projected impact.", "Alternatives considered.",
                 "Confidence."):
        assert part in out.text
    assert "1,296 L projected unmet demand avoided vs the no-action counterfactual" in out.text
    assert "saved" not in out.text.lower() and out.source == "template"


def test_investigate_summary_and_incident_templates(snap, rec):
    st = T.investigate_station(T.station_facts(snap, "station-tongi", rec))
    assert "Tongi" in st.text and "no backup route" in st.text
    assert T.station_facts(snap, "station-nowhere") is None
    net = T.network_summary(snap)
    assert "Service level 86.7%" in net.text and "route-gazipur-tongi" in net.text
    rec_record = DecisionRecord(decision_id="d1", sim_tick=97, stage="rejected", recommendation=rec,
                                approval={"decision": "rejected", "by": "samprity", "reason": "hold for Tongi"})
    inc = T.incident_report(T.incident_facts(snap, [rec_record], 90, 98, [{"tick": 97, "message": "Mode A -> B"}]))
    for part in ("Summary.", "Decisions.", "Autonomy.", "Now.", "Open."):
        assert part in inc.text
    assert "hold for Tongi" in inc.text


def test_faithfulness_check():
    facts = {"legs": [{"litres": 3000.0}], "confidence": 0.74, "before_unmet_l": 1496.0}
    assert not unsupported_numbers("Send 3,000 L; confidence 74%; 1,496 L before.", facts)
    assert unsupported_numbers("Send 4,200 L now.", facts) == {4200.0}
    assert faithfulness_problem("This saved 3,000 L.", facts) == "broke the projection wording rule"


def test_explainer_without_llm_uses_templates(snap, rec, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = Explainer().explain(rec, snap)
    assert out.source == "template" and "Projected impact" in out.text


def test_copilot_eval_dataset_passes_on_templates():
    from app.explain.evals import load_cases, run_case
    cases = load_cases()
    assert len(cases) >= 6
    results = [run_case(c, Explainer()) for c in cases]
    assert all(r["passed"] for r in results), [r for r in results if not r["passed"]]


# ---------------------------------------------------------------- engine + API

def test_scoreboard_is_labelled_projected(rec):
    r = DecisionRecord(decision_id="d", sim_tick=1, stage="verified", recommendation=rec,
                       twin_check={"predicted_l": 200.0, "actual_l": 240.0, "error_l": 40.0})
    sb = scoreboard([r])
    assert sb["executed"] == 1 and sb["projected_avoided_vs_noop_l"] == 1296.0
    assert sb["projected_vs_baseline_l"] == 350.0 and sb["mean_twin_error_l"] == 40.0 and "not outcomes" in sb["note"]


@pytest.fixture
def api(transport, monkeypatch):
    settings = Settings(operator_key=SecretStr("test-key"), sse_enabled=False, sim_retries=0,
                        sim_backoff_base_seconds=0)
    monkeypatch.setattr("app.api.auth.get_settings", lambda: settings)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    services = build_services(settings, transport=transport)
    services.engine = DecisionEngine(services.decisions, _fixture_recommender, "fixture", autopilot=False)
    app = create_app(settings, services=services, start_sync=False)
    with TestClient(app) as client:
        client.portal.call(services.store.refresh)
        yield client


def test_engine_registers_a_reviewable_decision(api):
    cur = api.get("/api/recommendations/current").json()
    rid = cur["recommendation"]["id"]
    assert cur["record_stage"] == "gated" and cur["autonomy"]["mode"] in ("MANUAL", "SUPERVISED")
    rec = api.get(f"/api/decisions/{rid}").json()
    assert rec["gate"]["confidence"] == cur["gate"]["confidence"] and rec["mode"] == cur["autonomy"]["mode"]
    # the backend's review flow (control_routes) approves it
    r = api.post(f"/api/decisions/{rid}/approve", json={"by": "samprity", "reason": "ok"}, headers=KEY)
    assert r.status_code == 200, r.text
    assert api.get("/api/recommendations/current").json()["record_stage"] == "submitted"
    sb = api.get("/api/scoreboard").json()
    assert sb["executed"] == 1


def test_copilot_endpoints(api):
    rid = api.get("/api/recommendations/current").json()["recommendation"]["id"]
    assert "Projected impact" in api.post("/api/explain", json={"decision_id": rid}).json()["text"]
    assert api.post("/api/explain", json={"decision_id": "nope"}).status_code == 404
    assert "Mirpur" in api.post("/api/copilot/investigate", json={"station_id": "station-mirpur"}).json()["text"]
    assert api.post("/api/copilot/investigate", json={"station_id": "station-x"}).status_code == 404
    assert "Service level" in api.post("/api/copilot/summary", json={}).json()["text"]
    assert "Summary." in api.get("/api/copilot/incident-report").json()["text"]
    assert api.get("/api/copilot/incident-report?from_tick=5&to_tick=1").status_code == 422
    assert api.get("/api/copilot/info").json()["llm"] is None


def test_autonomy_endpoints_and_health(api):
    api.get("/api/recommendations/current")
    assert api.get("/api/autonomy").json()["factors"][0]["key"] == "fit"
    assert api.post("/api/autonomy/mode", json={"mode": "MANUAL"}).status_code == 401
    assert api.post("/api/autonomy/mode", json={"mode": "MANUAL"}, headers=KEY).json()["mode"] == "MANUAL"
    rearmed = api.post("/api/autonomy/rearm", headers=KEY)  # the tick-0 world is healthy, so re-arm is allowed
    assert rearmed.status_code == 200 and rearmed.json()["armed"] is True
    names = {c["name"] for c in api.get("/api/health").json()["components"]}
    assert {"Decision engine", "Explanation"} <= names


def test_autopilot_executes_only_in_autonomous_mode(transport, monkeypatch):
    settings = Settings(operator_key=SecretStr("test-key"), sse_enabled=False, sim_retries=0,
                        sim_backoff_base_seconds=0)
    services = build_services(settings, transport=transport)
    eng = DecisionEngine(services.decisions, _fixture_recommender, "fixture", autopilot=True)
    eng.autonomy = AutonomyController(mode="AUTONOMOUS", armed=True)
    app = create_app(settings, services=services, start_sync=False)
    with TestClient(app) as client:
        client.portal.call(services.store.refresh)
        snap = services.store.snapshot
        monkeypatch.setattr("app.decisions.engine.compute_factors", lambda *a, **k: dict(HEALTHY))
        view = client.portal.call(eng.cycle, snap, [], None, None)
        assert view["gate"]["auto_execute"] is True
        assert services.decisions.repo.get(view["recommendation"]["id"]).approval["by"] == "autopilot"
