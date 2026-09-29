"""
FuelGuard Copilot Service (backend/app/copilot/service.py)
Unified interface for LangGraph explanation copilot and DecisionRecord generation.
"""

import os
from typing import Optional, Dict, Any
from backend.app.contracts import (
    Recommendation,
    NetworkSnapshot,
    ExplainResponse,
    DecisionRecord,
    AutonomyMode,
    RouteStatus,
    StationStatus,
)
from backend.app.copilot.graph import build_copilot_graph


class CopilotService:
    def __init__(self):
        self.app = build_copilot_graph()

    def explain(
        self,
        recommendation: Recommendation,
        snapshot: NetworkSnapshot,
        query: Optional[str] = None,
    ) -> ExplainResponse:
        inputs = {
            "snapshot": snapshot,
            "recommendation": recommendation,
            "query": query,
            "facts": [],
            "playbook": "",
            "explanation_text": "",
            "response": None,
        }
        result = self.app.invoke(inputs)
        return result["response"]

    def build_decision_record(
        self,
        recommendation: Recommendation,
        snapshot: NetworkSnapshot,
        mode: AutonomyMode = AutonomyMode.SUPERVISED,
        operator_approval: Optional[Dict[str, Any]] = None,
        submission_result: Optional[Dict[str, Any]] = None,
        outcome_result: Optional[Dict[str, Any]] = None,
        twin_check: Optional[Dict[str, Any]] = None,
    ) -> DecisionRecord:
        """Constructs full 9-stage audit snapshot per §12."""
        candidates = []
        twin_proj = {}
        for f in recommendation.twin_futures:
            candidates.append({
                "id": f.candidate_id,
                "name": f.name,
                "legs_count": len(f.legs),
                "network_unmet_l": f.network_unmet_liters,
            })
            twin_proj[f.candidate_id] = {
                "network_unmet_l": f.network_unmet_liters,
                "notes": f.notes,
            }

        return DecisionRecord(
            decision_id=recommendation.id,
            sim_tick=recommendation.tick,
            timestamp=recommendation.created_at,
            versions={
                "policy": recommendation.policy,
                "forecast_model": "fc-v1",
                "copilot_model": "gpt-4o-mini",
            },
            mode=mode,
            observed={
                "tick": snapshot.tick,
                "routes_available": sum(1 for r in snapshot.routes if getattr(r, 'status', None) in ("AVAILABLE", RouteStatus.AVAILABLE)),
                "stations_open": sum(1 for s in snapshot.stations if getattr(s, 'status', None) in ("OPEN", StationStatus.OPEN)),
                "in_transit_count": len(snapshot.in_transit),
            },
            prediction={
                "risks_count": len(recommendation.risks),
                "unmet_avoided_l": recommendation.projected_unmet_avoided,
                "confidence": recommendation.confidence,
            },
            candidates=candidates,
            twin_projected=twin_proj,
            gate={
                "requires_human": recommendation.human_review_required,
                "confidence": recommendation.confidence,
                "constraints": recommendation.constraints_applied,
            },
            approval=operator_approval,
            submission=submission_result,
            outcome=outcome_result,
            twin_verified=twin_check,
        )
