"""Endpoints the Policy Gauntlet (scripts/gauntlet_official.py) needs.

`POST /api/recommendations` asks the decision engine's recommender for a stateless recommendation under a
given policy: nothing is registered, gated or executed. Results are stored with `POST /api/policy-runs`.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_operator
from app.contracts import Recommendation
from app.decisions.engine import apply_policy
from app.decisions.routes import demand_history

router = APIRouter(prefix="/api")


@router.post("/recommendations", response_model=Recommendation,
             summary="Stateless recommendation under one policy (used by the Policy Gauntlet)")
async def recommend(request: Request, policy: str | None = Query(None, max_length=40)) -> Recommendation:
    svc = request.app.state.services
    snap = svc.store.snapshot
    if snap is None:
        raise HTTPException(503, {"code": "NO_STATE", "message": "no successful sync yet"})
    if svc.engine.recommender is None:
        raise HTTPException(503, {"code": "ENGINE_UNAVAILABLE", "message": f"recommender {svc.engine.source}"})
    try:
        rec = await run_in_threadpool(svc.engine.recommender, snap, demand_history(svc))
    except Exception as exc:
        raise HTTPException(503, {"code": "ENGINE_FAILED", "message": f"{type(exc).__name__}: {exc}"[:300]}) from exc
    return apply_policy(rec, policy)


@router.get("/policy-runs", summary="Policy Gauntlet results, newest first")
def policy_runs(request: Request, limit: int = Query(50, ge=1, le=500)):
    return request.app.state.services.repo.policy_runs(limit)


@router.post("/policy-runs", status_code=201, dependencies=[Depends(require_operator)])
async def save_policy_run(request: Request, run: dict = Body(...)):
    if not isinstance(run.get("policy"), str) or not isinstance(run.get("scenario_id"), str):
        raise HTTPException(422, {"code": "BAD_RUN", "message": "policy and scenario_id are required"})
    await request.app.state.services.repo.save_policy_run(run)
    return {"saved": True}
