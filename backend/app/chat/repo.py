"""Chat session and message persistence with PostgreSQL and offline fallback."""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict, deque
from datetime import UTC, datetime
from pathlib import Path

from app.chat.contracts import ChatMessage
from app.contracts import ComponentHealth
from app.obs.logging import log_event

CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES chat_conversations(conversation_id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_conv_idx ON chat_messages (conversation_id, created_at ASC);
"""

MAX_CONVERSATIONS_IN_MEMORY = 200
MAX_MESSAGES_PER_CONVERSATION = 50


class ChatRepo:
    def __init__(
        self,
        dsn: str | None,
        buffer_path: str | Path = "/tmp/fuelguard-chat-buffer.jsonl",
    ):
        self.dsn = dsn
        self.buffer_path = Path(buffer_path)
        self._conversations: OrderedDict[str, list[ChatMessage]] = OrderedDict()
        self._pending: deque[tuple[str, ChatMessage]] = deque()
        self._pool = None
        self._task: asyncio.Task | None = None
        self.last_error: str | None = None

    async def start(self) -> None:
        if not self.dsn:
            return
        self._load_buffer_file()
        self._task = asyncio.create_task(self._maintain(), name="chat-db-maintain")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._pool:
            await self._pool.close()

    def health(self) -> ComponentHealth:
        if not self.dsn:
            return ComponentHealth(
                name="Chat Database",
                status="healthy",
                detail="in-memory only (no DATABASE_URL)",
            )
        if self._pool is None or self.last_error:
            buffered = len(self._pending)
            detail = f"disconnected; {buffered} messages buffered"
            return ComponentHealth(
                name="Chat Database", status="degraded", detail=detail
            )
        return ComponentHealth(name="Chat Database", status="healthy")

    # ------------------------------------------------------------------ in-memory operations

    def add_message(self, conversation_id: str, message: ChatMessage) -> None:
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = []
            if len(self._conversations) > MAX_CONVERSATIONS_IN_MEMORY:
                self._conversations.popitem(last=False)
        else:
            self._conversations.move_to_end(conversation_id)

        history = self._conversations[conversation_id]
        history.append(message)
        if len(history) > MAX_MESSAGES_PER_CONVERSATION:
            self._conversations[conversation_id] = history[-MAX_MESSAGES_PER_CONVERSATION:]

        if self.dsn:
            self._pending.append((conversation_id, message))
            self._append_buffer_file(conversation_id, message)

    def get_history(self, conversation_id: str) -> list[ChatMessage]:
        return list(self._conversations.get(conversation_id, []))

    def get_context_window(
        self, conversation_id: str, max_messages: int = 10
    ) -> list[ChatMessage]:
        """Returns the most recent N messages for context management."""
        history = self._conversations.get(conversation_id, [])
        return list(history[-max_messages:])

    def clear(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)

    # ------------------------------------------------------------------ persistence & buffering

    def _append_buffer_file(self, conversation_id: str, message: ChatMessage) -> None:
        try:
            self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"conversation_id": conversation_id, "message": message.model_dump(mode="json")}
            with self.buffer_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            log_event("chat.db.buffer_write_failed", error=str(exc))

    def _load_buffer_file(self) -> None:
        if not self.buffer_path.exists():
            return
        try:
            with self.buffer_path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    conv_id = entry["conversation_id"]
                    msg = ChatMessage.model_validate(entry["message"])
                    self._pending.append((conv_id, msg))
                    if conv_id not in self._conversations:
                        self._conversations[conv_id] = []
                    self._conversations[conv_id].append(msg)
        except Exception as exc:
            log_event("chat.db.buffer_load_failed", error=str(exc))

    async def _maintain(self) -> None:
        import asyncpg

        while True:
            try:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self.dsn, min_size=1, max_size=3, timeout=5
                    )
                    async with self._pool.acquire() as con:
                        await con.execute(CHAT_SCHEMA)
                    self.last_error = None
                    log_event("chat.db.connected")
                else:
                    async with self._pool.acquire(timeout=3) as con:
                        await con.fetchval("SELECT 1", timeout=3)
                    self.last_error = None

                await self._flush()
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                if self._pool:
                    try:
                        await self._pool.close()
                    except Exception:
                        pass
                    self._pool = None
                await asyncio.sleep(3.0)

    async def _flush(self) -> None:
        if not self._pool or not self._pending:
            return

        batch: list[tuple[str, ChatMessage]] = []
        while self._pending and len(batch) < 100:
            batch.append(self._pending.popleft())

        if not batch:
            return

        async with self._pool.acquire() as con:
            async with con.transaction():
                for conv_id, msg in batch:
                    await con.execute(
                        """
                        INSERT INTO chat_conversations (conversation_id, created_at, updated_at, message_count)
                        VALUES ($1, now(), now(), 1)
                        ON CONFLICT (conversation_id) DO UPDATE
                        SET updated_at = now(), message_count = chat_conversations.message_count + 1
                        """,
                        conv_id,
                    )
                    await con.execute(
                        """
                        INSERT INTO chat_messages (id, conversation_id, role, content, source, created_at)
                        VALUES ($1, $2, $3, $4, $5, now())
                        ON CONFLICT (id) DO NOTHING
                        """,
                        msg.id,
                        conv_id,
                        msg.role,
                        msg.content,
                        msg.source,
                    )

        # Clear buffer file if memory queue is emptied
        if not self._pending and self.buffer_path.exists():
            try:
                self.buffer_path.unlink()
            except Exception:
                pass
