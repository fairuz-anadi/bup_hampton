"""Operator key for anything that changes the world: allocations, approvals, chaos actions."""
import secrets

from fastapi import Header, HTTPException, status

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
