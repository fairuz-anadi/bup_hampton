"""FuelGuard backend entry point: `uvicorn app.main:app`."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.auth import auth_router
from app.api.control_routes import router as control_router
from app.api.gauntlet_routes import router as gauntlet_router
from app.api.rag_routes import router as rag_router
from app.api.rl_routes import router as rl_router
from app.api.routes import router
from app.chat import ChatRepo, ChatService, chat_router
from app.config import Settings, get_settings
from app.contracts import ComponentHealth
from app.db.repo import DecisionRepo
from app.decisions.engine import DecisionEngine
from app.decisions.routes import components, demand_history
from app.decisions.routes import router as decisions_router
from app.decisions.service import DecisionService
from app.explain.service import Explainer
from app.obs.logging import log_event, setup_logging
from app.obs.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.ops.control import Pacer, PolicySwitch
from app.sim.allocations import AllocationWriter
from app.sim.breaker import CircuitBreaker
from app.sim.client import SimulatorClient
from app.state.store import StateStore
from app.state.sync import SyncService

HealthProbe = Callable[[], Awaitable[ComponentHealth]]


@dataclass
class Services:
    settings: Settings
    client: SimulatorClient
    store: StateStore
    sync: SyncService
    writer: AllocationWriter
    repo: DecisionRepo
    decisions: DecisionService
    pacer: Pacer
    policy: PolicySwitch
    engine: DecisionEngine | None = None      # per-tick recommendation -> confidence -> gate (decisions/engine.py)
    explainer: Explainer | None = None        # templates + LangGraph copilot (explain/)
    chat_repo: ChatRepo | None = None
    chat: ChatService | None = None
    rag: Any | None = None
    rl: Any | None = None
    # Result of the background /v1/health probe: {"alive", "checked_at", "latency_ms", "pending_since"}
    sim_probe: dict = field(default_factory=dict)
    demand_cache: dict = field(default_factory=dict)
    # Other lanes register an async health probe here (forecaster, decision engine, copilot).
    health_probes: list[HealthProbe] = field(default_factory=list)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    async def extra_health(self) -> list[ComponentHealth]:
        out = [self.repo.health()]
        for probe in self.health_probes:
            try:
                out.append(await asyncio.wait_for(probe(), timeout=2))
            except Exception as exc:  # a broken probe must not break /api/health
                out.append(ComponentHealth(name=getattr(probe, "__name__", "probe"), status="unknown",
                                           detail=repr(exc)[:120]))
        return out

    async def start(self, start_sync: bool) -> None:
        await self.repo.start()
        if self.chat_repo:
            await self.chat_repo.start()
        if self.rag:
            await self.rag.initialize()
        self._tasks.append(asyncio.create_task(self._probe_loop(), name="sim-health-probe"))
        if start_sync:
            self.sync.start()
            self._tasks.append(asyncio.create_task(self._outcome_loop(), name="outcome-check"))
            if self.engine is not None:
                self._tasks.append(asyncio.create_task(self.engine.run(
                    lambda: self.store.snapshot, lambda: components(self), lambda: demand_history(self),
                    lambda: self.policy.active, self.settings.poll_interval_seconds), name="decision-engine"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.pacer.stop()
        await self.sync.stop()
        if self.chat_repo:
            await self.chat_repo.stop()
        await self.repo.stop()
        await self.client.aclose()

    async def _outcome_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.outcome_check_seconds)
            try:
                await self.decisions.check_outcomes()
            except Exception as exc:
                log_event("decision.outcome_check_failed", error=repr(exc)[:200])

    async def _probe_loop(self) -> None:
        """One /v1/health probe at a time, every 2 s. /api/health reads the result and never waits on the
        simulator; a probe that hasn't answered yet shows up as 'slow'."""
        while True:
            self.sim_probe["pending_since"] = time.monotonic()
            started = time.perf_counter()
            try:
                await self.client.health()
                alive = True
            except Exception:
                alive = False
            self.sim_probe.update(alive=alive, checked_at=time.monotonic(), pending_since=None,
                                  latency_ms=round((time.perf_counter() - started) * 1000, 1))
            await asyncio.sleep(2.0)


