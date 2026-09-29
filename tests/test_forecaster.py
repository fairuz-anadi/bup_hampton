"""Tests for Forecaster Service (fc-v1, fc-v2, and Registry)."""

import pytest
from backend.app.contracts import FuelType, ForecastRequest
from forecaster.models.baseline import BaselineForecaster, get_hour_factor
from forecaster.models.lgbm_quantile import LGBMQuantileForecaster
from forecaster.registry import ModelRegistry, fallback_predict


def test_baseline_forecaster_diurnal_factors():
    # Peak vs off-peak for urban_high
    assert get_hour_factor("urban_high", 8) == 1.45
    assert get_hour_factor("urban_high", 12) == 0.70
    assert get_hour_factor("industrial", 10) == 1.55
    assert get_hour_factor("industrial", 20) == 0.45


def test_baseline_forecaster_prediction():
    forecaster = BaselineForecaster()
    res = forecaster.predict(
        station_id="station-mirpur",
        fuel=FuelType.PETROL,
        horizon_ticks=24,
        current_tick=0,
        demand_multiplier=1.0,
    )
    assert res.station_id == "station-mirpur"
    assert res.fuel == FuelType.PETROL
    assert len(res.bands) == 24
    assert res.model_version == "fc-v1"
    for band in res.bands:
        assert band.p10 <= band.mean <= band.p90
        assert band.p10 >= 0.0


def test_lgbm_quantile_forecaster():
    forecaster = LGBMQuantileForecaster()
    res = forecaster.predict(
        station_id="station-mirpur",
        fuel=FuelType.DIESEL,
        horizon_ticks=12,
        current_tick=10,
        demand_multiplier=1.2,
    )
    assert res.model_version == "fc-v2"
    assert len(res.bands) == 12
    for band in res.bands:
        assert band.p10 <= band.mean <= band.p90


def test_model_registry_and_fallback():
    registry = ModelRegistry()
    req = ForecastRequest(
        station_id="station-tongi",
        fuel=FuelType.OCTANE,
        horizon_ticks=8,
        current_tick=0,
    )
    res = registry.predict(req)
    assert len(res.bands) == 8

    # Test in-process fallback function
    fb_res = fallback_predict("station-karnaphuli", FuelType.DIESEL, horizon_ticks=6)
    assert fb_res.model_version == "fc-v1"
    assert len(fb_res.bands) == 6
