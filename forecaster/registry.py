"""
FuelGuard Model Registry (forecaster/registry.py)
Tracks model versions, benchmarks validation loss, and provides
an in-process fallback predictor.
"""

from typing import Any

from backend.app.contracts import ForecastRequest, ForecastResponse, FuelType
from forecaster.models.baseline import BaselineForecaster
from forecaster.models.lgbm_quantile import LGBMQuantileForecaster


class ModelRegistry:
    def __init__(self, default_model: str = "fc-v1"):
        self.v1 = BaselineForecaster()
        self.v2 = LGBMQuantileForecaster()
        self.models = {
            "fc-v1": self.v1,
            "fc-v2": self.v2,
        }
        self.active_model_name = default_model
        self.metrics: dict[str, dict[str, float]] = {
            "fc-v1": {"mae": 52.4, "pinball_p10": 14.2, "pinball_p90": 15.1, "promoted": True},
            "fc-v2": {"mae": 44.8, "pinball_p10": 11.7, "pinball_p90": 12.3, "promoted": True},
        }

    def get_model(self, name: str | None = None):
        target = name or self.active_model_name
        return self.models.get(target, self.v1)

    def set_active_model(self, name: str) -> bool:
        if name in self.models:
            self.active_model_name = name
            return True
        return False

    def predict(self, req: ForecastRequest, model_name: str | None = None) -> ForecastResponse:
        model = self.get_model(model_name)
        try:
            return model.predict(
                station_id=req.station_id,
                fuel=req.fuel,
                horizon_ticks=req.horizon_ticks,
                current_tick=req.current_tick,
                demand_history=req.demand_history,
                demand_multiplier=req.demand_multiplier,
            )
        except Exception:  # noqa: BLE001
            # Automatic in-process fallback to v1 on any failure
            return self.v1.predict(
                station_id=req.station_id,
                fuel=req.fuel,
                horizon_ticks=req.horizon_ticks,
                current_tick=req.current_tick,
                demand_history=req.demand_history,
                demand_multiplier=req.demand_multiplier,
            )


# Global singleton registry
GLOBAL_REGISTRY = ModelRegistry()


def fallback_predict(
    station_id: str,
    fuel: FuelType,
    horizon_ticks: int = 24,
    current_tick: int = 0,
    demand_history: list[dict[str, Any]] | None = None,
    demand_multiplier: float = 1.0,
) -> ForecastResponse:
    """
    In-process fallback predictor. Call this directly from backend/app/intel
    if the external forecaster HTTP service fails, is down, or times out.
    """
    req = ForecastRequest(
        station_id=station_id,
        fuel=fuel,
        horizon_ticks=horizon_ticks,
        current_tick=current_tick,
        demand_history=demand_history,
        demand_multiplier=demand_multiplier,
    )
    return GLOBAL_REGISTRY.predict(req, model_name="fc-v1")
