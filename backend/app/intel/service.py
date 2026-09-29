"""
FuelGuard Intelligence Orchestration Service (backend/app/intel/service.py)
Connects Detection -> Forecasting -> Risk -> LP Optimizer / Greedy -> Decision Twin.
Generates fully inspectable Recommendation objects conforming to contracts.py.
"""

import os
import uuid
import datetime
import httpx
from typing import List, Dict, Tuple, Optional, Any

from backend.app.contracts import (
    NetworkSnapshot,
    Recommendation,
    StockoutRisk,
    DetectionSignal,
    FuelType,
    ForecastRequest,
    ForecastResponse,
)
from backend.app.intel.detection import DetectionEngine
from backend.app.intel.risk import RiskEngine
from backend.app.intel.lp import LPOptimizer
from backend.app.intel.greedy import GreedyPolicy
from backend.app.intel.twin import DecisionTwin
from forecaster.registry import fallback_predict


class IntelligenceService:
    def __init__(
        self,
        forecaster_url: Optional[str] = None,
        horizon_ticks: int = 24,
    ):
        self.forecaster_url = forecaster_url or os.getenv("FORECASTER_URL", "http://localhost:8001")
        self.horizon_ticks = horizon_ticks
        self.detector = DetectionEngine()
        self.risk_engine = RiskEngine(default_horizon_ticks=horizon_ticks)
        self.lp_optimizer = LPOptimizer()
        self.greedy_policy = GreedyPolicy(horizon_ticks=horizon_ticks)
        self.twin = DecisionTwin(horizon_ticks=horizon_ticks)

    def _get_forecast(
        self,
        station_id: str,
        fuel: FuelType,
        current_tick: int,
        demand_history: Optional[List[Dict[str, Any]]],
        demand_multiplier: float,
    ) -> ForecastResponse:
        """Tries HTTP forecaster service first with breaker; seamlessly falls back to in-process predictor."""
        import time
        now = time.time()
        if not hasattr(self, '_forecaster_offline_until'):
            self._forecaster_offline_until = 0.0

        if now > self._forecaster_offline_until:
            req_payload = {
                "station_id": station_id,
                "fuel": fuel.value,
                "horizon_ticks": self.horizon_ticks,
                "current_tick": current_tick,
                "demand_history": demand_history,
                "demand_multiplier": demand_multiplier,
            }
            try:
                with httpx.Client(timeout=0.2) as client:
                    res = client.post(f"{self.forecaster_url}/forecast", json=req_payload)
                    if res.status_code == 200:
                        return ForecastResponse(**res.json())
            except Exception:
                # Mark offline for 15 seconds to avoid connection spam
                self._forecaster_offline_until = now + 15.0

        # In-process pure Python / LightGBM fallback
        return fallback_predict(
            station_id=station_id,
            fuel=fuel,
            horizon_ticks=self.horizon_ticks,
            current_tick=current_tick,
            demand_history=demand_history,
            demand_multiplier=demand_multiplier,
        )

    def evaluate_and_recommend(
        self,
        snapshot: NetworkSnapshot,
        demand_history: Optional[List[Dict[str, Any]]] = None,
        force_containment: bool = False,
    ) -> Recommendation:
        rec_id = f"rec-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        current_tick = snapshot.tick

        # 1. Detection
        signals = self.detector.detect_signals(snapshot, demand_history)

        # Determine if crisis containment mode is warranted
        containment_warranted = force_containment or any(
            s.type in ["route_disruption", "depot_constraint"] and s.severity == "CRITICAL"
            for s in signals
        )

        # 2. Forecasting across all (station, fuel) pairs
        forecasts: Dict[Tuple[str, str], ForecastResponse] = {}
        for s_id, station in snapshot.station_map.items():
            for f in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]:
                fc = self._get_forecast(
                    station_id=s_id,
                    fuel=f,
                    current_tick=current_tick,
                    demand_history=demand_history,
                    demand_multiplier=station.demand_multiplier,
                )
                forecasts[(s_id, f.value)] = fc

        # 3. Risk Engine
        risks = self.risk_engine.evaluate_risks(snapshot, forecasts, self.horizon_ticks)

        # 4. Solvers: LP Optimizer + Greedy Baseline
        lp_legs, is_fallback, policy_name = self.lp_optimizer.optimize_allocations(
            snapshot, risks, containment_mode=containment_warranted
        )
        greedy_legs = self.greedy_policy.plan_allocations(snapshot, risks)

        # 5. Decision Twin 3-Futures Projection
        twin_futures = self.twin.project_three_futures(
            snapshot=snapshot,
            forecasts=forecasts,
            greedy_legs=greedy_legs,
            lp_legs=lp_legs,
        )

        f_noop = twin_futures[0]
        f_greedy = twin_futures[1]
        f_lp = twin_futures[2]

        before_unmet = f_noop.network_unmet_liters
        after_unmet = f_lp.network_unmet_liters
        unmet_avoided = max(0.0, before_unmet - after_unmet)

        # 6. Confidence Scoring
        confidence = 0.95
        if is_fallback:
            confidence -= 0.15
        if snapshot.is_stale:
            confidence -= 0.40
        if containment_warranted:
            confidence -= 0.10
        critical_signals = sum(1 for s in signals if s.severity == "CRITICAL")
        confidence = max(0.40, min(0.99, confidence - (0.05 * critical_signals)))

        # Human Review Gate
        human_review_required = (
            confidence < 0.80
            or containment_warranted
            or any(l.quantity_liters > 5000.0 for l in lp_legs)
            or snapshot.is_stale
        )

        constraints = []
        if containment_warranted:
            constraints.append("Crisis containment mode active: priority leveled across single-route stations.")
        if snapshot.is_stale:
            constraints.append("Operational snapshot marked stale: auto-dispatch locked.")
        if any(r.status != "AVAILABLE" for r in snapshot.route_map.values()):
            constraints.append("Route disruptions present: shipments restricted to available corridors.")

        alternatives = [
            f"No-Op Future: {before_unmet:.1f} L projected unmet demand.",
            f"Greedy-v1 Baseline Future: {f_greedy.network_unmet_liters:.1f} L projected unmet demand.",
        ]

        rec = Recommendation(
            id=rec_id,
            created_at=created_at,
            tick=current_tick,
            policy=policy_name,
            legs=lp_legs,
            signals=signals,
            risks=risks,
            twin_futures=twin_futures,
            selected_future_id="lp-v2" if not is_fallback else "greedy-v1",
            before_projected_unmet=round(before_unmet, 1),
            after_projected_unmet=round(after_unmet, 1),
            projected_unmet_avoided=round(unmet_avoided, 1),
            confidence=round(confidence, 2),
            status="PENDING_REVIEW" if human_review_required else "AUTO_READY",
            human_review_required=human_review_required,
            constraints_applied=constraints,
            alternatives=alternatives,
        )

        # Record for future Twin verification
        self.twin.record_prediction(rec_id, f_lp)

        return rec
