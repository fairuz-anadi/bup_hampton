"""Operator key for anything that changes the world: allocations, approvals, chaos actions."""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import get_settings


def require_operator(x_operator_key: str | None = Header(default=None)) -> None:
    expected = get_settings().operator_key.get_secret_value()
    if not expected:
        # Fail closed: without a configured key, writes are disabled rather than open to anyone.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"code": "WRITES_DISABLED", "message": "OPERATOR_KEY is not configured."})
    if not x_operator_key or not secrets.compare_digest(x_operator_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            {"code": "OPERATOR_KEY_REQUIRED", "message": "Missing or wrong X-Operator-Key."})


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.get("/status", summary="Operator key status and verification")
def auth_status(x_operator_key: str | None = Header(default=None)):
    settings = get_settings()
    expected = settings.operator_key.get_secret_value()
    writes_enabled = bool(expected)
    is_valid = bool(expected and x_operator_key and secrets.compare_digest(x_operator_key, expected))
    is_dev = settings.deployment_version == "dev" or expected == "dev-operator-key"
    dev_key = expected if is_dev else None
    return {
        "writes_enabled": writes_enabled,
        "is_valid": is_valid,
        "is_dev": is_dev,
        "dev_key": dev_key,
    }


@auth_router.post("/verify", dependencies=[Depends(require_operator)], summary="Verify operator key")
def auth_verify():
    return {"ok": True}

