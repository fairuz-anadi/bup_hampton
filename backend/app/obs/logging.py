"""JSON-lines logging to stdout. Use log_event() for anything an operator or judge might look for."""
import json
import logging
import sys
from datetime import UTC, datetime

_logger = logging.getLogger("fuelguard")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.name),
        }
        out.update(getattr(record, "fields", {}))
        if record.getMessage() and record.getMessage() != out["event"]:
            out["msg"] = record.getMessage()
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's access log duplicates our HTTP metrics; keep its errors only.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    """log_event("sim.circuit_opened", failures=5) -> {"event": "sim.circuit_opened", "failures": 5, ...}"""
    _logger.log(level, event, extra={"event": event, "fields": fields})
