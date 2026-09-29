"""End-to-end smoke test: backend + official simulator.

Needs both running (docker compose up, or uvicorn on :8080 + simulator on :8000).
Resets the simulator, so never run it during a demo.

    python scripts/backend_smoke.py [--backend http://localhost:8080] [--sim http://localhost:8000]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--backend", default="http://localhost:8080")
p.add_argument("--sim", default="http://localhost:8000")
p.add_argument("--key", default=os.environ.get("OPERATOR_KEY", "local-dev-key"))
p.add_argument("--docker", action="store_true", help="also stop/start postgres to test buffering (compose stack)")
args = p.parse_args()


def call(method, url, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                               headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


api = lambda m, path, body=None: call(m, args.backend + path, body, {"X-Operator-Key": args.key})
sim = lambda m, path, body=None: call(m, args.sim + path, body)
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_for(pred, timeout=15.0, every=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(every)
    return pred()


def leg(route, depot, station, fuel, qty):
    return {"route_id": route, "source_depot_id": depot, "station_id": station, "fuel_type": fuel, "quantity": qty}


sim("POST", "/admin/faults/clear"); sim("POST", "/admin/pause"); sim("POST", "/admin/reset"); sim("POST", "/admin/pause")
check("backend reachable", wait_for(lambda: api("GET", "/api/health")[0] == 200, 30))
st = wait_for(lambda: (lambda s: s[1] if s[0] == 200 and s[1]["tick"] == 0 else None)(api("GET", "/api/state")))
check("state synced after reset", st is not None and not st["freshness"]["stale"], st and st["freshness"])

# --- writes need the operator key
code, _ = call("POST", args.backend + "/api/allocations", {"decision_id": "smoke-nokey", "legs": [
    leg("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 100)]})
check("allocation without key -> 401", code == 401, code)

# --- a normal submission, a split, and pre-check blocks
code, r = api("POST", "/api/allocations", {"decision_id": "smoke-1", "legs": [
    leg("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 3000),
    leg("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 7000),           # > 6500 -> split in 2
    leg("route-patiya-karnaphuli", "depot-patiya", "station-karnaphuli", "OCTANE", 4000),   # 5200 + 4000 > 9000
]})
subs = r["submissions"] if code == 200 else []
check("submit returns 200", code == 200, r)
check("3000 L petrol accepted", subs and subs[0]["result"] == "accepted", subs[:1])
check("7000 L split into 6500 + 500, both accepted",
      [(s["leg"]["quantity"], s["result"]) for s in subs[1:3]] == [(6500, "accepted"), (500, "accepted")], subs[1:3])
check("overfill blocked by pre-check", subs and subs[-1]["error_code"] == "PRECHECK_TANK_HEADROOM", subs[-1:])

code, r2 = api("POST", "/api/allocations", {"decision_id": "smoke-1", "legs": [
    leg("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 3000)]})
check("idempotent resubmit accepted, same sim id",
      code == 200 and r2["submissions"][0]["sim_allocation_id"] == subs[0]["sim_allocation_id"], r2)
sims = sim("GET", "/v1/allocations")[1]
check("no duplicate shipment in simulator", sum(a["idempotency_key"] == "fg-smoke-1-0" for a in sims) == 1)

st = wait_for(lambda: (lambda s: s if len(s["in_transit"]) == 3 else None)(api("GET", "/api/state")[1]), 5)
st = st or api("GET", "/api/state")[1]
check("in-transit ledger has the legs", len(st["in_transit"]) == 3, len(st["in_transit"]))
check("dispatched_this_tick counts gazipur", st["dispatched_this_tick"].get("depot-gazipur") == 10000,
      st["dispatched_this_tick"])

# --- scheduled disruption blocks a post that would FAIL and lose the fuel
sim("POST", "/admin/events", {"type": "route_disruption", "start_tick": st["tick"], "duration_ticks": 4,
                              "parameters": {"route_ids": ["route-patiya-mirpur"]}})
code, r = api("POST", "/api/allocations", {"decision_id": "smoke-2", "legs": [
    leg("route-patiya-mirpur", "depot-patiya", "station-mirpur", "PETROL", 1000)]})
check("scheduled disruption blocked", code == 200 and r["submissions"][0]["error_code"] == "PRECHECK_ROUTE_DISRUPTED", r)

# --- ticks move the ledger
for _ in range(3):
    sim("POST", "/admin/step")
st = wait_for(lambda: (lambda s: s if s["tick"] == 3 else None)(api("GET", "/api/state")[1]))
check("state follows steps", st is not None, "tick never reached 3")
arrived = [a for a in sim("GET", "/v1/allocations")[1] if a["status"] == "ARRIVED"]
check("2-tick shipments arrived", len(arrived) >= 3, len(arrived))

# --- fault: simulator unavailable -> degraded, cached state, writes held
sim("POST", "/admin/faults", {"type": "unavailable", "duration_seconds": 20})
h = wait_for(lambda: (lambda h: h if h["status"] == "degraded" else None)(api("GET", "/api/health")[1]), 20)
check("health degraded while simulator faulted", h is not None, api("GET", "/api/health")[1])
code, st = api("GET", "/api/state")
check("state still served (cached, stale)", code == 200 and st["freshness"]["stale"], st.get("freshness") if code == 200 else code)
st = wait_for(lambda: (lambda s: s if s["freshness"]["circuit"] == "OPEN" else None)(api("GET", "/api/state")[1]), 20)
check("circuit opens", st is not None)
code, r = api("POST", "/api/allocations", {"decision_id": "smoke-3", "legs": [
    leg("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 500)]})
check("write held while circuit open", code == 200 and r["submissions"][0]["result"] == "held", r)
sim("POST", "/admin/faults/clear")
h = wait_for(lambda: (lambda h: h if h["status"] in ("healthy", "degraded") and
                      not api("GET", "/api/state")[1]["freshness"]["stale"] else None)(api("GET", "/api/health")[1]), 40)
check("recovers after fault clears", h is not None, api("GET", "/api/state")[1]["freshness"])

# --- fault: stale data header
sim("POST", "/admin/faults", {"type": "stale_data", "duration_seconds": 15})
st = wait_for(lambda: (lambda s: s if s["freshness"]["stale"] else None)(api("GET", "/api/state")[1]))
check("stale header detected", st is not None and any("X-Simulator-Stale" in x for x in st["freshness"]["reasons"]),
      st and st["freshness"]["reasons"])
sim("POST", "/admin/faults/clear")

sim("POST", "/admin/reset"); sim("POST", "/admin/pause")
wait_for(lambda: (lambda s: not s["freshness"]["stale"] and s["tick"] == 0)(api("GET", "/api/state")[1]), 20)

# --- decision review: register a recommendation, approve it, see it submitted and in history
tick = api("GET", "/api/state")[1]["tick"]
rec = json.loads(open(os.path.join(os.path.dirname(__file__), "..", "fixtures", "recommendation.json")).read())
rec.update(id=f"smoke-rec-{int(time.time())}", tick=tick)
code, d = api("POST", "/api/decisions", {"recommendation": rec})
check("decision registered", code == 201 and d["stage"] == "projected", d)
code, d = api("POST", f"/api/decisions/{rec['id']}/approve", {"by": "smoke", "reason": "smoke test"})
check("approve submits allocations", code == 200 and d["stage"] == "submitted"
      and d["submissions"][0]["result"] == "accepted", d)
code, d = api("POST", f"/api/decisions/{rec['id']}/approve", {"by": "smoke"})
check("double approve refused", code == 409, code)
check("decision in history", any(x["decision_id"] == rec["id"] for x in api("GET", "/api/decisions")[1]))

# --- pacer steps the simulator for us
t0 = api("GET", "/api/state")[1]["tick"]
api("POST", "/api/pacer", {"enabled": True, "interval_ms": 200, "max_ticks": 5})
st = wait_for(lambda: (lambda s: s if s["tick"] >= t0 + 5 else None)(api("GET", "/api/state")[1]), 15)
check("pacer advanced 5 ticks", st is not None, api("GET", "/api/state")[1]["tick"])
api("POST", "/api/pacer", {"enabled": False})

# --- chaos proxy
code, _ = api("POST", "/api/chaos/events", {"type": "demand_spike", "start_in_ticks": 1, "duration_ticks": 4,
                                            "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.5}})
check("chaos event injected through backend", code == 201, code)

# --- database outage: operations continue, records buffer, then flush
if args.docker:
    import subprocess
    subprocess.run(["docker", "compose", "stop", "postgres"], check=True, capture_output=True)
    comps = wait_for(lambda: (lambda h: h if any(c["name"] == "Database" and c["status"] == "down"
                                                 for c in h["components"]) else None)(api("GET", "/api/health")[1]), 30)
    check("health shows database down", comps is not None)
    rec.update(id=rec["id"] + "-dbdown")
    code, d = api("POST", "/api/decisions", {"recommendation": rec})
    check("decisions still accepted while database is down", code == 201, d)
    subprocess.run(["docker", "compose", "start", "postgres"], check=True, capture_output=True)
    ok = wait_for(lambda: any(c["name"] == "Database" and c["status"] == "healthy"
                              for c in api("GET", "/api/health")[1]["components"]), 60, 1)
    check("database reconnects", ok)
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "fuelguard", "-tAc",
                          f"select count(*) from decisions where decision_id = '{rec['id']}'"],
                         capture_output=True, text=True)
    check("buffered decision flushed to postgres", out.stdout.strip() == "1", out.stdout + out.stderr)

text = urllib.request.urlopen(args.backend + "/metrics").read().decode()
check("metrics exported", "fuelguard_sim_requests_total" in text and "fuelguard_decisions_total" in text)

sim("POST", "/admin/reset"); sim("POST", "/admin/pause")
print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: {failures}'}")
sys.exit(1 if failures else 0)
