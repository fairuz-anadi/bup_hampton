"""Circuit breaker around the simulator client.

    CLOSED --N failures in W seconds--> OPEN --cool-down--> HALF_OPEN --probe ok--> CLOSED
                                          ^                     |
                                          +----probe fails------+

While OPEN the client refuses calls immediately, so a dead or faulted simulator can't pile up
timeouts inside the backend. In HALF_OPEN exactly one call is let through as a probe.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Literal

from app.obs.logging import log_event
from app.obs.metrics import SIM_CIRCUIT

State = Literal["CLOSED", "OPEN", "HALF_OPEN"]
_GAUGE = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, window_seconds: float = 10.0, cooldown_seconds: float = 15.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._failures: deque[float] = deque()
        self._state: State = "CLOSED"
        self._opened_at = 0.0
        self._probe_in_flight = False
        SIM_CIRCUIT.set(0)

    @property
    def state(self) -> State:
        if self._state == "OPEN" and self._clock() - self._opened_at >= self.cooldown_seconds:
            self._set("HALF_OPEN")
        return self._state

    def allow(self) -> bool:
        """Call before each request. False means: don't call, the circuit is open."""
        state = self.state
        if state == "CLOSED":
            return True
        if state == "HALF_OPEN" and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._probe_in_flight = False
        self._failures.clear()
        if self._state != "CLOSED":
            self._set("CLOSED")

    def record_failure(self) -> None:
        now = self._clock()
        self._probe_in_flight = False
        if self._state == "HALF_OPEN":
            self._open(now)
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()
        if self._state == "CLOSED" and len(self._failures) >= self.failure_threshold:
            self._open(now)

    def _open(self, now: float) -> None:
        self._opened_at = now
        self._failures.clear()
        self._set("OPEN")

    def _set(self, state: State) -> None:
        if state != self._state:
            log_event({"OPEN": "sim.circuit_opened", "HALF_OPEN": "sim.circuit_half_open",
                       "CLOSED": "sim.circuit_closed"}[state], previous=self._state)
        self._state = state
        SIM_CIRCUIT.set(_GAUGE[state])
