"""
Forecaster v1: Baseline + Diurnal Profile + EWMA Residual Tracking (fc-v1)
Conforms to BUP Fuel Supply Simulator Integration Guide (§8.5, §8.6).
"""

import math
from typing import List, Dict, Any, Optional
from backend.app.contracts import FuelType, ForecastBand, ForecastResponse


PROFILES = {
    "urban_high": {
        "DIESEL": 8500.0,
        "PETROL": 10500.0,
        "OCTANE": 5600.0,
        "noise": 0.10,
    },
    "industrial": {
        "DIESEL": 14000.0,
        "PETROL": 4500.0,
        "OCTANE": 2200.0,
        "noise": 0.08,
    },
    "highway": {
        "DIESEL": 10500.0,
        "PETROL": 11000.0,
        "OCTANE": 6200.0,
        "noise": 0.12,
    },
    "regional": {
        "DIESEL": 7200.0,
        "PETROL": 7600.0,
        "OCTANE": 3600.0,
        "noise": 0.10,
    },
}

STATION_PROFILES = {
    "station-mirpur": "urban_high",
    "station-tongi": "industrial",
    "station-karnaphuli": "highway",
    "station-coxsbazar": "regional",
}


def get_hour_factor(profile: str, hour: int) -> float:
    """Return diurnal hour-of-day demand multiplier per §8.6."""
    if profile == "industrial":
        return 1.55 if 6 <= hour < 18 else 0.45
    elif profile == "highway":
        return 1.35 if (6 <= hour < 10 or 16 <= hour < 21) else 0.75
    elif profile == "urban_high":
        return 1.45 if (7 <= hour < 10 or 16 <= hour < 21) else 0.70
    elif profile == "regional":
        return 1.25 if 7 <= hour < 21 else 0.65
    return 1.0


def tick_to_sim_hour(tick: int) -> int:
    """96 ticks per day, 15 min per tick -> 4 ticks per hour."""
    return (tick % 96) // 4


class BaselineForecaster:
    """
    Forecaster v1: Combines the known simulator base rate, diurnal hour-of-day
    profile, current demand multiplier, and EWMA online residual tracking.
    """
    def __init__(self, ewma_alpha: float = 0.2):
        self.version = "fc-v1"
        self.ewma_alpha = ewma_alpha

    def compute_base_demand_for_tick(
        self, profile: str, fuel: str, tick: int, demand_multiplier: float = 1.0
    ) -> float:
        prof_data = PROFILES.get(profile, PROFILES["urban_high"])
        daily_liters = prof_data.get(fuel, 7000.0)
        base_per_tick = daily_liters / 96.0
        hour = tick_to_sim_hour(tick)
        hour_factor = get_hour_factor(profile, hour)
        return base_per_tick * hour_factor * demand_multiplier

    def predict(
        self,
        station_id: str,
        fuel: FuelType,
        horizon_ticks: int = 24,
        current_tick: int = 0,
        demand_history: Optional[List[Dict[str, Any]]] = None,
        demand_multiplier: float = 1.0,
    ) -> ForecastResponse:
        profile = STATION_PROFILES.get(station_id, "urban_high")
        prof_noise = PROFILES[profile]["noise"]

        # Online EWMA residual tracking if history exists
        residual_bias = 0.0
        residual_variance = 0.0
        if demand_history and len(demand_history) > 0:
            residuals = []
            for item in demand_history[-20:]:  # last 20 ticks
                t = item.get("tick", 0)
                actual = item.get("demand", item.get("quantity", 0.0))
                expected = self.compute_base_demand_for_tick(
                    profile, fuel.value, t, demand_multiplier
                )
                residuals.append(actual - expected)

            if residuals:
                # EWMA calculation
                ewma = residuals[0]
                for r in residuals[1:]:
                    ewma = self.ewma_alpha * r + (1 - self.ewma_alpha) * ewma
                residual_bias = ewma
                
                # Sample variance
                if len(residuals) > 1:
                    mean_r = sum(residuals) / len(residuals)
                    residual_variance = sum((x - mean_r) ** 2 for x in residuals) / (len(residuals) - 1)
                else:
                    residual_variance = (residuals[0] * 0.1) ** 2

        bands: List[ForecastBand] = []
        nominal_daily = PROFILES[profile][fuel.value]
        nominal_tick = nominal_daily / 96.0
        sigma = math.sqrt(residual_variance) if residual_variance > 0 else (nominal_tick * prof_noise)
        sigma = max(sigma, nominal_tick * prof_noise * 0.5)

        for step in range(1, horizon_ticks + 1):
            future_tick = current_tick + step
            base_pred = self.compute_base_demand_for_tick(
                profile, fuel.value, future_tick, demand_multiplier
            )
            # Add decayed residual bias
            decay = 0.95 ** step
            mean_pred = max(0.0, base_pred + residual_bias * decay)
            # Growing uncertainty over horizon
            horizon_sigma = sigma * math.sqrt(1.0 + 0.03 * step)
            p10 = max(0.0, mean_pred - 1.28 * horizon_sigma)
            p90 = mean_pred + 1.28 * horizon_sigma

            bands.append(ForecastBand(
                tick=future_tick,
                mean=round(mean_pred, 1),
                p10=round(p10, 1),
                p90=round(p90, 1),
            ))

        return ForecastResponse(
            station_id=station_id,
            fuel=fuel,
            horizon_ticks=horizon_ticks,
            bands=bands,
            residual_sigma=round(sigma, 2),
            model_version=self.version,
        )
