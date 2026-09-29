"""Shared fixtures: a fake simulator behind httpx.MockTransport, so tests need no Docker."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "simulator_tick0.json"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeSim:
    """Serves the recorded tick-0 world. Tests mutate `.world`, `.fail` and `.stale` to script faults."""

    def __init__(self):
        self.world = json.loads(FIXTURE.read_text())
        self.fail: dict[str, int] = {}      # path -> status code to return instead
        self.fail_times: dict[str, int] = {}  # path -> how many more times to fail (missing = forever)
        self.stale = False
        self.calls: list[tuple[str, str]] = []
        self.posted: dict[str, dict] = {}
        self.admin_calls: list[tuple[str, str, object]] = []
        self.world["demand_history"] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path in self.fail and self.fail_times.get(path, 1) > 0:
            if path in self.fail_times:
                self.fail_times[path] -= 1
            status = self.fail[path]
            return httpx.Response(status, json={"error": {"code": "FAULT_INJECTED", "message": "injected"}})
        headers = {"X-Simulator-Stale": "true"} if self.stale and path.startswith("/v1/") else {}
        if request.method == "POST" and path == "/v1/allocations":
            body = json.loads(request.content)
            prev = self.posted.get(body["idempotency_key"])
            if prev and prev["body"] != body:
                return httpx.Response(409, json={"detail": {"code": "IDEMPOTENCY_KEY_MISMATCH", "message": "x"}})
            if not prev:
                alloc = {"id": len(self.posted) + 1, "idempotency_key": body["idempotency_key"],
                         "source_depot_id": body["source_depot_id"],
                         "destination_station_id": body["destination_station_id"], "route_id": body["route_id"],
                         "fuel_type": body["fuel_type"], "quantity": body["quantity"],
                         "created_tick": self.world["instance"]["tick"], "status": "PENDING"}
                self.posted[body["idempotency_key"]] = {"body": body, "alloc": alloc}
                self.world["allocations"].insert(0, alloc)
            return httpx.Response(201, json=self.posted[body["idempotency_key"]]["alloc"])
        if path == "/v1/health":
            return httpx.Response(200, json={"status": "ok"})
        if path.startswith("/admin/"):
            body = json.loads(request.content) if request.content else None
            self.admin_calls.append((request.method, path, body))
            if path == "/admin/step":
                self.world["instance"]["tick"] += 1
                return httpx.Response(200, json={"tick": self.world["instance"]["tick"]})
            return httpx.Response(201 if body else 200, json=body or {"status": "ok"})
        key = path.removeprefix("/v1/").replace("-", "_")
        if key in self.world:
            return httpx.Response(200, json=self.world[key], headers=headers)
        return httpx.Response(404, json={"detail": {"code": "NOT_FOUND", "message": path}})


@pytest.fixture
def fake_sim() -> FakeSim:
    return FakeSim()


@pytest.fixture
def transport(fake_sim) -> httpx.MockTransport:
    return httpx.MockTransport(fake_sim.handler)
