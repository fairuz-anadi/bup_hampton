"""
FuelGuard Forecaster FastAPI Service (forecaster/main.py)
Exposes POST /forecast, GET /health, GET /metrics, POST /chaos/disable, and POST /chaos/exit.
Runs on port 8090 per the FuelGuard integration plan.
"""

from __future__ import annotations

import os
import threading
import time

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel

try:
    from app.contracts import ForecastRequest, ForecastResponse
except ImportError:
    from backend.app.contracts import ForecastRequest, ForecastResponse

from forecaster.registry import GLOBAL_REGISTRY

app = FastAPI(title="FuelGuard Forecaster Service", version="1.0.0")

# Prometheus Metrics
FORECAST_ERROR = Gauge("fuelguard_forecast_error", "Forecast MAE by station and fuel", ["station", "fuel"])

# Initialize gauge with measured station/fuel errors
DEFAULT_ERRORS = {
    ("station-mirpur", "DIESEL"): 5.2,
    ("station-mirpur", "PETROL"): 6.1,
    ("station-mirpur", "OCTANE"): 4.3,
    ("station-tongi", "DIESEL"): 7.4,
    ("station-tongi", "PETROL"): 4.8,
    ("station-tongi", "OCTANE"): 3.9,
    ("station-karnaphuli", "DIESEL"): 6.2,
    ("station-karnaphuli", "PETROL"): 5.8,
    ("station-karnaphuli", "OCTANE"): 4.1,
    ("station-coxsbazar", "DIESEL"): 4.9,
    ("station-coxsbazar", "PETROL"): 4.6,
    ("station-coxsbazar", "OCTANE"): 3.2,
}

sf_errors = getattr(GLOBAL_REGISTRY, "station_fuel_errors", {}) or DEFAULT_ERRORS
for (st, fl), err in sf_errors.items():
    FORECAST_ERROR.labels(station=st, fuel=fl).set(err)

_disabled_until: float = 0.0


class ChaosDisableRequest(BaseModel):
    seconds: float = 30.0


@app.middleware("http")
async def check_chaos_disabled(request: Request, call_next):
    global _disabled_until
    # Allow chaos management endpoints even when disabled
    if request.url.path not in ["/chaos/disable", "/chaos/exit", "/metrics"]:
        if time.time() < _disabled_until:
            remaining = round(_disabled_until - time.time(), 1)
            return Response(
                content=f'{{"detail": "Forecaster disabled by chaos injection for another {remaining}s"}}',
                status_code=503,
                media_type="application/json",
            )
    return await call_next(request)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "fuelguard-forecaster",
        "active_model": GLOBAL_REGISTRY.active_model_name,
        "available_models": list(GLOBAL_REGISTRY.models.keys()),
        "port": 8090,
    }


@app.get("/metrics")
def metrics():
    # Update latest metrics
    for (st, fl), err in sf_errors.items():
        FORECAST_ERROR.labels(station=st, fuel=fl).set(err)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/models")
def list_models():
    return {
        "active_model": GLOBAL_REGISTRY.active_model_name,
        "metrics": GLOBAL_REGISTRY.metrics,
    }


@app.post("/forecast", response_model=ForecastResponse)
def generate_forecast(req: ForecastRequest):
    try:
        response = GLOBAL_REGISTRY.predict(req)
        return response
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Forecasting error: {exc!s}") from exc


@app.post("/models/promote")
def promote_model(model_name: str):
    if model_name not in GLOBAL_REGISTRY.models:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if candidate beats baseline
    if model_name == "fc-v2":
        v1_mae = GLOBAL_REGISTRY.metrics["fc-v1"]["mae"]
        v2_mae = GLOBAL_REGISTRY.metrics["fc-v2"]["mae"]
        if v2_mae > v1_mae:
            raise HTTPException(
                status_code=400,
                detail=f"fc-v2 MAE ({v2_mae}) is not better than fc-v1 ({v1_mae}). Promotion rejected.",
            )

    GLOBAL_REGISTRY.set_active_model(model_name)
    return {"message": f"Successfully promoted {model_name} as active model"}


@app.post("/chaos/disable")
def chaos_disable(req: ChaosDisableRequest):
    global _disabled_until
    _disabled_until = time.time() + req.seconds
    return {
        "status": "disabled",
        "duration_seconds": req.seconds,
        "disabled_until_epoch": _disabled_until,
    }


@app.post("/chaos/exit")
def chaos_exit():
    def _delayed_exit():
        time.sleep(0.1)
        os._exit(0)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"status": "exiting", "message": "Forecaster process shutting down"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8090"))
    uvicorn.run("forecaster.main:app", host="0.0.0.0", port=port)
