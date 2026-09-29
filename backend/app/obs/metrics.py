"""Prometheus metrics. Names follow the blueprint's observability table (section 09)."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("fuelguard_http_requests_total", "Backend API requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("fuelguard_http_request_duration_seconds", "Backend API latency", ["method", "route"],
                         buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5))

SIM_REQUESTS = Counter("fuelguard_sim_requests_total", "Calls to the official simulator", ["endpoint", "code"])
SIM_LATENCY = Histogram("fuelguard_sim_request_duration_seconds", "Simulator call latency", ["endpoint"],
                        buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5))
SIM_RETRIES = Counter("fuelguard_sim_retries_total", "Simulator calls retried", ["endpoint"])
SIM_CIRCUIT = Gauge("fuelguard_sim_circuit_state", "0 = closed, 1 = half-open, 2 = open")
SIM_INVALID = Counter("fuelguard_sim_invalid_responses_total", "Simulator responses rejected by validation",
                      ["endpoint"])

SNAPSHOT_AGE = Gauge("fuelguard_snapshot_age_seconds", "Age of the oldest resource in the current snapshot")
SNAPSHOT_STALE = Gauge("fuelguard_snapshot_stale", "1 if the current snapshot is stale")
SNAPSHOT_TICK = Gauge("fuelguard_sim_tick", "Simulator tick in the current snapshot")
SERVICE_LEVEL = Gauge("fuelguard_sim_service_level", "Simulator service level (ground truth)")
SSE_RECONNECTS = Counter("fuelguard_sse_reconnects_total", "SSE stream reconnects")

ALLOCATIONS = Counter("fuelguard_allocations_total", "Allocation legs by result", ["result", "code"])
FALLBACKS = Counter("fuelguard_fallback_activations_total", "Fallbacks activated", ["component"])

DECISIONS = Counter("fuelguard_decisions_total", "Decision records reaching a stage", ["stage"])
TWIN_ERROR = Gauge("fuelguard_twin_error_liters", "Latest |projected - actual| network unmet over a decision horizon")
TWIN_ERROR_HIST = Histogram("fuelguard_twin_error_liters_hist", "Twin error per verified decision",
                            buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000))
DB_BUFFERED = Gauge("fuelguard_db_buffered", "Records waiting for the database")
DB_WRITE_ERRORS = Counter("fuelguard_db_write_errors_total", "Database write failures")
PACER_RUNNING = Gauge("fuelguard_pacer_running", "1 while the backend pacer is stepping the simulator")


def endpoint_label(path: str) -> str:
    """Collapse ids so label cardinality stays small: /v1/stations/station-mirpur -> /v1/stations/{id}."""
    parts = path.split("?")[0].strip("/").split("/")
    keep = []
    for i, p in enumerate(parts):
        if i >= 2 and parts[i - 1] in {"stations", "depots", "allocations"} and p not in {"cancel"}:
            keep.append("{id}")
        else:
            keep.append(p)
    return "/" + "/".join(keep)
