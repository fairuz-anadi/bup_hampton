"""Async client for the official BUP Fuel Supply Simulator.

Every /v1/* call goes through the same path:
  breaker check -> request with timeout -> retry transient failures with jittered backoff
  -> parse the error shape -> validate the body with Pydantic -> report stale header.

/v1/health and /admin/* bypass fault injection on the simulator side, so they also bypass the
breaker here: health is the probe that tells "faulted" apart from "down".
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from app.contracts import (
    Allocation,
    DemandObservation,
    Depot,
    Instance,
    Region,
    Route,
    SimEvent,
    SimMetrics,
    Station,
    SupplyArrival,
)
from app.obs.logging import log_event
from app.obs.metrics import SIM_INVALID, SIM_LATENCY, SIM_REQUESTS, SIM_RETRIES, endpoint_label
from app.sim.breaker import CircuitBreaker
from app.sim.errors import (
    CircuitOpenError,
    InvalidResponseError,
    SimulatorAPIError,
    SimulatorUnavailable,
    parse_error_body,
)

T = TypeVar("T")
STALE_HEADER = "x-simulator-stale"


@dataclass(frozen=True)
class Fetched(Generic[T]):
    data: T
    stale: bool
    latency_ms: float


_ADAPTERS: dict[Any, TypeAdapter] = {}


def _adapter(tp: Any) -> TypeAdapter:
    if tp not in _ADAPTERS:
        _ADAPTERS[tp] = TypeAdapter(tp)
    return _ADAPTERS[tp]


class SimulatorClient:
    def __init__(self, base_url: str, *, timeout: float = 3.0, retries: int = 2, backoff_base: float = 0.2,
                 breaker: CircuitBreaker | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.backoff_base = backoff_base
        self.breaker = breaker or CircuitBreaker()
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core request path

    async def _request(self, method: str, path: str, *, json_body: dict | None = None,
                       params: dict | None = None, use_breaker: bool = True) -> tuple[Any, bool, float]:
        """Returns (parsed_json, stale, latency_ms). Raises a SimulatorError subclass on failure."""
        label = endpoint_label(path)
        if use_breaker and not self.breaker.allow():
            SIM_REQUESTS.labels(label, "circuit_open").inc()
            raise CircuitOpenError(f"simulator circuit open, {method} {path} not attempted")

        last: SimulatorUnavailable | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                SIM_RETRIES.labels(label).inc()
                await asyncio.sleep(self.backoff_base * (2 ** (attempt - 1)) * random.uniform(0.5, 1.5))
            started = time.perf_counter()
            try:
                resp = await self._http.request(method, path, json=json_body, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                SIM_REQUESTS.labels(label, type(exc).__name__).inc()
                last = SimulatorUnavailable(f"{method} {path}: {type(exc).__name__}")
                continue
            latency_ms = (time.perf_counter() - started) * 1000
            SIM_LATENCY.labels(label).observe(latency_ms / 1000)
            SIM_REQUESTS.labels(label, str(resp.status_code)).inc()

            body = _json_or_none(resp)
            if resp.status_code >= 500:
                code, message, injected = parse_error_body(resp.status_code, body)
                last = SimulatorUnavailable(f"{method} {path}: {resp.status_code} {code} {message}".strip(),
                                            status=resp.status_code, injected=injected)
                continue
            if resp.status_code >= 400:
                # A domain answer from a healthy simulator: not a breaker failure, not retried.
                if use_breaker:
                    self.breaker.record_success()
                code, message, _ = parse_error_body(resp.status_code, body)
                raise SimulatorAPIError(resp.status_code, code, message)
            if use_breaker:
                self.breaker.record_success()
            return body, resp.headers.get(STALE_HEADER, "").lower() == "true", latency_ms

        if use_breaker:
            self.breaker.record_failure()
        log_event("sim.request_failed", method=method, path=path, status=last.status, injected=last.injected,
                  error=str(last))
        raise last

    async def _get(self, path: str, tp: Any, params: dict | None = None) -> Fetched:
        body, stale, latency = await self._request("GET", path, params=params)
        return Fetched(self._validate(path, tp, body), stale, latency)

    def _validate(self, path: str, tp: Any, body: Any) -> Any:
        try:
            return _adapter(tp).validate_python(body)
        except ValidationError as exc:
            SIM_INVALID.labels(endpoint_label(path)).inc()
            self.breaker.record_failure()
            log_event("sim.invalid_response", path=path, errors=exc.error_count(), sample=str(exc)[:300])
            raise InvalidResponseError(f"{path}: {exc.error_count()} validation errors") from exc

    # ------------------------------------------------------------------ public reads

    async def health(self) -> dict:
        """Fault-free liveness probe. Bypasses the breaker so it can tell 'faulted' from 'down'."""
        body, _, _ = await self._request("GET", "/v1/health", use_breaker=False)
        return body

    async def instance(self) -> Fetched[Instance]:
        return await self._get("/v1/instance", Instance)

    async def regions(self) -> Fetched[list[Region]]:
        return await self._get("/v1/regions", list[Region])

    async def depots(self) -> Fetched[list[Depot]]:
        return await self._get("/v1/depots", list[Depot])

    async def stations(self) -> Fetched[list[Station]]:
        return await self._get("/v1/stations", list[Station])

    async def routes(self) -> Fetched[list[Route]]:
        return await self._get("/v1/routes", list[Route])

    async def supply_arrivals(self) -> Fetched[list[SupplyArrival]]:
        return await self._get("/v1/supply-arrivals", list[SupplyArrival])

    async def events(self) -> Fetched[list[SimEvent]]:
        return await self._get("/v1/events", list[SimEvent])

    async def allocations(self) -> Fetched[list[Allocation]]:
        return await self._get("/v1/allocations", list[Allocation])

    async def metrics(self) -> Fetched[SimMetrics]:
        return await self._get("/v1/metrics", SimMetrics)

    async def demand_history(self, limit: int = 200, station_id: str | None = None) -> Fetched[list[DemandObservation]]:
        params: dict[str, Any] = {"limit": max(1, min(limit, 2000))}
        if station_id:
            params["station_id"] = station_id
        return await self._get("/v1/demand-history", list[DemandObservation], params=params)

    # ------------------------------------------------------------------ the only domain writes

    async def create_allocation(self, *, idempotency_key: str, source_depot_id: str, destination_station_id: str,
                                route_id: str, fuel_type: str, quantity: float) -> Allocation:
        """Safe to retry: the same key and body return the existing allocation (201)."""
        body, _, _ = await self._request("POST", "/v1/allocations", json_body={
            "idempotency_key": idempotency_key, "source_depot_id": source_depot_id,
            "destination_station_id": destination_station_id, "route_id": route_id,
            "fuel_type": fuel_type, "quantity": quantity})
        return self._validate("/v1/allocations", Allocation, body)

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        body, _, _ = await self._request("POST", f"/v1/allocations/{allocation_id}/cancel")
        return self._validate("/v1/allocations/{id}/cancel", Allocation, body)

    # ------------------------------------------------------------------ admin (organizer / Chaos Lab / pacer)

    async def admin(self, method: str, path: str, json_body: dict | None = None, params: dict | None = None) -> Any:
        if not path.startswith("/admin/"):
            raise ValueError("admin() only calls /admin/* paths")
        body, _, _ = await self._request(method, path, json_body=json_body, params=params, use_breaker=False)
        return body

    # ------------------------------------------------------------------ SSE

    async def stream(self) -> AsyncIterator[tuple[str, Any]]:
        """Yields (event_name, data) from /v1/stream until the connection drops.

        The first item is always ("stream.open", None). Comments (': connected', ': keepalive') are
        skipped. There is no replay, so the caller must re-sync over REST after every (re)connect.
        """
        async with self._http.stream("GET", "/v1/stream", timeout=httpx.Timeout(None, connect=5.0)) as resp:
            if resp.status_code != 200:
                await resp.aread()
                code, message, injected = parse_error_body(resp.status_code, _json_or_none(resp))
                raise SimulatorUnavailable(f"/v1/stream: {resp.status_code} {code} {message}".strip(),
                                           status=resp.status_code, injected=injected)
            # A paused simulator sends no events for a long time; tell the caller the stream is up.
            yield "stream.open", None
            event, data_lines = "message", []
            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        raw = "\n".join(data_lines)
                        try:
                            yield event, json.loads(raw)
                        except json.JSONDecodeError:
                            yield event, raw
                    event, data_lines = "message", []
                elif line.startswith(":"):
                    continue
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
