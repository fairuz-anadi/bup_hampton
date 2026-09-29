import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import build_services, create_app

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def api(transport, monkeypatch):
    settings = Settings(operator_key=SecretStr("test-key"), sse_enabled=False, sim_retries=0,
                        sim_backoff_base_seconds=0)
    monkeypatch.setattr("app.api.auth.get_settings", lambda: settings)
    services = build_services(settings, transport=transport)
    app = create_app(settings, services=services, start_sync=False)
    with TestClient(app) as client:
        client.portal.call(services.store.refresh)
        yield client


def test_state_and_health(api):
    state = api.get("/api/state").json()
    assert state["tick"] == 0 and len(state["stations"]) == 4
    health = api.get("/api/health").json()
    names = {c["name"]: c["status"] for c in health["components"]}
    assert names["Simulator"] == "healthy" and names["Backend API"] == "healthy"


def test_writes_need_operator_key(api):
    body = {"decision_id": "t1", "legs": [{"route_id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur",
                                           "station_id": "station-mirpur", "fuel_type": "DIESEL", "quantity": 100}]}
    assert api.post("/api/allocations", json=body).status_code == 401
    assert api.post("/api/allocations", json=body, headers={"X-Operator-Key": "wrong"}).status_code == 401
    r = api.post("/api/allocations", json=body, headers={"X-Operator-Key": "test-key"})
    assert r.status_code == 200 and r.json()["submissions"][0]["result"] == "accepted"


def test_input_validation(api):
    bad = {"decision_id": "bad id with spaces", "legs": []}
    assert api.post("/api/allocations", json=bad, headers={"X-Operator-Key": "test-key"}).status_code == 422
    neg = {"decision_id": "ok", "legs": [{"route_id": "r", "source_depot_id": "d", "station_id": "s",
                                          "fuel_type": "DIESEL", "quantity": -5}]}
    assert api.post("/api/allocations", json=neg, headers={"X-Operator-Key": "test-key"}).status_code == 422


def test_decision_review_flow(api):
    rec = json.loads(FIXTURES.joinpath("recommendation.json").read_text())
    rec["tick"] = 0
    key = {"X-Operator-Key": "test-key"}
    assert api.post("/api/decisions", json={"recommendation": rec}).status_code == 401
    r = api.post("/api/decisions", json={"recommendation": rec}, headers=key)
    assert r.status_code == 201 and r.json()["stage"] == "projected"
    assert api.post("/api/decisions", json={"recommendation": rec}, headers=key).status_code == 409
    assert api.post(f"/api/decisions/{rec['id']}/reject", json={"by": "a"}, headers=key).status_code == 422
    r = api.post(f"/api/decisions/{rec['id']}/approve", json={"by": "anadi"}, headers=key)
    assert r.status_code == 200 and r.json()["stage"] == "submitted"
    assert [d["decision_id"] for d in api.get("/api/decisions").json()] == [rec["id"]]


def test_chaos_and_pacer_proxy(api, fake_sim):
    key = {"X-Operator-Key": "test-key"}
    r = api.post("/api/chaos/events", json={"type": "demand_spike", "start_in_ticks": 2, "duration_ticks": 8,
                                            "parameters": {"region_ids": ["region-dhaka"]}}, headers=key)
    assert r.status_code == 201
    assert fake_sim.admin_calls[-1][1] == "/admin/events" and fake_sim.admin_calls[-1][2]["start_tick"] == 2
    assert api.post("/api/chaos/faults", json={"type": "boom", "duration_seconds": 5}, headers=key).status_code == 422
    assert api.post("/api/chaos/faults", json={"type": "stale_data", "duration_seconds": 5},
                    headers=key).status_code == 201
    assert api.post("/api/chaos/sim/explode", headers=key).status_code == 404
    assert api.post("/api/chaos/sim/step", headers=key).status_code == 200
    assert api.put("/api/policy", json={"policy": "lp-v2"}, headers=key).json()["active"] == "lp-v2"
    assert api.post("/api/policy/rollback", headers=key).json()["active"] == "greedy-v1"
    assert api.get("/api/health").json()["active_policy"] == "greedy-v1"


def test_metrics_endpoint(api):
    api.get("/api/state")
    text = api.get("/metrics").text
    assert "fuelguard_http_requests_total" in text and "fuelguard_sim_requests_total" in text
