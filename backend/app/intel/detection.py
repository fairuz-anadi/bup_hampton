"""
FuelGuard Detection Engine (backend/app/intel/detection.py)
Monitors demand residual z-scores, CUSUM drift, depletion rate anomalies,
and status diffs across the fuel network. Pure functions over NetworkSnapshot.
"""

from typing import Any

from backend.app.contracts import (
    DepotStatus,
    DetectionSignal,
    NetworkSnapshot,
    RouteStatus,
    SignalSeverity,
    StationStatus,
    SupplyStatus,
)
from forecaster.models.baseline import PROFILES, BaselineForecaster


class DetectionEngine:
    def __init__(self, z_score_threshold: float = 3.0, cusum_h: float = 5.0):
        self.z_score_threshold = z_score_threshold
        self.cusum_h = cusum_h
        self.forecaster = BaselineForecaster()
        self.cusum_state: dict[str, float] = {}

    def detect_signals(
        self,
        snapshot: NetworkSnapshot,
        demand_history: list[dict[str, Any]] | None = None,
        previous_snapshot: NetworkSnapshot | None = None,
    ) -> list[DetectionSignal]:
        signals: list[DetectionSignal] = []
        current_tick = snapshot.tick

        # 1. State Diffs: Route disruptions
        for r_id, route in snapshot.route_map.items():
            if route.status == RouteStatus.DISRUPTED:
                signals.append(DetectionSignal(
                    id=f"sig-route-{r_id}-{current_tick}",
                    type="route_disruption",
                    severity=SignalSeverity.CRITICAL,
                    target_id=r_id,
                    message=f"Route {r_id} ({route.depot_id} -> {route.station_id}) is DISRUPTED.",
                    value=1.0,
                    threshold=0.0,
                    detected_at_tick=current_tick,
                ))

        # 2. State Diffs: Station outages
        for s_id, station in snapshot.station_map.items():
            if station.status == StationStatus.OUTAGE:
                signals.append(DetectionSignal(
                    id=f"sig-station-outage-{s_id}-{current_tick}",
                    type="station_outage",
                    severity=SignalSeverity.CRITICAL,
                    target_id=s_id,
                    message=f"Station {s_id} is in OUTAGE mode. Sales halted.",
                    value=1.0,
                    threshold=0.0,
                    detected_at_tick=current_tick,
                ))
            elif station.demand_multiplier > 1.2:
                signals.append(DetectionSignal(
                    id=f"sig-demand-spike-{s_id}-{current_tick}",
                    type="demand_spike",
                    severity=SignalSeverity.WARNING if station.demand_multiplier < 1.6 else SignalSeverity.CRITICAL,
                    target_id=s_id,
                    message=f"Station {s_id} elevated demand multiplier: {station.demand_multiplier}x normal.",
                    value=station.demand_multiplier,
                    threshold=1.2,
                    detected_at_tick=current_tick,
                ))

        # 3. State Diffs: Depot constraints
        for d_id, depot in snapshot.depot_map.items():
            if depot.status == DepotStatus.CONSTRAINED:
                signals.append(DetectionSignal(
                    id=f"sig-depot-{d_id}-{current_tick}",
                    type="depot_constraint",
                    severity=SignalSeverity.WARNING,
                    target_id=d_id,
                    message=f"Depot {d_id} throughput is CONSTRAINED.",
                    value=depot.dispatch_capacity_per_tick,
                    threshold=1000.0,
                    detected_at_tick=current_tick,
                ))

        # 4. Supply Arrivals: Delayed or shortfall
        for arr in snapshot.supply_arrivals:
            if arr.status == SupplyStatus.DELAYED:
                signals.append(DetectionSignal(
                    id=f"sig-supply-delay-{arr.id}-{current_tick}",
                    type="shipment_delay",
                    severity=SignalSeverity.WARNING,
                    target_id=arr.depot_id,
                    message=(
                        f"Supply arrival {arr.id} to {arr.depot_id} ({arr.fuel}) "
                        f"delayed to tick {arr.arrival_tick}."
                    ),
                    value=float(arr.arrival_tick - arr.planned_tick),
                    threshold=1.0,
                    detected_at_tick=current_tick,
                ))

        # 5. Statistical Residual Z-Score and CUSUM on Demand History
        if demand_history:
            recent_demands = demand_history[-15:]
            for entry in recent_demands:
                s_id = entry.get("station_id")
                fuel = entry.get("fuel", "PETROL")
                actual = entry.get("demand", entry.get("quantity", 0.0))
                t = entry.get("tick", current_tick)

                if not s_id or s_id not in snapshot.station_map:
                    continue

                profile = snapshot.station_map[s_id].demand_profile
                expected = self.forecaster.compute_base_demand_for_tick(
                    profile, fuel, t, demand_multiplier=snapshot.station_map[s_id].demand_multiplier
                )
                nominal_daily = PROFILES.get(profile, {}).get(fuel, 7000.0)
                sigma = (nominal_daily / 96.0) * PROFILES.get(profile, {}).get("noise", 0.1)

                diff = actual - expected
                z_score = abs(diff) / max(sigma, 1.0)

                key = f"{s_id}:{fuel}"
                # CUSUM update
                slack = 0.5 * sigma
                self.cusum_state[key] = max(0.0, self.cusum_state.get(key, 0.0) + (diff - slack))

                if z_score >= self.z_score_threshold:
                    msg = (
                        f"Demand anomaly at {s_id} ({fuel}): observed={actual:.1f}L, "
                        f"expected={expected:.1f}L, z-score={z_score:.2f}."
                    )
                    signals.append(DetectionSignal(
                        id=f"sig-zscore-{s_id}-{fuel}-{t}",
                        type="demand_anomaly",
                        severity=SignalSeverity.CRITICAL if z_score > 4.5 else SignalSeverity.WARNING,
                        target_id=s_id,
                        message=msg,
                        value=round(z_score, 2),
                        threshold=self.z_score_threshold,
                        detected_at_tick=current_tick,
                    ))

                if self.cusum_state[key] > (self.cusum_h * sigma):
                    signals.append(DetectionSignal(
                        id=f"sig-cusum-{s_id}-{fuel}-{t}",
                        type="persistent_demand_drift",
                        severity=SignalSeverity.WARNING,
                        target_id=s_id,
                        message=(
                            f"Persistent upward demand drift at {s_id} ({fuel}): "
                            f"CUSUM={self.cusum_state[key]:.1f}."
                        ),
                        value=round(self.cusum_state[key], 1),
                        threshold=round(self.cusum_h * sigma, 1),
                        detected_at_tick=current_tick,
                    ))

        return signals
