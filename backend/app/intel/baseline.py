"""
FuelGuard In-Process Baseline Forecaster & Fallback Predictor (backend/app/intel/baseline.py)
Implements Forecaster v1: Baseline diurnal profiles, EWMA residual tracking,
and regional demand multipliers (Chattogram 1.08x).
Conforms to BUP Fuel Supply Simulator Integration Guide (§8.5, §8.6).
"""

from __future__ import annotations

import math
from typing import Any

from app.contracts import ForecastBand, ForecastResponse, FuelType

PROFILES: dict[str, dict[str, float]] = {
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

STATION_PROFILES: dict[str, str] = {
    "station-mirpur": "urban_high",
    "station-tongi": "industrial",
    "station-karnaphuli": "highway",
    "station-coxsbazar": "regional",
}

STATION_REGIONS: dict[str, str] = {
    "station-mirpur": "region-dhaka",
    "station-tongi": "region-dhaka",
    "station-karnaphuli": "region-chattogram",
    "station-coxsbazar": "region-chattogram",
}

# Regional demand factors per §8.5: Chattogram Division has a 1.08x factor
REGION_FACTORS: dict[str, float] = {
    "region-dhaka": 1.0,
    "region-chattogram": 1.08,
}


def get_station_region_factor(station_id: str) -> float:
    region_id = STATION_REGIONS.get(station_id, "region-dhaka")
    return REGION_FACTORS.get(region_id, 1.0)


def get_hour_factor(profile: str, hour: int) -> float:
    """Return diurnal hour-of-day demand multiplier per §8.6."""
    if profile == "industrial":
        return 1.55 if 6 <= hour < 18 else 0.45
    if profile == "highway":
        return 1.35 if (6 <= hour < 10 or 16 <= hour < 21) else 0.75
    if profile == "urban_high":
        return 1.45 if (7 <= hour < 10 or 16 <= hour < 21) else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour < 21 else 0.65
    return 1.0


def tick_to_sim_hour(tick: int) -> int:
    """96 ticks per day, 15 min per tick -> 4 ticks per hour."""
    return (tick % 96) // 4


class BaselineForecaster:
    """
    Forecaster v1: Combines the known simulator base rate, diurnal hour-of-day
    profile, regional factor (Chattogram 1.08x), current demand multiplier,
    and online EWMA residual tracking over recent demand observations.
    """

    def __init__(self, ewma_alpha: float = 0.2):
        self.version = "fc-v1"
        self.ewma_alpha = ewma_alpha

    def compute_base_demand_for_tick(
        self,
        profile: str,
        fuel: str,
        tick: int,
        demand_multiplier: float = 1.0,
        station_id: str | None = None,
    ) -> float:
        prof_data = PROFILES.get(profile, PROFILES["urban_high"])
        daily_liters = prof_data.get(fuel, 7000.0)
        base_per_tick = daily_liters / 96.0
        hour = tick_to_sim_hour(tick)
        hour_factor = get_hour_factor(profile, hour)
        region_factor = get_station_region_factor(station_id) if station_id else 1.0
        return base_per_tick * hour_factor * demand_multiplier * region_factor

    def predict(
        self,
        station_id: str,
        fuel: FuelType,
        horizon_ticks: int = 24,
        current_tick: int = 0,
        demand_history: list[dict[str, Any]] | None = None,
        demand_multiplier: float = 1.0,
    ) -> ForecastResponse:
        profile = STATION_PROFILES.get(station_id, "urban_high")
        prof_noise = PROFILES[profile]["noise"]

        # Online EWMA residual tracking on station/fuel filtered history
        residual_bias = 0.0
        residual_variance = 0.0

        if demand_history:
            # 1. Filter by station and fuel (support both 'demand_liters' and 'fuel_type')
            filtered: list[dict[str, Any]] = []
            for item in demand_history:
                s_match = item.get("station_id") == station_id
                f_raw = item.get("fuel_type", item.get("fuel"))
                f_val = f_raw.value if hasattr(f_raw, "value") else str(f_raw)
                if s_match and f_val == fuel.value:
                    filtered.append(item)

            # 2. Sort chronologically by tick ascending (simulator/backend history is newest-first)
            filtered.sort(key=lambda x: x.get("tick", 0))
            recent_entries = filtered[-20:]  # most recent 20 observations for this station/fuel

            residuals = []
            for item in recent_entries:
                t = item.get("tick", 0)
                actual = float(item.get("demand_liters", item.get("demand", item.get("quantity", 0.0))))
                expected = self.compute_base_demand_for_tick(
                    profile, fuel.value, t, demand_multiplier=demand_multiplier, station_id=station_id
                )
                residuals.append(actual - expected)

            if residuals:
                ewma = residuals[0]
                for r in residuals[1:]:
                    ewma = self.ewma_alpha * r + (1 - self.ewma_alpha) * ewma
                residual_bias = ewma

                if len(residuals) > 1:
                    mean_r = sum(residuals) / len(residuals)
                    residual_variance = sum((x - mean_r) ** 2 for x in residuals) / (len(residuals) - 1)
                else:
                    residual_variance = (residuals[0] * 0.1) ** 2

        bands: list[ForecastBand] = []
        nominal_daily = PROFILES[profile][fuel.value]
        region_factor = get_station_region_factor(station_id)
        nominal_tick = (nominal_daily / 96.0) * region_factor
        sigma = math.sqrt(residual_variance) if residual_variance > 0 else (nominal_tick * prof_noise)
        sigma = max(sigma, nominal_tick * prof_noise * 0.5)

        for step in range(1, horizon_ticks + 1):
            future_tick = current_tick + step
            base_pred = self.compute_base_demand_for_tick(
                profile, fuel.value, future_tick, demand_multiplier=demand_multiplier, station_id=station_id
            )
            decay = 0.95 ** step
            mean_pred = max(0.0, base_pred + residual_bias * decay)
            horizon_sigma = sigma * math.sqrt(1.0 + 0.03 * step)
            p10 = max(0.0, mean_pred - 1.28 * horizon_sigma)
            p90 = mean_pred + 1.28 * horizon_sigma

            bands.append(
                ForecastBand(
                    tick=future_tick,
                    mean=round(mean_pred, 1),
                    p10=round(p10, 1),
                    p90=round(p90, 1),
                )
            )

        return ForecastResponse(
            station_id=station_id,
            fuel=fuel,
            horizon_ticks=horizon_ticks,
            bands=bands,
            residual_sigma=round(sigma, 2),
            model_version=self.version,
            fallback=True,
        )


_DEFAULT_BASELINE = BaselineForecaster()


def fallback_predict(
    station_id: str,
    fuel: FuelType,
    horizon_ticks: int = 24,
    current_tick: int = 0,
    demand_history: list[dict[str, Any]] | None = None,
    demand_multiplier: float = 1.0,
) -> ForecastResponse:
    """
    In-process baseline forecaster fallback. Runs entirely within the backend process
    with zero external HTTP calls, providing reliable degradation when forecaster service is offline.
    """
    return _DEFAULT_BASELINE.predict(
        station_id=station_id,
        fuel=fuel,
        horizon_ticks=horizon_ticks,
        current_tick=current_tick,
        demand_history=demand_history,
        demand_multiplier=demand_multiplier,
    )
