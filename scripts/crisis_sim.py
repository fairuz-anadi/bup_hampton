"""Stress the simple reorder rule: long horizon + injected crises."""
import json, urllib.request, urllib.error

B = "http://localhost:8000"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


get = lambda p: req("GET", p)[1]
post = lambda p, b=None: req("POST", p, b)

ROUTES = {"station-mirpur": ("depot-gazipur", "route-gazipur-mirpur"),
          "station-tongi": ("depot-gazipur", "route-gazipur-tongi"),
          "station-karnaphuli": ("depot-patiya", "route-patiya-karnaphuli"),
          "station-coxsbazar": ("depot-patiya", "route-patiya-coxsbazar")}
n = [0]


def simple_rule(t):
    if t % 4:
        return []
    codes, inflight = [], {}
    for a in get("/v1/allocations"):
        if a["status"] in ("PENDING", "IN_TRANSIT"):
            k = (a["destination_station_id"], a["fuel_type"])
            inflight[k] = inflight.get(k, 0) + a["quantity"]
    for s in get("/v1/stations"):
        depot, route = ROUTES[s["id"]]
        for f, inv in s["inventory"].items():
            cap = s["capacity"][f]
            pos = inv + inflight.get((s["id"], f), 0)
            if pos < 0.4 * cap:
                n[0] += 1
                code, body = post("/v1/allocations", {"idempotency_key": f"c-{n[0]}", "source_depot_id": depot,
                                  "destination_station_id": s["id"], "route_id": route, "fuel_type": f,
                                  "quantity": round(min(cap * 0.9 - pos, 6000))})
                codes.append("OK" if code == 201 else body["detail"]["code"])
    return codes


def run(name, ticks, events=()):
    post("/admin/pause"); post("/admin/reset"); post("/admin/pause")
    for e in events:
        print("  inject:", post("/admin/events", e)[0], e["type"], e.get("parameters"))
    errs, unmet_by_day = {}, []
    prev_unmet = 0
    for t in range(ticks):
        for c in simple_rule(t):
            errs[c] = errs.get(c, 0) + 1
        post("/admin/step")
        if (t + 1) % 96 == 0:
            u = get("/v1/metrics")["unmet_demand_liters"]
            unmet_by_day.append(round(u - prev_unmet)); prev_unmet = u
    m = get("/v1/metrics")
    print(f"\n=== {name} ({ticks} ticks) ===")
    print(f"service_level={m['service_level']:.4f} unmet={m['unmet_demand_liters']:.0f} L failures={m['allocation_failures']}")
    print("unmet per day:", unmet_by_day)
    print("alloc results:", errs)
    print("final depots:", {d["id"]: {k: round(v) for k, v in d["inventory"].items()} for d in get("/v1/depots")})


run("SIMPLE RULE, 6 days, no crisis", 576)
run("SIMPLE RULE, 3 days, crises", 288, [
    {"type": "demand_spike", "start_tick": 40, "duration_ticks": 48, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
    {"type": "route_disruption", "start_tick": 60, "duration_ticks": 24, "parameters": {"route_ids": ["route-gazipur-mirpur"]}},
    {"type": "supply_shortfall", "start_tick": 50, "duration_ticks": 1, "parameters": {"depot_ids": ["depot-gazipur"], "factor": 0.3}},
    {"type": "station_outage", "start_tick": 120, "duration_ticks": 16, "parameters": {"station_ids": ["station-coxsbazar"]}},
])
post("/admin/reset"); post("/admin/pause")
