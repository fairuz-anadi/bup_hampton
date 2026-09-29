"""Runtime controls: the pacer and the active-policy switch.

Pacer: SIMULATION_SPEED is fixed when the simulator container starts, so for a demo we pause the
simulator and step it ourselves at a pace humans can follow (default 1 tick per second).

Policy switch: which allocation policy the intelligence layer should use. Rolling back is just
switching to the last accepted policy; the change is logged and shown in health.
"""
from __future__ import annotations

import asyncio

from app.obs.logging import log_event
from app.obs.metrics import PACER_RUNNING
from app.sim.client import SimulatorClient
from app.sim.errors import SimulatorError


class Pacer:
    def __init__(self, client: SimulatorClient, on_tick=None):
        self.client = client
        self.on_tick = on_tick  # called after each step, e.g. to refresh state right away
        self.interval_ms = 1000
        self.ticks_done = 0
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, interval_ms: int, max_ticks: int | None = None) -> None:
        await self.stop()
        self.interval_ms, self.ticks_done, self.last_error = interval_ms, 0, None
        await self.client.admin("POST", "/admin/pause")
        self._task = asyncio.create_task(self._run(max_ticks), name="pacer")
        PACER_RUNNING.set(1)
        log_event("pacer.started", interval_ms=interval_ms, max_ticks=max_ticks)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
            PACER_RUNNING.set(0)
            log_event("pacer.stopped", ticks=self.ticks_done)

    async def _run(self, max_ticks: int | None) -> None:
        try:
            while max_ticks is None or self.ticks_done < max_ticks:
                try:
                    await self.client.admin("POST", "/admin/step")
                    self.ticks_done += 1
                    if self.on_tick:
                        await self.on_tick()
                except SimulatorError as exc:
                    self.last_error = str(exc)[:200]
                    log_event("pacer.step_failed", error=self.last_error)
                await asyncio.sleep(self.interval_ms / 1000)
        finally:
            PACER_RUNNING.set(0)

    def status(self) -> dict:
        return {"running": self.running, "interval_ms": self.interval_ms, "ticks_done": self.ticks_done,
                "last_error": self.last_error}


class PolicySwitch:
    def __init__(self, active: str = "greedy-v1"):
        self.active = active
        self.accepted = active
        self.history: list[dict] = []

    def set(self, policy: str, by: str, accept: bool = False) -> None:
        self.history.append({"from": self.active, "to": policy, "by": by, "accepted": accept})
        self.active = policy
        if accept:
            self.accepted = policy
        log_event("policy.switched", to=policy, by=by, accepted=accept)

    def rollback(self, by: str) -> None:
        self.set(self.accepted, by)
        log_event("policy.rollback", to=self.accepted, by=by)

    def status(self) -> dict:
        return {"active": self.active, "accepted": self.accepted, "history": self.history[-10:]}
