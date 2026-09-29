"""
FuelGuard Intelligence Orchestration Service (backend/app/intel/service.py)
Connects Detection -> Forecasting -> Risk -> LP Optimizer / Greedy -> Decision Twin.
Generates fully inspectable Recommendation objects conforming to contracts.py.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import os
import uuid
from typing import Any

import httpx

from app.contracts import (
    Candidate,
    ForecastResponse,
    FuelType,
    NetworkSnapshot,
    Recommendation,
    SignalSeverity,
)
from app.intel.baseline import fallback_predict
from app.intel.detection import DetectionEngine
from app.intel.greedy import GreedyPolicy
from app.intel.lp import LPOptimizer
from app.intel.risk import RiskEngine
from app.intel.twin import DecisionTwin
from app.obs.logging import log_event
from app.obs.metrics import FALLBACKS


class IntelligenceService:
    def __init__(
        self,
        forecaster_url: str | None = None,
        horizon_ticks: int = 24,
    ):
        self.forecaster_url = forecaster_url or os.getenv("FORECASTER_URL", "http://forecaster:8090")
        self.horizon_ticks = horizon_ticks
        self.detector = DetectionEngine()
        self.risk_engine = RiskEngine(default_horizon_ticks=horizon_ticks)
        self.lp_optimizer = LPOptimizer()
        self.greedy_policy = GreedyPolicy(horizon_ticks=horizon_ticks)
        self.twin = DecisionTwin(horizon_ticks=horizon_ticks)
        self._forecaster_offline_until = 0.0

    def _get_forecast(
        self,
        station_id: str,
        fuel: FuelType,
        current_tick: int,
        demand_history: list[dict[str, Any]] | None = None,
        demand_multiplier: float = 1.0,
        client: httpx.Client | None = None,
    ) -> ForecastResponse:
        """Tries HTTP forecaster service first; seamlessly falls back to in-process baseline."""
        import time
        now = time.time()

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
                if client is not None:
                    res = client.post(f"{self.forecaster_url}/forecast", json=req_payload)
                else:
                    with httpx.Client(timeout=0.3) as c:
                        res = c.post(f"{self.forecaster_url}/forecast", json=req_payload)
                if res.status_code == 200:
                    data = res.json()
                    resp = ForecastResponse(**data)
                    resp.fallback = False
                    return resp
            except Exception:
                self._forecaster_offline_until = now + 15.0

        fc = fallback_predict(
            station_id=station_id,
            fuel=fuel,
            horizon_ticks=self.horizon_ticks,
            current_tick=current_tick,
            demand_history=demand_history,
            demand_multiplier=demand_multiplier,
        )
        fc.fallback = True
        return fc

    def evaluate_and_recommend(
        self,
        snapshot: NetworkSnapshot,
        demand_history: list[dict[str, Any]] | None = None,
        force_containment: bool = False,
    ) -> Recommendation:
        rec_id = f"rec-{uuid.uuid4().hex[:8]}"
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        current_tick = snapshot.tick

        # 1. Detection
        signals = self.detector.detect_signals(snapshot, demand_history)

        # Fix severity check: contract stores "crit" (or SignalSeverity.CRITICAL)
        crit_severities = ("crit", "CRITICAL", SignalSeverity.CRITICAL)
        containment_warranted = force_containment or any(
            s.type in ["route_disruption", "route_disrupted", "depot_constrained", "depot_constraint"]
            and s.severity in crit_severities
            for s in signals
        )

        # 2. Forecasting across all (station, fuel) pairs in parallel
        pairs = [
            (s_id, station, fuel)
            for s_id, station in snapshot.station_map.items()
            for fuel in [FuelType.DIESEL, FuelType.PETROL, FuelType.OCTANE]
        ]

        forecasts: dict[tuple[str, str], ForecastResponse] = {}

        # Run with a pooled httpx client concurrently across threads
        try:
            with httpx.Client(timeout=0.3) as client:
                def _fetch(item):
                    s_id, station, fuel = item
                    fc = self._get_forecast(
                        station_id=s_id,
                        fuel=fuel,
                        current_tick=current_tick,
                        demand_history=demand_history,
                        demand_multiplier=station.demand_multiplier,
                        client=client,
                    )
                    return (s_id, fuel.value), fc

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(pairs), 8)) as executor:
                    results = executor.map(_fetch, pairs)
                    for key, fc in results:
                        forecasts[key] = fc
        except Exception:
            for s_id, station, fuel in pairs:
                fc = self._get_forecast(
                    station_id=s_id,
                    fuel=fuel,
                    current_tick=current_tick,
                    demand_history=demand_history,
                    demand_multiplier=station.demand_multiplier,
                )
                forecasts[(s_id, fuel.value)] = fc

        forecaster_fallback_occurred = any(fc.fallback for fc in forecasts.values())

        # 3. Risk Engine
        risks = self.risk_engine.evaluate_risks(snapshot, forecasts, self.horizon_ticks)

        # 4. Solvers: LP Optimizer + Greedy Baseline
        lp_legs, lp_fallback, policy_name = self.lp_optimizer.optimize_allocations(
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

        # 6. Fallback logging & Prometheus metrics
        fallback_used: list[str] = []
        if lp_fallback:
            fallback_used.append("optimizer")
            FALLBACKS.labels(component="optimizer").inc()
            log_event("fallback.activated", component="optimizer")
        if forecaster_fallback_occurred:
            fallback_used.append("forecaster")
            FALLBACKS.labels(component="forecaster").inc()
            log_event("fallback.activated", component="forecaster")

        # 7. Confidence Scoring & Stale Data Flagging
        confidence = 0.95
        if lp_fallback:
            confidence -= 0.15
        if forecaster_fallback_occurred:
            confidence -= 0.10
        if snapshot.is_stale:
            confidence -= 0.40
        if containment_warranted:
            confidence -= 0.10
        critical_signals = sum(1 for s in signals if s.severity in crit_severities)
        confidence = max(0.40, min(0.99, confidence - (0.05 * critical_signals)))

        # Human Review Gate
        human_review_required = (
            confidence < 0.80
            or containment_warranted
            or any(leg.quantity_liters > 5000.0 for leg in lp_legs)
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

        # Structure 3 Candidate options: noop, greedy-v1, lp-v2
        candidates = [
            Candidate(id="noop", policy="noop", legs=[]),
            Candidate(id="greedy-v1", policy="greedy-v1", legs=greedy_legs),
            Candidate(id="lp-v2", policy="lp-v2", legs=lp_legs),
        ]
        selected_candidate_id = "lp-v2" if not lp_fallback else "greedy-v1"

        deployment_ver = os.getenv("DEPLOYMENT_VERSION", "dev")
        versions = {
            "policy": policy_name,
            "forecast_model": "fc-v1",
            "deployment": deployment_ver,
        }

        rec = Recommendation(
            id=rec_id,
            created_at=created_at,
            tick=current_tick,
            mode="containment" if containment_warranted else "prevention",
            candidates=candidates,
            selected_candidate_id=selected_candidate_id,
            policy=policy_name,
            legs=lp_legs,
            signals=signals,
            risks=risks,
            futures=twin_futures,
            twin_futures=twin_futures,
            before_projected_unmet=round(before_unmet, 1),
            after_projected_unmet=round(after_unmet, 1),
            projected_unmet_avoided=round(unmet_avoided, 1),
            confidence=round(confidence, 2),
            status="PENDING_REVIEW" if human_review_required else "AUTO_READY",
            human_review_required=human_review_required,
            constraints_applied=constraints,
            alternatives=alternatives,
            versions=versions,
            fallback_used=fallback_used,
            built_on_stale_data=snapshot.is_stale,
        )

        # Record for future Twin verification
        self.twin.record_prediction(rec_id, f_lp)

        return rec
