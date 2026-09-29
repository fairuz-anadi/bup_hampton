"""Samprity's lane API: current recommendation + gate, autonomy, copilot, scoreboard.

Decision records, approve / reject, Chaos Lab, pacer and policy are in app/api/control_routes.py.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_operator
from app.contracts import ComponentHealth, ExplainResponse, NetworkSnapshot, Recommendation
from app.decisions.engine import scoreboard

router = APIRouter(prefix="/api")
ID = r"^[A-Za-z0-9._-]+$"


class ExplainBody(BaseModel):
    decision_id: str | None = Field(None, max_length=100, pattern=ID)
    question: str | None = Field(None, max_length=500)


class InvestigateBody(BaseModel):
    station_id: str = Field(max_length=64, pattern=r"^[a-z0-9-]+$")
    question: str | None = Field(None, max_length=500)


class SummaryBody(BaseModel):
    question: str | None = Field(None, max_length=500)


class ModeBody(BaseModel):
    mode: Literal["MANUAL", "SUPERVISED"]
    by: str = Field("operator", max_length=60)


def _svc(request: Request):
    return request.app.state.services


def _snap(request: Request) -> NetworkSnapshot:
    snap = _svc(request).store.snapshot
    if snap is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "NO_STATE_YET", "message": "No successful simulator sync yet."})
    return snap


def components(svc) -> list[ComponentHealth]:
    """Cheap health view for the confidence factor (no network calls, unlike /api/health)."""
    snap = svc.store.snapshot
    circuit = svc.client.breaker.state
    return [
        ComponentHealth(name="Simulator", status="healthy" if circuit == "CLOSED" else
                        "degraded" if circuit == "HALF_OPEN" else "down"),
        ComponentHealth(name="Operational state", status="degraded" if snap is None or snap.is_stale else "healthy"),
        ComponentHealth(name="Event stream", status="healthy" if svc.sync.sse_connected or not svc.settings.sse_enabled
                        else "degraded"),
        svc.engine.health(),
    ]


def demand_history(svc) -> list | None:
    data = svc.demand_cache.get("data")
    return [d.model_dump(mode="json") for d in data] if data else None


def _current_rec(svc) -> tuple[Recommendation | None, dict | None]:
    cur = svc.engine.current
    if cur and cur.get("recommendation"):
        return Recommendation.model_validate(cur["recommendation"]), cur.get("gate")
    return None, None


@router.get("/recommendations/current", summary="Recommendation for the current tick, with gate and autonomy")
async def current_recommendation(request: Request) -> dict:
    svc = _svc(request)
    snap = _snap(request)
    return await svc.engine.cycle(snap, components(svc), demand_history(svc), svc.policy.active)


# ------------------------------------------------------------------ copilot (read-only)

@router.post("/explain", response_model=ExplainResponse,
             summary="Explain a decision (the current one when decision_id is omitted)")
async def explain(body: ExplainBody, request: Request) -> ExplainResponse:
    svc = _svc(request)
    snap = svc.store.snapshot
    if body.decision_id:
        record = svc.decisions.repo.get(body.decision_id)
        if record is not None:
            rec, gate = record.recommendation, record.gate
        else:
            rec, gate = _current_rec(svc)
            if rec is None or rec.id != body.decision_id:
                raise HTTPException(404, {"code": "DECISION_NOT_FOUND", "message": body.decision_id})
    else:
        rec, gate = _current_rec(svc)
    if rec is None:
        return await run_in_threadpool(svc.explainer.summarize, _snap(request), body.question)
    return await run_in_threadpool(svc.explainer.explain, rec, snap, body.question, gate)


@router.post("/copilot/investigate", response_model=ExplainResponse, summary="Ask about one station")
async def investigate(body: InvestigateBody, request: Request) -> ExplainResponse:
    svc = _svc(request)
    rec, _ = _current_rec(svc)
    out = await run_in_threadpool(svc.explainer.investigate, _snap(request), body.station_id, rec, body.question)
    if out is None:
        raise HTTPException(404, {"code": "STATION_NOT_FOUND", "message": body.station_id})
    return out


@router.post("/copilot/summary", response_model=ExplainResponse, summary="Network situation summary")
async def summary(body: SummaryBody, request: Request) -> ExplainResponse:
    return await run_in_threadpool(_svc(request).explainer.summarize, _snap(request), body.question)


@router.get("/copilot/incident-report", response_model=ExplainResponse,
            summary="Incident report for a tick window (default: the last 96 ticks)")
async def incident_report(request: Request, from_tick: int | None = Query(None, ge=0),
                          to_tick: int | None = Query(None, ge=0)) -> ExplainResponse:
    svc = _svc(request)
    snap = _snap(request)
    to_tick = snap.tick if to_tick is None else to_tick
    from_tick = max(0, to_tick - 96) if from_tick is None else from_tick
    if from_tick > to_tick:
        raise HTTPException(422, {"code": "BAD_WINDOW", "message": "from_tick must be <= to_tick"})
    records = svc.decisions.repo.list(500)
    return await run_in_threadpool(svc.explainer.incident, snap, records, from_tick, to_tick,
                                   list(svc.engine.autonomy.log))


@router.get("/copilot/info", summary="Copilot engine, model and tracing status")
def copilot_info(request: Request) -> dict:
    return _svc(request).explainer.describe()


# ------------------------------------------------------------------ autonomy + scoreboard

@router.get("/autonomy", summary="Autonomy mode, confidence factors and transition log")
def autonomy(request: Request) -> dict:
    eng = _svc(request).engine
    return eng.autonomy.view() | {"autopilot": eng.autopilot}


@router.post("/autonomy/rearm", dependencies=[Depends(require_operator)])
def rearm(request: Request, by: str = Query("operator", max_length=60)) -> dict:
    eng = _svc(request).engine
    ok, msg = eng.autonomy.rearm(by)
    if not ok:
        raise HTTPException(status.HTTP_409_CONFLICT, {"code": "REARM_REFUSED", "message": msg})
    return eng.autonomy.view() | {"autopilot": eng.autopilot}


@router.post("/autonomy/mode", dependencies=[Depends(require_operator)], summary="Step the mode down")
def set_mode(body: ModeBody, request: Request) -> dict:
    eng = _svc(request).engine
    eng.autonomy.force(body.mode, body.by)
    return eng.autonomy.view() | {"autopilot": eng.autopilot}


@router.get("/scoreboard", summary="Counterfactual scoreboard (projected) + verified Twin error")
def get_scoreboard(request: Request) -> dict:
    return scoreboard(_svc(request).decisions.repo.list(500))


@router.get("/decisions/multiagent/latest", summary="Latest Multi-Agent Decision deliberation and consensus")
def get_latest_multiagent(request: Request) -> dict:
    svc = _svc(request)
    rec, gate = _current_rec(svc)
    if rec and rec.multiagent_decision:
        return {
            "decision_id": rec.id,
            "tick": rec.tick,
            "multiagent_decision": rec.multiagent_decision.model_dump(mode="json"),
            "confidence": rec.confidence,
            "human_review_required": rec.human_review_required,
        }
    # Check decision repo
    records = svc.decisions.repo.list(20)
    for r in records:
        if r.recommendation and r.recommendation.multiagent_decision:
            return {
                "decision_id": r.decision_id,
                "tick": r.sim_tick,
                "multiagent_decision": r.recommendation.multiagent_decision.model_dump(mode="json"),
                "confidence": r.recommendation.confidence,
                "human_review_required": r.recommendation.human_review_required,
            }
    return {
        "status": "unavailable",
        "message": "No multi-agent deliberations recorded yet.",
    }

