"""Explore the BUP fuel simulator: allocation lifecycle, do-nothing baseline, simple rule policy."""
import json, sys, time, urllib.request, urllib.error

B = "http://localhost:8000"
TICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 288  # 288 ticks = 3 sim days


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


get = lambda p: req("GET", p)[1]
post = lambda p, b=None: req("POST", p, b)


def reset():
    post("/admin/pause"); post("/admin/reset"); post("/admin/pause")


def stockouts():
    """(station, fuel) pairs at zero inventory right now."""
    return [(s["id"], f) for s in get("/v1/stations") for f, v in s["inventory"].items() if v <= 1]


def run(policy, name):
    reset()
    first_stockout = None
    errors = {}
    t0 = time.time()
    for t in range(TICKS):
        if policy:
            for code in policy(t):
                errors[code] = errors.get(code, 0) + 1
        post("/admin/step")
        if first_stockout is None:
            so = stockouts()
            if so:
                first_stockout = (t + 1, so)
    m = get("/v1/metrics")
    depots = {d["id"]: d["inventory"] for d in get("/v1/depots")}
    stations = {s["id"]: s["inventory"] for s in get("/v1/stations")}
    print(f"\n=== {name}  ({TICKS} ticks, {time.time()-t0:.1f}s wall) ===")
    print("metrics:", json.dumps(m))
    print("first stockout:", first_stockout)
    print("allocation error codes:", errors)
    print("final depots:", json.dumps(depots))
    print("final stations:", json.dumps(stations))
    # unmet demand by station/fuel from demand history
    hist = get("/v1/demand-history?limit=2000")
    return m


# ---------- 1. allocation lifecycle ----------
reset()
code, a = post("/v1/allocations", {"idempotency_key": "explore-001", "source_depot_id": "depot-gazipur",
                                   "destination_station_id": "station-mirpur", "route_id": "route-gazipur-mirpur",
                                   "fuel_type": "DIESEL", "quantity": 3000})
print("POST allocation ->", code, a["status"] if isinstance(a, dict) else a)
print("replay same key ->", post("/v1/allocations", {"idempotency_key": "explore-001", "source_depot_id": "depot-gazipur",
      "destination_station_id": "station-mirpur", "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 3000})[0])
print("same key, diff body ->", post("/v1/allocations", {"idempotency_key": "explore-001", "source_depot_id": "depot-gazipur",
      "destination_station_id": "station-mirpur", "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 100}))
print("too big ->", post("/v1/allocations", {"idempotency_key": "explore-002", "source_depot_id": "depot-gazipur",
      "destination_station_id": "station-mirpur", "route_id": "route-gazipur-mirpur", "fuel_type": "DIESEL", "quantity": 8000}))
depot_before = get("/v1/depots/depot-gazipur")["inventory"]["DIESEL"]
for i in range(4):
    post("/admin/step")
    al = get("/v1/allocations")[0]
    st = get("/v1/stations/station-mirpur")["inventory"]["DIESEL"]
    print(f"  after step {i+1}: alloc={al['status']} dep={al['departure_tick']} eta={al['expected_arrival_tick']} "
          f"arr={al['actual_arrival_tick']} mirpur_diesel={st:.0f}")
print("gazipur diesel after POST:", depot_before)
dh = get("/v1/demand-history?station_id=station-mirpur&limit=12")
print("demand-history sample:", json.dumps(dh[:3]))

# ---------- 2. do nothing ----------
run(None, "BASELINE: do nothing")


# ---------- 3. simple reorder rule ----------
ROUTES = {"station-mirpur": ("depot-gazipur", "route-gazipur-mirpur"),
          "station-tongi": ("depot-gazipur", "route-gazipur-tongi"),
          "station-karnaphuli": ("depot-patiya", "route-patiya-karnaphuli"),
          "station-coxsbazar": ("depot-patiya", "route-patiya-coxsbazar")}
key_n = [0]


def simple_rule(t):
    """Every 4 ticks: if a station fuel is below 40% of capacity, send up to the gap (in-flight aware)."""
    if t % 4:
        return []
    codes = []
    inflight = {}
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
                qty = min(cap * 0.9 - pos, 6000)
                key_n[0] += 1
                code, body = post("/v1/allocations", {"idempotency_key": f"rule-{key_n[0]}", "source_depot_id": depot,
                                  "destination_station_id": s["id"], "route_id": route, "fuel_type": f,
                                  "quantity": round(qty)})
                codes.append(str(code) if code == 201 else body["detail"]["code"])
    return codes


run(simple_rule, "SIMPLE RULE: refill below 40%")
reset()
