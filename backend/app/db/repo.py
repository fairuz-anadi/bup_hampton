"""Decision history and policy runs.

Reads are served from memory, so the UI and load tests never wait on the database. Every write
goes to memory first, then to Postgres. If Postgres is down, writes queue in memory and in a
JSONL file, and a background task flushes them when the database comes back. Losing the database
never stops operations.

Without DATABASE_URL the repo runs memory-only (unit tests, quick local runs).
"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict, deque
from datetime import UTC, datetime
from pathlib import Path

from app.contracts import ComponentHealth, DecisionRecord
from app.obs.logging import log_event
from app.obs.metrics import DB_BUFFERED, DB_WRITE_ERRORS

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    sim_tick    INTEGER NOT NULL,
    stage       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    record      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_tick_idx ON decisions (sim_tick DESC);
CREATE INDEX IF NOT EXISTS decisions_stage_idx ON decisions (stage);

CREATE TABLE IF NOT EXISTS policy_runs (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    policy      TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    run         JSONB NOT NULL
);
"""

UPSERT = """
INSERT INTO decisions (decision_id, sim_tick, stage, created_at, updated_at, record)
VALUES ($1, $2, $3, $4, now(), $5::jsonb)
ON CONFLICT (decision_id) DO UPDATE
SET stage = EXCLUDED.stage, updated_at = now(), record = EXCLUDED.record
"""

MAX_IN_MEMORY = 2000


class DecisionRepo:
    def __init__(self, dsn: str | None, buffer_path: str | Path = "/tmp/fuelguard-buffer.jsonl"):
        self.dsn = dsn
        self.buffer_path = Path(buffer_path)
        self._records: OrderedDict[str, DecisionRecord] = OrderedDict()
        self._policy_runs: deque[dict] = deque(maxlen=500)
        self._pending: deque[tuple[str, object]] = deque()
        self._pool = None
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if not self.dsn:
            return
        self._load_buffer_file()
        self._task = asyncio.create_task(self._maintain(), name="db-maintain")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._pool:
            await self._pool.close()

    async def _maintain(self) -> None:
        """Connect (and reconnect) to Postgres, then flush anything that was buffered."""
        import asyncpg

        backoff = 1.0
        while True:
            try:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5, timeout=5)
                    async with self._pool.acquire() as con:
                        await con.execute(SCHEMA)
                    await self._load_recent()
                    log_event("db.connected")
                    backoff = 1.0
                else:
                    # Ping, so an outage shows up in health even when nothing is being written.
                    async with self._pool.acquire(timeout=3) as con:
                        await con.fetchval("SELECT 1", timeout=3)
                await self._flush()
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:200]
                if self._pool is not None:
                    log_event("db.disconnected", error=self.last_error)
                    pool, self._pool = self._pool, None
                    pool.terminate()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _load_recent(self) -> None:
        async with self._pool.acquire() as con:
            rows = await con.fetch("SELECT record FROM decisions ORDER BY sim_tick DESC, created_at DESC LIMIT $1",
                                   MAX_IN_MEMORY)
        for row in reversed(rows):
            rec = DecisionRecord.model_validate_json(row["record"])
            self._records.setdefault(rec.decision_id, rec)

    # ------------------------------------------------------------------ writes

    async def save(self, record: DecisionRecord) -> None:
        self._records[record.decision_id] = record
        self._records.move_to_end(record.decision_id)
        while len(self._records) > MAX_IN_MEMORY:
            self._records.popitem(last=False)
        await self._persist("decision", record)

    async def save_policy_run(self, run: dict) -> None:
        run = {"created_at": datetime.now(UTC).isoformat(), **run}
        self._policy_runs.appendleft(run)
        await self._persist("policy_run", run)

    async def _persist(self, kind: str, item) -> None:
        if not self.dsn:
            return
        if self._pool is not None and not self._pending:
            try:
                await self._write(kind, item)
                return
            except Exception as exc:
                self.last_error = str(exc)[:200]
                DB_WRITE_ERRORS.inc()
                log_event("db.write_failed", kind=kind, error=self.last_error)
        self._buffer(kind, item)

    async def _write(self, kind: str, item) -> None:
        async with self._pool.acquire() as con:
            if kind == "decision":
                created = item.created_at
                if isinstance(created, str):  # contracts allow an ISO string here
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                await con.execute(UPSERT, item.decision_id, item.sim_tick, item.stage, created,
                                  item.model_dump_json())
            else:
                await con.execute("INSERT INTO policy_runs (policy, scenario_id, run) VALUES ($1, $2, $3::jsonb)",
                                  str(item.get("policy", "?")), str(item.get("scenario_id", "?")),
                                  json.dumps(item, default=str))

    def _buffer(self, kind: str, item) -> None:
        self._pending.append((kind, item))
        DB_BUFFERED.set(len(self._pending))
        try:
            payload = item.model_dump(mode="json") if isinstance(item, DecisionRecord) else item
            with self.buffer_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"kind": kind, "item": payload}, default=str) + "\n")
        except OSError as exc:
            log_event("db.buffer_file_failed", error=str(exc)[:200])

    async def _flush(self) -> None:
        if not self._pending:
            return
        count = len(self._pending)
        while self._pending:
            kind, item = self._pending[0]
            await self._write(kind, item)  # raises -> stays queued, retried next round
            self._pending.popleft()
            DB_BUFFERED.set(len(self._pending))
        self.buffer_path.unlink(missing_ok=True)
        log_event("db.buffer_flushed", records=count)

    def _load_buffer_file(self) -> None:
        """Records buffered before a backend restart are replayed on the next connection."""
        if not self.buffer_path.exists():
            return
        for line in self.buffer_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                item = (DecisionRecord.model_validate(entry["item"]) if entry["kind"] == "decision"
                        else entry["item"])
                self._pending.append((entry["kind"], item))
                if isinstance(item, DecisionRecord):
                    self._records[item.decision_id] = item
            except (ValueError, KeyError):
                continue
        self.buffer_path.unlink(missing_ok=True)
        DB_BUFFERED.set(len(self._pending))
        if self._pending:
            log_event("db.buffer_recovered", records=len(self._pending))

    # ------------------------------------------------------------------ reads (memory)

    def get(self, decision_id: str) -> DecisionRecord | None:
        return self._records.get(decision_id)

    def list(self, limit: int = 50, stage: str | None = None) -> list[DecisionRecord]:
        out = []
        for rec in reversed(self._records.values()):
            if stage is None or rec.stage == stage:
                out.append(rec)
                if len(out) >= limit:
                    break
        return out

    def by_stage(self, *stages: str) -> list[DecisionRecord]:
        return [r for r in self._records.values() if r.stage in stages]

    def policy_runs(self, limit: int = 50) -> list[dict]:
        return list(self._policy_runs)[:limit]

    # ------------------------------------------------------------------ health

    def health(self) -> ComponentHealth:
        if not self.dsn:
            return ComponentHealth(name="Database", status="unknown", detail="DATABASE_URL not set; memory only")
        if self._pool is None:
            return ComponentHealth(name="Database", status="down",
                                   detail=f"{len(self._pending)} records buffered; {self.last_error or 'connecting'}")
        if self._pending:
            return ComponentHealth(name="Database", status="degraded", detail=f"flushing {len(self._pending)} records")
        return ComponentHealth(name="Database", status="healthy")
