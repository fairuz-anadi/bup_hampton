"""FuelGuard backend entry point: `uvicorn app.main:app`."""
from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.routes import router
from app.config import Settings, get_settings
from app.contracts import ComponentHealth
from app.obs.logging import log_event, setup_logging
from app.obs.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.sim.allocations import AllocationWriter
from app.sim.breaker import CircuitBreaker
from app.sim.client import SimulatorClient
from app.state.store import StateStore
from app.state.sync import SyncService


@dataclass
class Services:
    settings: Settings
    client: SimulatorClient
    store: StateStore
    sync: SyncService
    writer: AllocationWriter
    demand_cache: dict = field(default_factory=dict)
    # Other lanes register a health probe here, e.g. the forecaster client.
    health_probes: list[Callable[[], ComponentHealth]] = field(default_factory=list)

    def extra_health(self) -> list[ComponentHealth]:
        out = []
        for probe in self.health_probes:
            try:
                out.append(probe())
            except Exception as exc:  # a broken probe must not break /api/health
                out.append(ComponentHealth(name=getattr(probe, "__name__", "probe"), status="unknown",
                                           detail=repr(exc)[:120]))
        return out


def build_services(settings: Settings, transport=None) -> Services:
    breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_window_seconds,
                             settings.breaker_cooldown_seconds)
    client = SimulatorClient(settings.simulator_url, timeout=settings.sim_timeout_seconds,
                             retries=settings.sim_retries, backoff_base=settings.sim_backoff_base_seconds,
                             breaker=breaker, transport=transport)
    store = StateStore(client)
    return Services(settings=settings, client=client, store=store,
                    sync=SyncService(store, settings.poll_interval_seconds, settings.sse_enabled),
                    writer=AllocationWriter(client, store))


def create_app(settings: Settings | None = None, services: Services | None = None,
               start_sync: bool = True) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services(settings)
        app.state.services = svc
        if start_sync:
            svc.sync.start()
        log_event("backend.started", simulator=settings.simulator_url, version=settings.deployment_version,
                  writes_enabled=bool(settings.operator_key.get_secret_value()))
        yield
        await svc.sync.stop()
        await svc.client.aclose()

    app = FastAPI(title="FuelGuard backend", version=settings.deployment_version, lifespan=lifespan,
                  description="Decision-support backend for the BUP Fuel Supply Simulator. SIMULATED data only.")

    @app.middleware("http")
    async def http_metrics(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        label = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, label, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, label).observe(time.perf_counter() - started)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"service": "fuelguard-backend", "version": settings.deployment_version, "docs": "/docs",
                "note": "SIMULATED environment. No real fuel infrastructure is accessed."}

    app.include_router(router)
    return app


app = create_app()
