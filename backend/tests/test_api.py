import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import build_services, create_app


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


def test_metrics_endpoint(api):
    api.get("/api/state")
    text = api.get("/metrics").text
    assert "fuelguard_http_requests_total" in text and "fuelguard_sim_requests_total" in text
