"""Backend HTTP API (/api/*). The frontend and the intelligence lane only talk to this."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.auth import require_operator
from app.contracts import (
    Allocation,
    ComponentHealth,
    DemandObservation,
    HealthReport,
    InTransitLeg,
    NetworkSnapshot,
    SubmitAllocationsRequest,
    SubmitAllocationsResponse,
)
from app.sim.errors import SimulatorAPIError, SimulatorError

router = APIRouter(prefix="/api")


def _services(request: Request):
    return request.app.state.services


def _snapshot_or_503(request: Request) -> NetworkSnapshot:
    snap = _services(request).store.snapshot
    if snap is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "NO_STATE_YET", "message": "No successful simulator sync yet."})
    return snap


@router.get("/state", response_model=NetworkSnapshot, summary="Current network snapshot (cached, never blocks)")
def get_state(request: Request) -> NetworkSnapshot:
    return _snapshot_or_503(request)


@router.get("/state/in-transit", response_model=list[InTransitLeg])
def get_in_transit(request: Request) -> list[InTransitLeg]:
    return _snapshot_or_503(request).in_transit


@router.get("/demand-history", response_model=list[DemandObservation],
            summary="Demand observations for forecasting, newest first (proxied, cached per tick)")
async def get_demand_history(request: Request, limit: int = Query(200, ge=1, le=2000),
                             station_id: str | None = Query(None, max_length=64, pattern=r"^[a-z0-9-]+$")):
    svc = _services(request)
    snap = svc.store.snapshot
    cache_key = (snap.tick if snap else None, limit, station_id)
    if svc.demand_cache.get("key") == cache_key:
        return svc.demand_cache["data"]
    try:
        fetched = await svc.client.demand_history(limit=limit, station_id=station_id)
    except SimulatorError as exc:
        if svc.demand_cache.get("data") is not None:
            return svc.demand_cache["data"]  # degraded: last good copy
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {"code": "SIMULATOR_UNAVAILABLE",
                                                                  "message": str(exc)[:200]}) from exc
    svc.demand_cache.update(key=cache_key, data=fetched.data)
    return fetched.data


@router.post("/allocations", response_model=SubmitAllocationsResponse, dependencies=[Depends(require_operator)],
             summary="Submit approved allocation legs to the simulator")
async def submit_allocations(body: SubmitAllocationsRequest, request: Request) -> SubmitAllocationsResponse:
    return await _services(request).writer.submit(body.decision_id, body.legs)


@router.post("/allocations/{allocation_id}/cancel", response_model=Allocation,
             dependencies=[Depends(require_operator)])
async def cancel_allocation(allocation_id: int, request: Request) -> Allocation:
    try:
        return await _services(request).writer.cancel(allocation_id)
    except SimulatorAPIError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
    except SimulatorError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "SIMULATOR_UNAVAILABLE", "message": str(exc)[:200]}) from exc


@router.get("/health", response_model=HealthReport, summary="Aggregated health of every component")
async def health(request: Request) -> HealthReport:
    svc = _services(request)
    snap = svc.store.snapshot
    components = [ComponentHealth(name="Backend API", status="healthy")]

    # Simulator: /v1/health skips fault injection, so "health ok but /v1/* failing" means faulted, not down.
    try:
        await svc.client.health()
        sim_alive = True
    except SimulatorError:
        sim_alive = False
    circuit = svc.client.breaker.state
    if not sim_alive:
        components.append(ComponentHealth(name="Simulator", status="down", detail="/v1/health unreachable"))
    elif circuit != "CLOSED" or (snap and snap.freshness.stale):
        reasons = snap.freshness.reasons[:3] if snap else [f"circuit {circuit}"]
        components.append(ComponentHealth(name="Simulator", status="degraded", detail="; ".join(reasons)))
    else:
        components.append(ComponentHealth(name="Simulator", status="healthy"))

    components.append(ComponentHealth(
        name="Operational state", status="healthy" if snap and not snap.freshness.stale else
        ("degraded" if snap else "down"), detail=None if snap else "no successful sync yet"))
    components.append(ComponentHealth(
        name="Event stream", status="healthy" if svc.sync.sse_connected else "degraded",
        detail=None if svc.sync.sse_connected else "SSE down, polling REST"))
    # Forecaster, decision engine, database and explanation register themselves here as they land.
    components.extend(svc.extra_health())

    worst = "healthy"
    for c in components:
        if c.status == "down" and c.name in ("Backend API", "Simulator"):
            worst = "down"
        elif c.status in ("down", "degraded") and worst == "healthy":
            worst = "degraded"
    ages = [r.age_seconds for r in snap.freshness.resources.values() if r.age_seconds is not None] if snap else []
    return HealthReport(status=worst, components=components, tick=snap.tick if snap else None,
                        snapshot_age_seconds=max(ages) if ages else None)
