"""Records the fixtures in fixtures/ from a live simulator + backend, and exports the contract schema.

    python scripts/record_fixtures.py            (simulator on :8000, backend on :8080)

Writes:
  fixtures/simulator_tick0.json     raw /v1/* responses at tick 0 (used by backend unit tests)
  fixtures/snapshot.json            a NetworkSnapshot mid-run, with shipments in transit
  fixtures/demand_history.json      /api/demand-history after one simulated day
  fixtures/recommendation.json      a hand-written example Recommendation (mock for the UI lane)
  fixtures/contracts.schema.json    JSON Schema of every shared contract
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / "fixtures"
SIM, API = "http://localhost:8000", "http://localhost:8080"


def call(method, url, body=None, headers=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or "null")


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")
    print("wrote", OUT / name)


def main():
    import time

    from app import contracts as c

    OUT.mkdir(exist_ok=True)
    call("POST", SIM + "/admin/faults/clear"); call("POST", SIM + "/admin/pause")
    call("POST", SIM + "/admin/reset"); call("POST", SIM + "/admin/pause")
    world = {k: call("GET", f"{SIM}/v1/{k.replace('_', '-')}") for k in
             ("instance", "regions", "depots", "stations", "routes", "supply_arrivals", "events", "allocations",
              "metrics")}
    dump("simulator_tick0.json", world)

    # One simulated day with a couple of shipments, then a spike and a disruption so the snapshot
    # has something interesting in it.
    for _ in range(96):
        call("POST", SIM + "/admin/step")
    call("POST", SIM + "/admin/events", {"type": "demand_spike", "start_tick": 96, "duration_ticks": 16,
                                         "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}})
    call("POST", SIM + "/admin/events", {"type": "route_disruption", "start_tick": 97, "duration_ticks": 12,
                                         "parameters": {"route_ids": ["route-gazipur-tongi"]}})
    call("POST", SIM + "/admin/step")
    key = {"X-Operator-Key": "local-dev-key"}
    call("POST", API + "/api/allocations", {"decision_id": "fixture-1", "legs": [
        {"route_id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur", "station_id": "station-mirpur",
         "fuel_type": "PETROL", "quantity": 3000},
        {"route_id": "route-patiya-coxsbazar", "source_depot_id": "depot-patiya", "station_id": "station-coxsbazar",
         "fuel_type": "DIESEL", "quantity": 2500}]}, key)
    call("POST", SIM + "/admin/step")
    time.sleep(2.5)
    dump("snapshot.json", call("GET", API + "/api/state"))
    dump("demand_history.json", call("GET", API + "/api/demand-history?limit=1200"))

    rec = c.Recommendation(
        id="rec-0042", tick=212, created_at="2026-10-02T14:03:11Z", mode="prevention",
        candidates=[
            c.Candidate(id="noop", policy="noop", legs=[]),
            c.Candidate(id="greedy-v1", policy="greedy-v1", legs=[c.AllocationLeg(
                route_id="route-gazipur-mirpur", source_depot_id="depot-gazipur", station_id="station-mirpur",
                fuel_type="PETROL", quantity=5000)]),
            c.Candidate(id="lp-v2", policy="lp-v2", legs=[c.AllocationLeg(
                route_id="route-gazipur-mirpur", source_depot_id="depot-gazipur", station_id="station-mirpur",
                fuel_type="PETROL", quantity=3000)])],
        selected_candidate_id="lp-v2",
        futures=[
            c.TwinFuture(candidate_id="noop", label="Do nothing", horizon_ticks=24, network_unmet_liters=1496,
                         unmet_by_station={"station-mirpur": 1296, "station-tongi": 200}, first_stockout_tick=225,
                         service_level=0.83),
            c.TwinFuture(candidate_id="greedy-v1", label="Send 5,000 L", horizon_ticks=24, network_unmet_liters=550,
                         unmet_by_station={"station-mirpur": 0, "station-tongi": 550}, first_stockout_tick=230,
                         service_level=0.94, notes=["Tongi short later"]),
            c.TwinFuture(candidate_id="lp-v2", label="Send 3,000 L", horizon_ticks=24, network_unmet_liters=200,
                         unmet_by_station={"station-mirpur": 0, "station-tongi": 200}, first_stockout_tick=None,
                         service_level=0.98)],
        risks=[c.RiskItem(station_id="station-mirpur", fuel_type="PETROL", hours_to_stockout=3.2, p_stockout=0.78,
                          has_backup_route=True),
               c.RiskItem(station_id="station-tongi", fuel_type="PETROL", hours_to_stockout=5.5, p_stockout=0.41,
                          has_backup_route=False)],
        signals=[c.Signal(kind="demand_anomaly", severity="warn", message="Demand 31% above forecast for 6 ticks",
                          station_id="station-mirpur", fuel_type="PETROL", value=0.31),
                 c.Signal(kind="route_disrupted", severity="crit", message="route-gazipur-tongi DISRUPTED until tick 216"),
                 c.Signal(kind="supply_delayed", severity="warn", message="Gazipur petrol supply delayed to tick 234")],
        constraints=["depot-gazipur dispatch 12,000 L/tick", "station-mirpur petrol headroom 12,000 L incl. in transit"],
        confidence=0.74, versions={"policy": "lp-v2", "forecast_model": "fc-v1", "deployment": "git-dev"})
    dump("recommendation.json", json.loads(rec.model_dump_json()))

    schema = {name: model.model_json_schema() for name, model in {
        "NetworkSnapshot": c.NetworkSnapshot, "ForecastRequest": c.ForecastRequest,
        "ForecastResponse": c.ForecastResponse, "Recommendation": c.Recommendation,
        "DecisionRecord": c.DecisionRecord, "ExplainRequest": c.ExplainRequest, "ExplainResponse": c.ExplainResponse,
        "SubmitAllocationsRequest": c.SubmitAllocationsRequest, "HealthReport": c.HealthReport}.items()}
    dump("contracts.schema.json", schema)
    call("POST", SIM + "/admin/reset"); call("POST", SIM + "/admin/pause")


if __name__ == "__main__":
    main()
