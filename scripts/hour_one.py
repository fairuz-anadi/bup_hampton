"""Answers the four "verify in hour one" questions from the blueprint against a live simulator.

Run with the simulator up:  python scripts/hour_one.py
It resets the simulator several times, so don't run it during a demo.
"""
import json
import urllib.error
import urllib.request

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


def reset():
    post("/admin/pause"); post("/admin/reset"); post("/admin/pause")


def alloc(key, route, depot, station, fuel, qty):
    return post("/v1/allocations", {"idempotency_key": key, "source_depot_id": depot, "destination_station_id": station,
                                    "route_id": route, "fuel_type": fuel, "quantity": qty})


def inv(kind, id_, fuel):
    return get(f"/v1/{kind}/{id_}")["inventory"][fuel]


# Q1: shipment arrives at a tank that has filled up in the meantime
reset()
code1, a1 = alloc("q1-a", "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 5000)   # 9000 -> 14000 of 15000
code2, a2 = alloc("q1-b", "route-patiya-mirpur", "depot-patiya", "station-mirpur", "DIESEL", 1500)     # capacity check ignores in-transit?
print(f"Q1 in-flight overfill: first={code1}, second={code2} {a2.get('detail', {}).get('code', '') if code2 != 201 else ''}")
for _ in range(6):
    post("/admin/step")
print(f"   mirpur diesel after both arrive: {inv('stations', 'station-mirpur', 'DIESEL'):.0f} / 15000 capacity")
print("   allocations:", [(a["idempotency_key"], a["status"], a["failure_reason"]) for a in get("/v1/allocations")])

# Q2: does a FAILED allocation refund depot stock?
reset()
before = inv("depots", "depot-gazipur", "PETROL")
code, a = alloc("q2", "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 3000)
after_post = inv("depots", "depot-gazipur", "PETROL")
post("/admin/events", {"type": "route_disruption", "start_tick": 0, "duration_ticks": 5, "parameters": {"route_ids": ["route-gazipur-mirpur"]}})
post("/admin/step")
a = get("/v1/allocations")[0]
print(f"Q2 FAILED refund: depot {before:.0f} -> {after_post:.0f} after POST -> {inv('depots', 'depot-gazipur', 'PETROL'):.0f} after "
      f"status={a['status']} reason={a['failure_reason']}")

# Q3: does CONSTRAINED lower dispatch capacity?
reset()
post("/admin/events", {"type": "depot_constraint", "start_tick": 0, "duration_ticks": 10, "parameters": {"depot_ids": ["depot-gazipur"]}})
post("/admin/step")
d = get("/v1/depots/depot-gazipur")
print(f"Q3 CONSTRAINED: status={d['status']} dispatch_capacity_per_tick={d['dispatch_capacity_per_tick']}")
codes = [alloc(f"q3-{i}", "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 1000)[0] for i in range(4)]
codes += [alloc(f"q3-t{i}", "route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 1000)[0] for i in range(5)]
print(f"   9 x 1000 L in one tick while constrained -> {codes}")
codes = [alloc(f"q3-k{i}", "route-gazipur-karnaphuli", "depot-gazipur", "station-karnaphuli", "OCTANE", 1000)
         for i in range(4)]
print(f"   pushing past 12000 L -> {[c if c == 201 else b['detail']['code'] for c, b in codes]}")

# Q4: idempotent replay status code
reset()
body = ("q4", "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "OCTANE", 500)
print(f"Q4 idempotent replay: first={alloc(*body)[0]}, replay={alloc(*body)[0]}")

# Bonus: when does PENDING depart, and is dispatch capacity per tick or rolling?
reset()
alloc("b1", "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "PETROL", 5000)
print("Bonus: status right after POST:", get("/v1/allocations")[0]["status"])
post("/admin/step")
a = get("/v1/allocations")[0]
print(f"   after 1 step: status={a['status']} created={a['created_tick']} departure={a['departure_tick']} eta={a['expected_arrival_tick']}")
reset()
