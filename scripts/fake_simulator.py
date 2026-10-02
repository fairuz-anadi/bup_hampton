"""Tiny stand-in for the organizer simulator, for UI work on machines without Docker.

NOT the official simulator and not used for any result we report. It serves fixtures/simulator_tick0.json
over the same /v1/* paths, runs a crude demand model on /admin/step, moves allocations through
PENDING -> IN_TRANSIT -> ARRIVED, applies the six event types from /admin/events and the five fault types
from /admin/faults (integration guide §7.7-7.11). Anything that matters (Twin accuracy, Gauntlet scores,
load tests) must run against the real image.

    python scripts/fake_simulator.py            # http://localhost:8000, paused
    curl -X POST localhost:8000/admin/run       # 8 ticks/s;  /admin/pause, /admin/toggle, /admin/step, /admin/reset
    curl -X POST localhost:8000/admin/events -H 'content-type: application/json' \
         -d '{"type":"demand_spike","start_tick":5,"duration_ticks":16,
              "parameters":{"region_ids":["region-dhaka"],"multiplier":1.8}}'
    curl -X POST localhost:8000/admin/faults -H 'content-type: application/json' \
         -d '{"type":"stale_data","duration_seconds":30}'
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

_fixture_candidates = [
    Path(__file__).resolve().parents[1] / "fixtures" / "simulator_tick0.json",
    Path(__file__).resolve().parents[2] / "fixtures" / "simulator_tick0.json",
    Path("/app/fixtures/simulator_tick0.json"),
]
FIXTURE = next((p for p in _fixture_candidates if p.is_file()), _fixture_candidates[0])
BASE_RATE = {"urban_high": 55.0, "industrial": 45.0, "highway": 40.0, "regional": 25.0}  # L/tick per fuel
FUEL_SHARE = {"DIESEL": 1.0, "PETROL": 0.8, "OCTANE": 0.45}
TICKS_PER_SECOND = 8
EVENT_TYPES = {
    "demand_spike",
    "route_disruption",
    "station_outage",
    "depot_constraint",
    "shipment_delay",
    "supply_shortfall",
}
FAULT_TYPES = {"latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"}

app = FastAPI(title="FuelGuard fake simulator (dev only)")
W: dict = {}
history: list[dict] = []
faults: list[dict] = []
running: dict = {"task": None}


def reset() -> None:
    W.clear()
    W.update(copy.deepcopy(json.loads(FIXTURE.read_text())))
    W["base_multiplier"] = {s["id"]: s["demand_multiplier"] for s in W["stations"]}
    history.clear()
    faults.clear()


def sim_time(tick: int) -> str:
    return (datetime.fromisoformat("2026-01-01T00:00:00") + timedelta(minutes=15 * tick)).isoformat()


def _in(e: dict, key: str, value: str) -> bool:
    ids = e["parameters"].get(key)
    return not ids or value in ids


def _active_faults() -> list[dict]:
    now = time.time()
    for f in faults:
        if f["active"] and now >= f["_end"]:
            f["active"] = False
    return [f for f in faults if f["active"]]


def _one_shot(e: dict) -> None:
    """shipment_delay and supply_shortfall act once when they start (guide §7.8)."""
    p = e["parameters"]
    for a in W["supply_arrivals"]:
        if (
            a["status"] not in ("SCHEDULED", "DELAYED")
            or not _in(e, "depot_ids", a["depot_id"])
            or not _in(e, "fuel_types", a["fuel_type"])
        ):
            continue
        if e["type"] == "shipment_delay":
            a["planned_tick"] += p.get("delay_ticks", 2)
            a["status"] = "DELAYED"
        else:
            a["quantity"] = round(a["quantity"] * p.get("factor", 0.5), 3)


def step() -> None:
    tick = W["instance"]["tick"] + 1
    W["instance"]["tick"], W["instance"]["sim_time"] = tick, sim_time(tick)
    for e in W["events"]:
        before = e["status"]
        e["status"] = (
            "ACTIVE"
            if e["start_tick"] <= tick < e["end_tick"]
            else "RESOLVED"
            if tick >= e["end_tick"]
            else "SCHEDULED"
        )
        if before == "SCHEDULED" and e["status"] != "SCHEDULED" and e["type"] in ("shipment_delay", "supply_shortfall"):
            _one_shot(e)
    active = [e for e in W["events"] if e["status"] == "ACTIVE"]
    for r in W["routes"]:
        hit = any(e["type"] == "route_disruption" and _in(e, "route_ids", r["id"]) for e in active)
        r["status"] = "DISRUPTED" if hit else "AVAILABLE"
    for d in W["depots"]:
        hit = any(e["type"] == "depot_constraint" and _in(e, "depot_ids", d["id"]) for e in active)
        d["status"] = "CONSTRAINED" if hit else "OPEN"
    regions = {r["id"]: r for r in W["regions"]}
    m = W["metrics"]
    for s in W["stations"]:
        out = any(e["type"] == "station_outage" and _in(e, "station_ids", s["id"]) for e in active)
        s["status"] = "OUTAGE" if out else "OPEN"
        spikes = [
            e["parameters"].get("multiplier", 1.5)
            for e in active
            if e["type"] == "demand_spike" and _in(e, "region_ids", s["region_id"]) and _in(e, "station_ids", s["id"])
        ]
        s["demand_multiplier"] = W["base_multiplier"][s["id"]] * (max(spikes) if spikes else 1.0)
        hour = (tick * 15 / 60) % 24
        daily = 0.6 + 0.7 * max(0.0, math.sin((hour - 5) / 24 * 2 * math.pi))
        for fuel in ("DIESEL", "PETROL", "OCTANE"):
            demand = (
                BASE_RATE.get(s["demand_profile"], 40)
                * FUEL_SHARE[fuel]
                * daily
                * s["demand_multiplier"]
                * regions[s["region_id"]]["demand_factor"]
            )
            served = 0.0 if out else min(demand, s["inventory"][fuel])
            s["inventory"][fuel] = round(s["inventory"][fuel] - served, 3)
            m["served_demand_liters"] += served
            m["unmet_demand_liters"] += demand - served
            history.append(
                {
                    "id": len(history) + 1,
                    "station_id": s["id"],
                    "fuel_type": fuel,
                    "tick": tick,
                    "sim_time": sim_time(tick),
                    "demand_liters": round(demand, 3),
                    "served_liters": round(served, 3),
                    "unmet_liters": round(demand - served, 3),
                }
            )
    total = m["served_demand_liters"] + m["unmet_demand_liters"]
    m["service_level"] = round(m["served_demand_liters"] / total, 6) if total else 1.0
    depots = {d["id"]: d for d in W["depots"]}
    for a in W["supply_arrivals"]:
        if a["status"] in ("SCHEDULED", "DELAYED") and a["planned_tick"] <= tick:
            d = depots[a["depot_id"]]
            d["inventory"][a["fuel_type"]] = min(
                d["capacity"][a["fuel_type"]], d["inventory"][a["fuel_type"]] + a["quantity"]
            )
            a["status"], a["actual_tick"] = "ARRIVED", tick
    stations = {s["id"]: s for s in W["stations"]}
    routes = {r["id"]: r for r in W["routes"]}
    for a in W["allocations"]:
        if a["status"] == "PENDING":
            if routes[a["route_id"]]["status"] == "DISRUPTED":
                a["status"], a["failure_reason"] = "FAILED", "ROUTE_UNAVAILABLE"
                m["allocation_failures"] += 1
            else:
                a["status"], a["departure_tick"] = "IN_TRANSIT", a["created_tick"]
        elif a["status"] == "IN_TRANSIT" and tick >= a["expected_arrival_tick"]:
            st = stations[a["destination_station_id"]]
            st["inventory"][a["fuel_type"]] = min(
                st["capacity"][a["fuel_type"]], st["inventory"][a["fuel_type"]] + a["quantity"]
            )
            a["status"], a["actual_arrival_tick"] = "ARRIVED", tick


@app.middleware("http")
async def inject_faults(request: Request, call_next):
    """Faults hit /v1/* only, never /v1/health or /admin/* (guide §7.10)."""
    path = request.url.path
    if not path.startswith("/v1/") or path == "/v1/health":
        return await call_next(request)
    active = {f["type"]: f for f in _active_faults()}
    if "latency" in active:
        await asyncio.sleep(active["latency"]["parameters"].get("delay_ms", 500) / 1000)
    if path == "/v1/stream" and "stream_disconnect" in active:
        return JSONResponse({"detail": {"code": "FAULT_INJECTED"}}, 503)
    if "unavailable" in active:
        return JSONResponse(
            {"error": {"code": "FAULT_INJECTED", "message": "Simulator API temporarily unavailable."}}, 503
        )
    if "error_rate" in active and random.random() < active["error_rate"]["parameters"].get("rate", 0.25):
        return JSONResponse({"error": {"code": "FAULT_INJECTED", "message": "Injected transient API error."}}, 503)
    response = await call_next(request)
    if "stale_data" in active and request.method == "GET":
        response.headers["X-Simulator-Stale"] = "true"
    return response


@app.get("/v1/health")
def health():
    return {"status": "ok", "note": "fake simulator (dev only)"}


@app.get("/v1/{resource}")
def read(resource: str, request: Request):
    if resource == "demand-history":
        sid = request.query_params.get("station_id")
        limit = max(1, min(2000, int(request.query_params.get("limit", 200))))
        rows = [h for h in reversed(history) if not sid or h["station_id"] == sid]
        return rows[:limit]
    if resource == "stream":
        raise HTTPException(404, {"code": "NOT_SUPPORTED", "message": "fake simulator has no SSE; poll REST"})
    key = resource.replace("-", "_")
    if key not in W:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": resource})
    if key == "events":
        return sorted(W["events"], key=lambda e: -e["id"])
    return W[key]


@app.post("/v1/allocations", status_code=201)
async def allocate(request: Request):
    b = await request.json()
    for a in W["allocations"]:
        if a["idempotency_key"] == b["idempotency_key"]:
            return a
    route = next((r for r in W["routes"] if r["id"] == b["route_id"]), None)
    depot = next((d for d in W["depots"] if d["id"] == b["source_depot_id"]), None)
    station = next((s for s in W["stations"] if s["id"] == b["destination_station_id"]), None)
    if route is None or depot is None or station is None:
        return JSONResponse({"detail": {"code": "NOT_FOUND", "message": "route, depot or station"}}, 404)
    if route["status"] == "DISRUPTED":
        return JSONResponse({"detail": {"code": "ROUTE_DISRUPTED", "message": "route is disrupted"}}, 409)
    if station["status"] != "OPEN":
        return JSONResponse({"detail": {"code": "STATION_CLOSED", "message": "station is in outage"}}, 409)
    if depot["inventory"][b["fuel_type"]] < b["quantity"]:
        return JSONResponse({"detail": {"code": "INSUFFICIENT_DEPOT_INVENTORY", "message": "not enough fuel"}}, 409)
    depot["inventory"][b["fuel_type"]] -= b["quantity"]
    tick = W["instance"]["tick"]
    a = {
        "id": len(W["allocations"]) + 1,
        "idempotency_key": b["idempotency_key"],
        "source_depot_id": b["source_depot_id"],
        "destination_station_id": b["destination_station_id"],
        "route_id": b["route_id"],
        "fuel_type": b["fuel_type"],
        "quantity": b["quantity"],
        "created_tick": tick,
        "departure_tick": None,
        "expected_arrival_tick": tick + route["transit_ticks"],
        "actual_arrival_tick": None,
        "status": "PENDING",
        "failure_reason": None,
    }
    W["allocations"].insert(0, a)
    W["metrics"]["allocation_liters"] += b["quantity"]
    return a


@app.post("/v1/allocations/{allocation_id}/cancel")
def cancel(allocation_id: int):
    a = next((x for x in W["allocations"] if x["id"] == allocation_id), None)
    if a is None:
        return JSONResponse({"detail": {"code": "NOT_FOUND", "message": str(allocation_id)}}, 404)
    if a["status"] != "PENDING":
        return JSONResponse({"detail": {"code": "NOT_CANCELLABLE", "message": a["status"]}}, 409)
    depot = next(d for d in W["depots"] if d["id"] == a["source_depot_id"])
    depot["inventory"][a["fuel_type"]] += a["quantity"]
    a["status"] = "CANCELLED"
    return a


@app.post("/admin/step")
def admin_step(ticks: int = 1):
    for _ in range(max(1, min(ticks, 500))):
        step()
    return {"tick": W["instance"]["tick"], "sim_time": W["instance"]["sim_time"]}


@app.post("/admin/reset")
async def admin_reset():
    await admin_pause()
    reset()
    return {"status": "reset"}


@app.post("/admin/events", status_code=201)
async def admin_event(request: Request):
    b = await request.json()
    if b.get("type") not in EVENT_TYPES or int(b.get("duration_ticks", 0)) <= 0:
        raise HTTPException(422, {"code": "INVALID_EVENT", "message": "type or duration_ticks"})
    e = {
        "id": len(W["events"]) + 1,
        "type": b["type"],
        "start_tick": b["start_tick"],
        "end_tick": b["start_tick"] + b["duration_ticks"],
        "status": "SCHEDULED",
        "parameters": b.get("parameters") or {},
    }
    W["events"].append(e)
    return e


@app.get("/admin/events")
def admin_events():
    return sorted(W["events"], key=lambda e: -e["id"])[:50]


@app.post("/admin/faults", status_code=201)
async def admin_fault(request: Request):
    b = await request.json()
    dur = int(b.get("duration_seconds", 0))
    if b.get("type") not in FAULT_TYPES or not 0 < dur <= 3600:
        raise HTTPException(422, {"code": "INVALID_FAULT", "message": "type or duration_seconds"})
    now = time.time()
    f = {
        "id": len(faults) + 1,
        "type": b["type"],
        "duration_seconds": dur,
        "parameters": b.get("parameters") or {},
        "start_wall_time": datetime.fromtimestamp(now).isoformat(),
        "end_wall_time": datetime.fromtimestamp(now + dur).isoformat(),
        "active": True,
        "_end": now + dur,
    }
    faults.append(f)
    return {k: v for k, v in f.items() if not k.startswith("_")}


@app.get("/admin/faults")
def admin_faults():
    _active_faults()
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in sorted(faults, key=lambda f: -f["id"])[:50]]


@app.post("/admin/faults/clear")
def admin_faults_clear():
    for f in faults:
        f["active"] = False
    return {"status": "cleared"}


async def _loop():
    while True:
        step()
        await asyncio.sleep(1 / TICKS_PER_SECOND)


@app.post("/admin/run")
async def admin_run():
    if running["task"] is None:
        running["task"] = asyncio.create_task(_loop())
    W["instance"]["status"] = "RUNNING"
    return W["instance"]


@app.post("/admin/pause")
async def admin_pause():
    if running["task"] is not None:
        running["task"].cancel()
        running["task"] = None
    if W:
        W["instance"]["status"] = "PAUSED"
    return W.get("instance", {})


@app.post("/admin/toggle")
async def admin_toggle():
    return await (admin_pause() if running["task"] is not None else admin_run())


reset()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
