"""FastAPI endpoints for FuelGuard Reinforcement Learning recommendations and telemetry."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.contracts import NetworkSnapshot
from app.rl.inference.predictor import get_rl_predictor

router = APIRouter(prefix="/api/rl", tags=["RL"])


class RecommendRequest(BaseModel):
    snapshot: NetworkSnapshot | None = Field(None, description="Optional custom snapshot to evaluate")
    use_latest_state: bool = Field(True, description="Whether to use the state store's latest snapshot")


def _services(request: Request):
    svc = getattr(request.app.state, "services", None)
    if svc is None:
        from app.config import get_settings
        from app.main import build_services
        request.app.state.services = build_services(get_settings())
        return request.app.state.services
    return svc


@router.post("/recommend", summary="Generate a safe RL dispatch recommendation from current network state")
def get_rl_recommendation(request: Request, body: RecommendRequest | None = None) -> dict[str, Any]:
    predictor = get_rl_predictor()
    svc = _services(request)

    # 1. Acquire Snapshot
    snapshot: NetworkSnapshot | None = None
    if body and body.snapshot is not None:
        snapshot = body.snapshot
    elif svc and svc.store and svc.store.snapshot:
        snapshot = svc.store.snapshot
    else:
        # Fallback to default snapshot from environment if simulator has not synced yet
        from app.rl.environment.fuel_env import _create_default_snapshot
        snapshot = _create_default_snapshot()

    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "STATE_UNAVAILABLE", "message": "No simulator state snapshot available."},
        )

    # 2. Check if Snapshot is Stale or Circuit Open
    if snapshot.is_stale:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "STALE_DATA_LOCK",
                "message": "Simulator data is stale or circuit open. RL autonomous allocations locked.",
            },
        )

    # 3. Predict & Validate Action
    res = predictor.predict_recommendation(snapshot, forecasts=svc.demand_cache if svc else None)

    # 4. Enforce: "The RL agent must NOT directly bypass existing safety checks... Do not automatically execute"
    rec_payload = res.get("recommendation")
    if rec_payload is None:
        return {
            "recommendation": None,
            "model": res.get("model", {"name": "fuel_ppo", "version": "v1"}),
            "confidence": res.get("confidence", 0.5),
            "status": "rejected_by_guardrails",
            "reason": res.get("rejection_reason", "Action violates safety constraints"),
            "latency_ms": res.get("latency_ms", 0.0),
        }

    return {
        "recommendation": {
            "source_depot": rec_payload["source_depot"],
            "destination_station": rec_payload["destination_station"],
            "fuel_type": rec_payload["fuel_type"],
            "quantity": rec_payload["quantity"],
            "route": rec_payload["route"],
        },
        "model": res.get("model", {"name": "fuel_ppo", "version": "v1"}),
        "confidence": res.get("confidence", 0.85),
        "status": "pending_human_review",
        "latency_ms": res.get("latency_ms", 0.0),
    }


@router.get("/stats", summary="Get RL policy model metadata, health, and configuration")
def get_rl_stats(request: Request) -> dict[str, Any]:
    predictor = get_rl_predictor()
    health = predictor.health()
    return {
        "model_name": predictor.model_name,
        "model_version": predictor.model_version,
        "loaded": predictor.loaded,
        "status": health.status,
        "detail": health.detail,
        "observation_dim": predictor.state_extractor.dim,
        "model_path": str(predictor.model_path),
    }
