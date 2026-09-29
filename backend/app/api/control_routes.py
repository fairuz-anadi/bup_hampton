"""Decisions (human review + audit), Chaos Lab, pacer and policy switch."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.auth import require_operator
from app.contracts import (
    ChaosEventRequest,
    ChaosFaultRequest,
    CreateDecisionRequest,
    DecisionRecord,
    PacerRequest,
    PolicyRequest,
    ReviewRequest,
)
from app.decisions.service import DecisionError
from app.obs.logging import log_event
from app.sim.errors import SimulatorAPIError, SimulatorError

router = APIRouter(prefix="/api")
operator = [Depends(require_operator)]


def _svc(request: Request):
    return request.app.state.services


def _raise(exc: Exception):
    if isinstance(exc, DecisionError):
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
    if isinstance(exc, SimulatorAPIError):
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                        {"code": "SIMULATOR_UNAVAILABLE", "message": str(exc)[:200]}) from exc


# ------------------------------------------------------------------ decisions

@router.get("/decisions", response_model=list[DecisionRecord], summary="Decision history, newest first")
def list_decisions(request: Request, limit: int = Query(50, ge=1, le=500),
                   stage: str | None = Query(None, max_length=20, pattern=r"^[a-z]+$")):
    return _svc(request).decisions.repo.list(limit, stage)


@router.get("/decisions/{decision_id}", response_model=DecisionRecord)
def get_decision(decision_id: str, request: Request):
    rec = _svc(request).decisions.repo.get(decision_id)
    if rec is None:
        raise HTTPException(404, {"code": "DECISION_NOT_FOUND", "message": decision_id})
    return rec


@router.post("/decisions", response_model=DecisionRecord, dependencies=operator, status_code=201,
             summary="Register a recommendation for review (intelligence lane)")
async def create_decision(body: CreateDecisionRequest, request: Request):
    try:
        return await _svc(request).decisions.create(body.recommendation, body.gate, body.mode)
    except DecisionError as exc:
        _raise(exc)


@router.post("/decisions/{decision_id}/approve", response_model=DecisionRecord, dependencies=operator,
             summary="Approve (optionally with modified legs); posts the allocations")
async def approve_decision(decision_id: str, body: ReviewRequest, request: Request):
    try:
        return await _svc(request).decisions.approve(decision_id, body.by, body.reason, body.legs)
    except (DecisionError, SimulatorError) as exc:
        _raise(exc)


@router.post("/decisions/{decision_id}/reject", response_model=DecisionRecord, dependencies=operator)
async def reject_decision(decision_id: str, body: ReviewRequest, request: Request):
    if not body.reason:
        raise HTTPException(422, {"code": "REASON_REQUIRED", "message": "A rejection needs a reason."})
    try:
        return await _svc(request).decisions.reject(decision_id, body.by, body.reason)
    except DecisionError as exc:
        _raise(exc)


# ------------------------------------------------------------------ Chaos Lab (proxies /admin/*)

@router.post("/chaos/events", dependencies=operator, status_code=201, summary="Inject a crisis event")
async def chaos_event(body: ChaosEventRequest, request: Request):
    svc = _svc(request)
    snap = svc.store.snapshot
    start = body.start_tick if body.start_tick is not None else (snap.tick if snap else 0) + body.start_in_ticks
    payload = {"type": body.type, "start_tick": start, "duration_ticks": body.duration_ticks,
               "parameters": body.parameters}
    try:
        result = await svc.client.admin("POST", "/admin/events", payload)
    except SimulatorError as exc:
        _raise(exc)
    log_event("chaos.event_injected", **payload)
    return result


@router.post("/chaos/faults", dependencies=operator, status_code=201, summary="Inject a simulator API fault")
async def chaos_fault(body: ChaosFaultRequest, request: Request):
    try:
        result = await _svc(request).client.admin("POST", "/admin/faults", body.model_dump())
    except SimulatorError as exc:
        _raise(exc)
    log_event("chaos.fault_injected", type=body.type, duration_seconds=body.duration_seconds)
    return result


@router.post("/chaos/faults/clear", dependencies=operator)
async def chaos_clear(request: Request):
    try:
        result = await _svc(request).client.admin("POST", "/admin/faults/clear")
    except SimulatorError as exc:
        _raise(exc)
    log_event("chaos.faults_cleared")
    return result


@router.get("/chaos/timeline", summary="Recent events and faults (organizer view)")
async def chaos_timeline(request: Request):
    client = _svc(request).client
    try:
        return {"events": await client.admin("GET", "/admin/events"),
                "faults": await client.admin("GET", "/admin/faults")}
    except SimulatorError as exc:
        _raise(exc)


@router.post("/chaos/sim/{action}", dependencies=operator, summary="pause | run | toggle | step | reset")
async def chaos_sim(action: str, request: Request):
    if action not in {"pause", "run", "toggle", "step", "reset"}:
        raise HTTPException(404, {"code": "UNKNOWN_ACTION", "message": action})
    svc = _svc(request)
    if action in {"run", "reset"}:
        await svc.pacer.stop()  # the simulator's own runner and our pacer must not both step
    try:
        result = await svc.client.admin("POST", f"/admin/{action}")
    except SimulatorError as exc:
        _raise(exc)
    log_event("chaos.sim_control", action=action)
    if action == "reset":
        svc.store.forget_recent()
    await svc.store.refresh()
    return result


@router.post("/chaos/forecaster/{action}", dependencies=operator, summary="disable | exit (forecaster chaos)")
async def chaos_forecaster(action: str, request: Request, seconds: int = Query(60, ge=1, le=600)):
    url = _svc(request).settings.forecaster_url
    if not url:
        raise HTTPException(409, {"code": "NO_FORECASTER", "message": "FORECASTER_URL is not configured."})
    if action not in {"disable", "exit"}:
        raise HTTPException(404, {"code": "UNKNOWN_ACTION", "message": action})
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            r = await http.post(f"{url.rstrip('/')}/chaos/{action}", json={"seconds": seconds})
    except httpx.HTTPError as exc:
        raise HTTPException(503, {"code": "FORECASTER_UNREACHABLE", "message": str(exc)[:200]}) from exc
    log_event("chaos.forecaster", action=action, seconds=seconds, status=r.status_code)
    return {"forecaster_status": r.status_code}


# ------------------------------------------------------------------ pacer + policy

@router.get("/pacer")
def pacer_status(request: Request):
    return _svc(request).pacer.status()


@router.post("/pacer", dependencies=operator, summary="Step the simulator at a human pace (demo mode)")
async def pacer_set(body: PacerRequest, request: Request):
    pacer = _svc(request).pacer
    try:
        if body.enabled:
            await pacer.start(body.interval_ms, body.max_ticks)
        else:
            await pacer.stop()
    except SimulatorError as exc:
        _raise(exc)
    return pacer.status()


@router.get("/policy")
def policy_status(request: Request):
    return _svc(request).policy.status()


@router.put("/policy", dependencies=operator, summary="Switch the active allocation policy")
def policy_set(body: PolicyRequest, request: Request):
    _svc(request).policy.set(body.policy, body.by, body.accept)
    return _svc(request).policy.status()


@router.post("/policy/rollback", dependencies=operator, summary="Return to the last accepted policy")
def policy_rollback(request: Request, by: str = Query("operator", max_length=60)):
    _svc(request).policy.rollback(by)
    return _svc(request).policy.status()
