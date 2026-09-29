"""Errors raised by the simulator client.

The simulator uses three error shapes (Integration Guide section 9):
  domain errors      {"detail": {"code": "ROUTE_DISRUPTED", "message": "..."}}
  injected faults    {"error":  {"code": "FAULT_INJECTED",  "message": "..."}}
  stream fault       {"detail": {"code": "FAULT_INJECTED"}}
  validation (422)   {"detail": [ ...pydantic errors... ]}
"""
from __future__ import annotations


class SimulatorError(Exception):
    """Base class for every simulator client failure."""


class CircuitOpenError(SimulatorError):
    """The breaker is open; the call was not attempted."""


class SimulatorUnavailable(SimulatorError):
    """Timeouts, connection errors or 5xx after all retries."""

    def __init__(self, message: str, status: int | None = None, injected: bool = False):
        super().__init__(message)
        self.status = status
        self.injected = injected


class InvalidResponseError(SimulatorError):
    """The simulator answered, but the body failed validation. Rejected, logged and alerted."""


class SimulatorAPIError(SimulatorError):
    """A 4xx domain error, e.g. 409 ROUTE_DISRUPTED. Not a sign the simulator is unhealthy."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def parse_error_body(status: int, body: object) -> tuple[str, str, bool]:
    """Return (code, message, injected) for any of the simulator's error shapes."""
    if isinstance(body, dict):
        for key in ("error", "detail"):
            inner = body.get(key)
            if isinstance(inner, dict) and "code" in inner:
                code = str(inner["code"])
                return code, str(inner.get("message", "")), code == "FAULT_INJECTED"
        if isinstance(body.get("detail"), list):
            return "VALIDATION_ERROR", str(body["detail"])[:500], False
    return f"HTTP_{status}", str(body)[:500] if body else "", False