def _forecaster_probe(url: str) -> HealthProbe:
    async def forecaster() -> ComponentHealth:
        try:
            async with httpx.AsyncClient(timeout=1.5) as http:
                r = await http.get(f"{url.rstrip('/')}/health")
            if r.status_code == 200:
                return ComponentHealth(name="Forecaster", status="healthy")
            return ComponentHealth(name="Forecaster", status="degraded", detail=f"/health {r.status_code}")
        except httpx.HTTPError as exc:
            return ComponentHealth(name="Forecaster", status="down", detail=type(exc).__name__)
    return forecaster


def build_services(settings: Settings, transport=None) -> Services:
    breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_window_seconds,
                             settings.breaker_cooldown_seconds)
    client = SimulatorClient(settings.simulator_url, read_timeout=settings.sim_timeout_seconds,
                             connect_timeout=settings.sim_connect_timeout_seconds,
                             max_concurrency=settings.sim_max_concurrency, retries=settings.sim_retries,
                             backoff_base=settings.sim_backoff_base_seconds, breaker=breaker, transport=transport)
    store = StateStore(client)
    writer = AllocationWriter(client, store)
    repo = DecisionRepo(settings.database_url or None, settings.db_buffer_path)

    async def refresh_after_step():
        with suppress(Exception):
            await store.refresh()

    svc = Services(settings=settings, client=client, store=store,
                   sync=SyncService(store, settings.poll_interval_seconds, settings.sse_enabled),
                   writer=writer, repo=repo,
                   decisions=DecisionService(repo, writer, store, settings.deployment_version),
                   pacer=Pacer(client, on_tick=refresh_after_step), policy=PolicySwitch(settings.default_policy))
    if settings.forecaster_url:
        svc.health_probes.append(_forecaster_probe(settings.forecaster_url))
    svc.engine = DecisionEngine(svc.decisions)
    svc.explainer = Explainer()

    chat_repo = ChatRepo(settings.database_url or None, settings.chat_db_buffer_path)
    api_key_val = settings.ai_api_key.get_secret_value() or os.getenv("OPENAI_API_KEY", "")
    chat_svc = ChatService(
        repo=chat_repo,
        provider=settings.ai_provider,
        model=settings.ai_model,
        api_key=api_key_val,
        base_url=settings.ai_base_url or None,
        timeout_seconds=settings.chat_timeout_seconds,
        max_history_turns=settings.chat_max_history,
    )
    svc.chat_repo = chat_repo
    svc.chat = chat_svc

    async def decision_engine() -> ComponentHealth:
        return svc.engine.health()

    async def explanation() -> ComponentHealth:
        info = svc.explainer.describe()
        detail = f"LangGraph + {info['llm']}" if info["llm"] else "template explanations (LLM off)"
        return ComponentHealth(name="Explanation", status="healthy", detail=detail)

    async def chat_health() -> ComponentHealth:
        return svc.chat.health()

    from app.rag.pipeline import get_rag_pipeline
    rag_pipe = get_rag_pipeline(settings.database_url or None)
    svc.rag = rag_pipe

    async def rag_health() -> ComponentHealth:
        return svc.rag.health()

    from app.rl.inference.predictor import get_rl_predictor
    rl_pred = get_rl_predictor()
    svc.rl = rl_pred

    async def rl_health() -> ComponentHealth:
        return svc.rl.health()

    svc.health_probes += [decision_engine, explanation, chat_health, rag_health, rl_health]
    return svc


def create_app(settings: Settings | None = None, services: Services | None = None,
               start_sync: bool = True) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = services or build_services(settings)
        app.state.services = svc
        await svc.start(start_sync)
        log_event("backend.started", simulator=settings.simulator_url, version=settings.deployment_version,
                  writes_enabled=bool(settings.operator_key.get_secret_value()),
                  database=bool(settings.database_url), forecaster=settings.forecaster_url or None)
        yield
        await svc.stop()

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
    app.include_router(auth_router)
    app.include_router(control_router)
    app.include_router(decisions_router)
    app.include_router(gauntlet_router)
    app.include_router(chat_router)
    app.include_router(rag_router)
    app.include_router(rl_router)
    return app


app = create_app()
