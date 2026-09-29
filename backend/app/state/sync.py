"""Keeps the state store in sync with the simulator.

Two loops run side by side:
  poller   refreshes over REST every poll_interval seconds. REST is the source of truth, so this
           alone is enough to stay correct.
  sse      listens on /v1/stream and triggers an immediate refresh on every tick, so the UI
           updates within a tick instead of within a poll interval. On any drop it reconnects with
           backoff and re-syncs over REST, because the stream has no replay.
"""
from __future__ import annotations

import asyncio
import random

from app.obs.logging import log_event
from app.obs.metrics import SSE_RECONNECTS
from app.sim.errors import SimulatorError
from app.state.store import StateStore

# SSE events that mean "the world changed, re-read it".
REFRESH_EVENTS = {"simulation.tick", "allocation.status_changed", "inventory.updated", "simulator.notice"}
MIN_REFRESH_GAP = 0.5  # seconds between two poller refreshes, however many events arrive


class SyncService:
    def __init__(self, store: StateStore, poll_interval: float = 1.0, sse_enabled: bool = True):
        self.store = store
        self.poll_interval = poll_interval
        self.sse_enabled = sse_enabled
        self.sse_connected = False
        self._tasks: list[asyncio.Task] = []
        self._kick = asyncio.Event()

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._poll_loop(), name="state-poller"))
        if self.sse_enabled:
            self._tasks.append(asyncio.create_task(self._sse_loop(), name="state-sse"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.store.refresh()
            except Exception as exc:  # never let the loop die
                log_event("state.poll_error", error=repr(exc))
            # Every allocation fires SSE events. Without a gap, a burst of them keeps the (single-threaded)
            # simulator busy serving our full refreshes and slows down everyone's writes.
            await asyncio.sleep(MIN_REFRESH_GAP)
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass
            self._kick.clear()

    async def _sse_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async for event, _data in self.store.client.stream():
                    if not self.sse_connected:
                        self.sse_connected, backoff = True, 1.0
                        log_event("sse.connected")
                        self._kick.set()  # no replay: re-sync over REST right after connecting
                    if event in REFRESH_EVENTS:
                        self._kick.set()
                    if event == "simulator.notice":
                        log_event("sim.notice", data=_data)
                raise SimulatorError("stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.sse_connected:
                    log_event("sse.disconnected", error=str(exc)[:200])
                self.sse_connected = False
                SSE_RECONNECTS.inc()
                await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
                backoff = min(backoff * 2, 30.0)
