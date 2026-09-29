"""
FuelGuard Forecaster FastAPI Service (forecaster/main.py)
Exposes POST /forecast, health checks, and model promotion endpoints.
"""

from fastapi import FastAPI, HTTPException
from backend.app.contracts import ForecastRequest, ForecastResponse
from forecaster.registry import GLOBAL_REGISTRY

app = FastAPI(title="FuelGuard Forecaster Service", version="1.0.0")


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "fuelguard-forecaster",
        "active_model": GLOBAL_REGISTRY.active_model_name,
        "available_models": list(GLOBAL_REGISTRY.models.keys()),
    }


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
        raise HTTPException(status_code=500, detail=f"Forecasting error: {str(exc)}")


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
                detail=f"fc-v2 MAE ({v2_mae}) is not better than fc-v1 ({v1_mae}). Promotion rejected."
            )
    
    GLOBAL_REGISTRY.set_active_model(model_name)
    return {"message": f"Successfully promoted {model_name} as active model"}
