"""FuelGuard RL Production Inference Engine.

Loads trained PPO policies, vectorizes live simulator state snapshots, generates candidate
actions, strictly validates against deterministic guardrails, and formats recommendations
for human review gating.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
import numpy as np

from app.contracts import AllocationLeg, ComponentHealth, NetworkSnapshot
from app.obs.logging import log_event
from app.obs.metrics import FALLBACKS
from app.rl.agents.ppo_agent import PPOAgent
from app.rl.environment.action import RLActionSpace, validate_action
from app.rl.environment.state import RLStateExtractor

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
DEFAULT_MODEL_PATH = DEFAULT_MODEL_DIR / "fuel_ppo_v1.pt"
METADATA_PATH = DEFAULT_MODEL_DIR / "model_metadata.json"


class RLPredictor:
    """Production predictor wrapping trained RL policy for fast inference."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        model_name: str = "fuel_ppo",
        model_version: str = "v1",
    ):
        self.model_path = Path(model_path or DEFAULT_MODEL_PATH)
        self.model_name = model_name
        self.model_version = model_version
        self.state_extractor = RLStateExtractor()
        self.action_space = RLActionSpace()

        self._read_metadata()

        # Initialize Agent
        self.agent = PPOAgent(
            input_dim=self.state_extractor.dim,
            num_actions=73,  # default placeholder, will adjust upon load
        )
        self.loaded = False
        self._load_model_if_available()

    def _read_metadata(self) -> None:
        if METADATA_PATH.is_file():
            try:
                meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
                self.model_name = meta.get("name", self.model_name)
                self.model_version = meta.get("version", self.model_version)
            except Exception:
                pass

    def _load_model_if_available(self) -> None:
        if self.model_path.is_file():
            try:
                self.agent.load(self.model_path)
                self.loaded = True
                log_event("rl.model_loaded", path=str(self.model_path), version=self.model_version)
            except Exception as exc:
                log_event("rl.model_load_failed", error=str(exc)[:150])
                self.loaded = False
        else:
            self.loaded = False

    def predict_recommendation(
        self,
        snapshot: NetworkSnapshot,
        forecasts: dict[tuple[str, str], Any] | None = None,
    ) -> dict[str, Any]:
        """Runs policy inference, validates the candidate dispatch, and returns recommendation."""
        started = time.perf_counter()

        # If model is not loaded, try to load it once more or initialize fallback
        if not self.loaded:
            self._load_model_if_available()

        # 1. State Vector Extraction
        obs = self.state_extractor.extract(snapshot, forecasts)

        # 2. Forward pass through policy
        action_idx, confidence = self.agent.predict(obs, deterministic=True)

        # 3. Decode Action to Candidate Allocation
        candidate_leg = self.action_space.decode_action(action_idx, snapshot)

        # 4. Strict Deterministic Guardrail Validation
        is_valid, rejection_reason, valid_leg = validate_action(candidate_leg, snapshot)

        latency_ms = round((time.perf_counter() - started) * 1000, 2)

        if not is_valid:
            log_event(
                "rl.action_rejected_by_guardrails",
                reason=rejection_reason,
                action_idx=action_idx,
                latency_ms=latency_ms,
            )
            return {
                "recommendation": None,
                "model": {
                    "name": self.model_name,
                    "version": self.model_version,
                },
                "confidence": round(confidence, 2),
                "status": "rejected_by_guardrails",
                "is_valid": False,
                "rejection_reason": rejection_reason,
                "allocation_leg": None,
                "latency_ms": latency_ms,
            }

        if valid_leg is None:
            # Valid No-Op recommendation
            return {
                "recommendation": {
                    "source_depot": "",
                    "destination_station": "",
                    "fuel_type": "DIESEL",
                    "quantity": 0.0,
                    "route": "",
                },
                "model": {
                    "name": self.model_name,
                    "version": self.model_version,
                },
                "confidence": round(confidence, 2),
                "status": "pending_human_review",
                "is_valid": True,
                "rejection_reason": None,
                "allocation_leg": None,
                "latency_ms": latency_ms,
            }

        # Valid active dispatch
        rec_data = {
            "source_depot": valid_leg.source_depot_id,
            "destination_station": valid_leg.station_id,
            "fuel_type": valid_leg.fuel_type.value,
            "quantity": valid_leg.quantity,
            "route": valid_leg.route_id,
        }

        return {
            "recommendation": rec_data,
            "model": {
                "name": self.model_name,
                "version": self.model_version,
            },
            "confidence": round(confidence, 2),
            "status": "pending_human_review",
            "is_valid": True,
            "rejection_reason": None,
            "allocation_leg": valid_leg,
            "latency_ms": latency_ms,
        }

    def health(self) -> ComponentHealth:
        if self.loaded:
            return ComponentHealth(
                name="RL Policy Engine",
                status="healthy",
                detail=f"Model: {self.model_name} ({self.model_version})",
            )
        return ComponentHealth(
            name="RL Policy Engine",
            status="degraded",
            detail="Using baseline uncheckpointed weights (training recommended)",
        )


_PREDICTOR_INSTANCE: RLPredictor | None = None


def get_rl_predictor() -> RLPredictor:
    global _PREDICTOR_INSTANCE
    if _PREDICTOR_INSTANCE is None:
        _PREDICTOR_INSTANCE = RLPredictor()
    return _PREDICTOR_INSTANCE
