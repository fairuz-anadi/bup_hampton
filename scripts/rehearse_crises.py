"""Rehearses every crisis in the blueprint's crisis matrix (section 07) against the full stack.

For each crisis: reset the simulator, run 60 calm ticks so stock is lower, record a calm
recommendation, inject the crisis, step into it, then check what the system does:

  detect     the expected signal appears in the recommendation
  respond    no fuel is planned into a disrupted route or a closed station; containment mode when a
             single-route station loses its route; confidence drops versus the calm run
  safe       submitting the plan loses no fuel (no FAILED allocations, no rejected legs)
  recover    once the event has ended, its signal is gone

Writes docs/crisis-rehearsal.md. Resets the simulator: never run it during a demo.

    python scripts/rehearse_crises.py
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--backend", default="http://localhost:8080")
p.add_argument("--sim", default="http://localhost:8000")
p.add_argument("--key", default=os.environ.get("OPERATOR_KEY", "local-dev-key"))
args = p.parse_args()


def call(method, url, body=None, key=False):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Operator-Key"] = args.key
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


api = lambda m, path, body=None: call(m, args.backend + path, body, key=True)
sim = lambda m, path, body=None: call(m, args.sim + path, body)

CALM_TICKS = 60

CRISES = [
    {"name": "Demand spike (Dhaka ×1.8)", "signals": {"demand_spike", "demand_anomaly"},
     "events": [{"type": "demand_spike", "duration_ticks": 12,
                 "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}}]},
    {"name": "Route disruption with a backup (Gazipur → Mirpur)", "signals": {"route_disrupted", "route_disruption"},
     "no_route": "route-gazipur-mirpur", "reroute": ("station-mirpur", "route-patiya-mirpur"),
     "events": [{"type": "route_disruption", "duration_ticks": 16,
                 "parameters": {"route_ids": ["route-gazipur-mirpur"]}}]},
    {"name": "Route disruption, single-route station (Gazipur → Tongi)",
     "signals": {"route_disrupted", "route_disruption"}, "no_route": "route-gazipur-tongi", "containment": True,
     "events": [{"type": "route_disruption", "duration_ticks": 16,
                 "parameters": {"route_ids": ["route-gazipur-tongi"]}}]},
    {"name": "Station outage (Tongi)", "signals": {"station_outage"}, "no_station": "station-tongi",
     "events": [{"type": "station_outage", "duration_ticks": 8, "parameters": {"station_ids": ["station-tongi"]}}]},
    {"name": "Depot constraint (Patiya)", "signals": {"depot_constrained", "depot_constraint"},
     "events": [{"type": "depot_constraint", "duration_ticks": 20, "parameters": {"depot_ids": ["depot-patiya"]}}]},
    {"name": "Shipment delay (Gazipur petrol +8)", "signals": {"supply_delayed", "shipment_delay"}, "one_shot": True,
     "events": [{"type": "shipment_delay", "duration_ticks": 1,
                 "parameters": {"delay_ticks": 8, "depot_ids": ["depot-gazipur"], "fuel_types": ["PETROL"]}}]},
    {"name": "Supply shortfall (Patiya diesel ×0.5)", "signals": {"supply_reduced", "supply_shortfall"},
     "one_shot": True,
     "events": [{"type": "supply_shortfall", "duration_ticks": 1,
                 "parameters": {"factor": 0.5, "depot_ids": ["depot-patiya"], "fuel_types": ["DIESEL"]}}]},
    {"name": "Combined: spike + Tongi route + stale data", "signals": {"demand_spike", "demand_anomaly"},
     "no_route": "route-gazipur-tongi", "containment": True, "stale_fault": True,
     "events": [{"type": "demand_spike", "duration_ticks": 12,
                 "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
                {"type": "route_disruption", "duration_ticks": 12,
                 "parameters": {"route_ids": ["route-gazipur-tongi"]}}]},
]


def step(n: int) -> None:
    for _ in range(n):
        sim("POST", "/admin/step")
    api("POST", "/api/chaos/sim/pause")  # also refreshes the backend snapshot


def recommend() -> tuple[int, dict]:
    return api("POST", "/api/recommendations")


def selected_legs(rec: dict) -> list[dict]:
    return next((c["legs"] for c in rec["candidates"] if c["id"] == rec["selected_candidate_id"]), [])


def rehearse(crisis: dict) -> dict:
    api("POST", "/api/chaos/faults/clear")
    api("POST", "/api/chaos/sim/reset")
    step(CALM_TICKS)
    code, calm = recommend()
    calm_conf = calm["confidence"] if code == 200 else None

    start = CALM_TICKS + 1
    end = start
    for ev in crisis["events"]:
        sim("POST", "/admin/events", {**ev, "start_tick": start})
        end = max(end, start + ev["duration_ticks"])
    step(2)  # into the event
    if crisis.get("stale_fault"):
        api("POST", "/api/chaos/faults", {"type": "stale_data", "duration_seconds": 30})
        step(1)

    checks: dict[str, tuple[bool, str]] = {}
    code, rec = recommend()
    checks["engine runs"] = (code == 200, "" if code == 200 else f"{code} {str(rec)[:160]}")
    if code != 200:
        api("POST", "/api/chaos/faults/clear")
        return {"crisis": crisis["name"], "checks": checks}

    kinds = {s["kind"] for s in rec["signals"]}
    checks["detect"] = (bool(kinds & crisis["signals"]), f"signals: {sorted(kinds) or 'none'}")
    legs = selected_legs(rec)
    if "no_route" in crisis:
        bad = [leg for leg in legs if leg["route_id"] == crisis["no_route"]]
        checks["no fuel into the disrupted route"] = (not bad, f"{len(bad)} legs on {crisis['no_route']}")
    if "no_station" in crisis:
        bad = [leg for leg in legs if leg["station_id"] == crisis["no_station"]]
        checks["no fuel to the closed station"] = (not bad, f"{len(bad)} legs to {crisis['no_station']}")
    if "reroute" in crisis:
        station, backup = crisis["reroute"]
        needs = any(r["station_id"] == station and r["severity"] in ("crit", "warn") for r in rec["risks"])
        used = any(leg["route_id"] == backup for leg in legs)
        checks["reroutes over the backup route"] = (used or not needs,
                                                    "used" if used else ("not needed" if not needs else "not used"))
    if crisis.get("containment"):
        checks["containment mode"] = (rec["mode"] == "containment", f"mode={rec['mode']}")
    if calm_conf is not None:
        checks["confidence drops"] = (rec["confidence"] < calm_conf, f"{calm_conf} → {rec['confidence']}")

    if crisis.get("stale_fault"):
        checks["flagged as stale"] = (rec["built_on_stale_data"], f"built_on_stale_data={rec['built_on_stale_data']}")
        api("POST", "/api/decisions", {"recommendation": {**rec, "id": rec["id"] + "-stale"}})
        code, _ = api("POST", f"/api/decisions/{rec['id']}-stale/approve", {"by": "rehearsal"})
        checks["stale plan cannot be approved"] = (code == 409, f"approve → {code}")
        api("POST", "/api/chaos/faults/clear")
        step(1)
        code, rec = recommend()
        legs = selected_legs(rec) if code == 200 else []

    failures_before = sim("GET", "/v1/metrics")[1]["allocation_failures"]
    if legs:
        _, resp = api("POST", "/api/allocations", {"decision_id": f"rh-{rec['id']}", "legs": [
            {k: leg[k] for k in ("route_id", "source_depot_id", "station_id", "fuel_type", "quantity")}
            for leg in legs]})
        results = [s["result"] for s in resp.get("submissions", [])]
        codes = sorted({s["error_code"] for s in resp.get("submissions", []) if s["error_code"]})
        checks["plan passes pre-checks"] = ("rejected" not in results and "skipped" not in results,
                                            f"{len(results)} legs; blocked: {codes or 'none'}")
    step(max(4, end - start))
    failures_after = sim("GET", "/v1/metrics")[1]["allocation_failures"]
    checks["no fuel lost (no FAILED shipments)"] = (failures_after == failures_before,
                                                    f"failures {failures_before} → {failures_after}")

    step(2)
    code, after = recommend()
    if code == 200 and not crisis.get("one_shot"):
        left = {s["kind"] for s in after["signals"]} & crisis["signals"]
        checks["recovers (signal clears)"] = (not left, f"still: {sorted(left)}" if left else "cleared")
    return {"crisis": crisis["name"], "checks": checks}


def main() -> None:
    results = []
    try:
        for crisis in CRISES:
            r = rehearse(crisis)
            results.append(r)
            ok = sum(v[0] for v in r["checks"].values())
            print(f"{r['crisis']:<52} {ok}/{len(r['checks'])} checks passed", flush=True)
            for name, (passed, detail) in r["checks"].items():
                print(f"   {'PASS' if passed else 'FAIL'} {name}: {detail}")
    finally:
        api("POST", "/api/chaos/faults/clear")
        api("POST", "/api/chaos/sim/reset")

    total = sum(len(r["checks"]) for r in results)
    passed = sum(v[0] for r in results for v in r["checks"].values())
    lines = ["# Crisis rehearsal", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/rehearse_crises.py` against the full stack "
             f"(official simulator, backend, decision engine). **{passed}/{total} checks passed.**", "",
             f"Each crisis starts after {CALM_TICKS} calm ticks and is checked two ticks in. Numbers are from "
             "the official simulator.", ""]
    for r in results:
        lines += [f"## {r['crisis']}", "", "| Check | Result | Detail |", "|---|---|---|"]
        for name, (ok, detail) in r["checks"].items():
            lines.append(f"| {name} | {'✅' if ok else '❌'} | {detail.replace('|', '/')} |")
        lines.append("")
    out = ROOT / "docs" / "crisis-rehearsal.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed}/{total} checks passed. Wrote {out}")


if __name__ == "__main__":
    main()
