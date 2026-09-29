"""
Forecaster v2: LightGBM Multi-Quantile Regression (fc-v2)
Predicts p10, p50 (mean), and p90 using gradient-boosted trees.
"""

import os
import random
from typing import Any

import lightgbm as lgb
import numpy as np

try:
    from app.contracts import ForecastBand, ForecastResponse, FuelType
except ImportError:
    from backend.app.contracts import ForecastBand, ForecastResponse, FuelType
from forecaster.models.baseline import (
    PROFILES,
    STATION_PROFILES,
    BaselineForecaster,
    tick_to_sim_hour,
)


class LGBMQuantileForecaster:
    """
    Forecaster v2: Uses LightGBM trained with quantile objective (alpha=0.1, 0.5, 0.9).
    Falls back gracefully to fc-v1 if insufficient historical data or models untrained.
    """
    def __init__(self, model_dir: str | None = None):
        self.version = "fc-v2"
        self.model_dir = model_dir or os.path.join(os.path.dirname(__file__), "weights")
        self.baseline = BaselineForecaster()
        self.models: dict[float, lgb.Booster | None] = {0.1: None, 0.5: None, 0.9: None}
        self.is_trained = False
        os.makedirs(self.model_dir, exist_ok=True)
        self._load_or_train_initial()

    def _extract_features(
        self, profile: str, fuel: str, tick: int, demand_multiplier: float,
        lag_1: float, lag_4: float, rolling_mean_8: float
    ) -> list[float]:
        hour = tick_to_sim_hour(tick)
        day_of_week = (tick // 96) % 7
        profiles_list = ["urban_high", "industrial", "highway", "regional"]
        prof_idx = profiles_list.index(profile) if profile in profiles_list else 0
        fuels_list = ["DIESEL", "PETROL", "OCTANE"]
        fuel_idx = fuels_list.index(fuel) if fuel in fuels_list else 0

        prof_base = self.baseline.compute_base_demand_for_tick(profile, fuel, tick, demand_multiplier)

        return [
            float(hour),
            float(day_of_week),
            float(prof_idx),
            float(fuel_idx),
            float(demand_multiplier),
            float(lag_1),
            float(lag_4),
            float(rolling_mean_8),
            float(prof_base),
        ]

    def _generate_synthetic_training_data(self, num_days: int = 14):
        """Generates realistic training data from simulator profile distributions."""
        X = []
        y = []
        random.seed(42)
        np.random.seed(42)

        for day in range(num_days):
            for profile in STATION_PROFILES.values():
                for fuel in ["DIESEL", "PETROL", "OCTANE"]:
                    noise_pct = PROFILES[profile]["noise"]
                    lag_1 = 0.0
                    lag_4 = 0.0
                    history = []

                    for t_idx in range(96):
                        tick = day * 96 + t_idx
                        # Inject random multiplier shocks occasionally
                        mult = 1.8 if (day == 5 and 20 <= t_idx <= 40) else 1.0
                        base = self.baseline.compute_base_demand_for_tick(profile, fuel, tick, mult)
                        noise = np.random.normal(0, base * noise_pct)
                        actual_demand = max(0.0, base + noise)

                        history.append(actual_demand)
                        rolling_8 = np.mean(history[-8:]) if len(history) >= 8 else actual_demand

                        feats = self._extract_features(
                            profile, fuel, tick, mult, lag_1, lag_4, rolling_8
                        )
                        X.append(feats)
                        y.append(actual_demand)

                        lag_1 = actual_demand
                        if len(history) >= 4:
                            lag_4 = history[-4]

        return np.array(X), np.array(y)

    def _load_or_train_initial(self):
        """Trains or loads quantile models."""
        X, y = self._generate_synthetic_training_data(num_days=7)
        dtrain = lgb.Dataset(X, label=y)

        params_base = {
            "objective": "quantile",
            "metric": "quantile",
            "verbosity": -1,
            "boosting_type": "gbdt",
            "n_estimators": 50,
            "learning_rate": 0.08,
            "num_leaves": 15,
            "min_child_samples": 10,
        }

        for alpha in [0.1, 0.5, 0.9]:
            p = params_base.copy()
            p["alpha"] = alpha
            model = lgb.train(p, dtrain)
            self.models[alpha] = model

        self.is_trained = True

    def predict(
        self,
        station_id: str,
        fuel: FuelType,
        horizon_ticks: int = 24,
        current_tick: int = 0,
        demand_history: list[dict[str, Any]] | None = None,
        demand_multiplier: float = 1.0,
    ) -> ForecastResponse:
        if not self.is_trained or any(m is None for m in self.models.values()):
            return self.baseline.predict(
                station_id, fuel, horizon_ticks, current_tick, demand_history, demand_multiplier
            )

        profile = STATION_PROFILES.get(station_id, "urban_high")
        bands: list[ForecastBand] = []

        # Derive initial lags from history or baseline
        history_vals = []
        if demand_history:
            history_vals = [item.get("demand", item.get("quantity", 0.0)) for item in demand_history]

        curr_base = self.baseline.compute_base_demand_for_tick(
            profile, fuel.value, current_tick, demand_multiplier
        )
        lag_1 = history_vals[-1] if len(history_vals) >= 1 else curr_base
        lag_4 = history_vals[-4] if len(history_vals) >= 4 else curr_base
        rolling_8 = np.mean(history_vals[-8:]) if len(history_vals) >= 8 else curr_base

        errors = []

        for step in range(1, horizon_ticks + 1):
            future_tick = current_tick + step
            feats = np.array([self._extract_features(
                profile, fuel.value, future_tick, demand_multiplier, lag_1, lag_4, rolling_8
            )])

            p10 = float(self.models[0.1].predict(feats)[0])
            p50 = float(self.models[0.5].predict(feats)[0])
            p90 = float(self.models[0.9].predict(feats)[0])

            # Ensure quantile monotonicity
            p10 = max(0.0, p10)
            p50 = max(p10, p50)
            p90 = max(p50, p90)

            bands.append(ForecastBand(
                tick=future_tick,
                mean=round(p50, 1),
                p10=round(p10, 1),
                p90=round(p90, 1),
            ))

            # Autoregressive step
            lag_4 = lag_1
            lag_1 = p50
            rolling_8 = 0.8 * rolling_8 + 0.2 * p50
            errors.append(p90 - p10)

        residual_sigma = float(np.mean(errors) / (2 * 1.28)) if errors else 100.0

        return ForecastResponse(
            station_id=station_id,
            fuel=fuel,
            horizon_ticks=horizon_ticks,
            bands=bands,
            residual_sigma=round(residual_sigma, 2),
            model_version=self.version,
        )
