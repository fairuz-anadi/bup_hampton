"""Persistence store for FuelGuard RAG documents, chunks, and embeddings.

Supports asynchronous PostgreSQL persistence, in-memory caching for zero-latency
retrieval, and JSONL disk journal fallback.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.obs.logging import log_event
from app.rag.ingestion.chunker import DocumentChunk
from app.rag.ingestion.loaders import LoadedDocument

RAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_documents (
    document_id   TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    category      TEXT NOT NULL,
    document_type TEXT NOT NULL,
    version       TEXT NOT NULL,
    year          INTEGER,
    section       TEXT,
    checksum      TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id      TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES rag_documents(document_id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    page          INTEGER,
    section       TEXT,
    embedding     FLOAT8[],
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS rag_chunks_doc_idx ON rag_chunks (document_id);
"""


class RAGStore:
    def __init__(self, dsn: str | None = None, buffer_path: str | Path = "/tmp/fuelguard-rag-store.jsonl"):
        self.dsn = dsn
        self.buffer_path = Path(buffer_path)
        self._documents: dict[str, LoadedDocument] = {}
        self._chunks: dict[str, DocumentChunk] = {}
        self._pool = None
        self._has_pgvector: bool = False
        self._load_from_disk()

    async def connect(self) -> None:
        if not self.dsn:
            return
        import asyncpg
        try:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3, timeout=5)
                async with self._pool.acquire() as con:
                    # Check for pgvector extension
                    ext = await con.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                    self._has_pgvector = bool(ext)
                    await con.execute(RAG_SCHEMA)
                log_event("rag.db_connected", pgvector=self._has_pgvector)
        except Exception as exc:
            log_event("rag.db_connection_failed", error=str(exc)[:200])

    def save_documents_and_chunks(self, docs: list[LoadedDocument], chunks: list[DocumentChunk]) -> None:
        for d in docs:
            self._documents[d.document_id] = d
        for c in chunks:
            self._chunks[c.chunk_id] = c
        self._save_to_disk()

    async def persist_all(self) -> None:
        if not self.dsn or self._pool is None:
            return
        try:
            async with self._pool.acquire() as con:
                async with con.transaction():
                    for d in self._documents.values():
                        await con.execute(
                            """
                            INSERT INTO rag_documents (document_id, filename, category, document_type, version, year, section, checksum, metadata)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                            ON CONFLICT (document_id) DO UPDATE
                            SET filename = EXCLUDED.filename, category = EXCLUDED.category, checksum = EXCLUDED.checksum, metadata = EXCLUDED.metadata
                            """,
                            d.document_id, d.filename, d.category, d.document_type, d.version, d.year, d.section, d.checksum, json.dumps(d.metadata, default=str)
                        )
                    for c in self._chunks.values():
                        await con.execute(
                            """
                            INSERT INTO rag_chunks (chunk_id, document_id, chunk_index, content, page, section, embedding, metadata)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                            ON CONFLICT (chunk_id) DO UPDATE
                            SET content = EXCLUDED.content, page = EXCLUDED.page, section = EXCLUDED.section, embedding = EXCLUDED.embedding, metadata = EXCLUDED.metadata
                            """,
                            c.chunk_id, c.document_id, c.chunk_index, c.content, c.page, c.section, c.embedding, json.dumps(c.metadata, default=str)
                        )
            log_event("rag.persisted_to_db", documents=len(self._documents), chunks=len(self._chunks))
        except Exception as exc:
            log_event("rag.persist_failed", error=str(exc)[:200])

    def get_all_chunks(self) -> list[DocumentChunk]:
        return list(self._chunks.values())

    def get_document(self, document_id: str) -> LoadedDocument | None:
        return self._documents.get(document_id)

    def get_chunk(self, chunk_id: str) -> DocumentChunk | None:
        return self._chunks.get(chunk_id)

    def count(self) -> dict[str, int]:
        return {"documents": len(self._documents), "chunks": len(self._chunks)}

    def _save_to_disk(self) -> None:
        try:
            data = {
                "documents": [asdict(d) for d in self._documents.values()],
                "chunks": [asdict(c) for c in self._chunks.values()],
            }
            self.buffer_path.write_text(json.dumps(data, default=str), encoding="utf-8")
        except Exception as exc:
            log_event("rag.disk_save_failed", error=str(exc)[:150])

    def _load_from_disk(self) -> None:
        if not self.buffer_path.exists():
            return
        try:
            data = json.loads(self.buffer_path.read_text(encoding="utf-8"))
            for d in data.get("documents", []):
                doc = LoadedDocument(**d)
                self._documents[doc.document_id] = doc
            for c in data.get("chunks", []):
                chunk = DocumentChunk(**c)
                self._chunks[chunk.chunk_id] = chunk
            log_event("rag.loaded_from_disk", documents=len(self._documents), chunks=len(self._chunks))
        except Exception as exc:
            log_event("rag.disk_load_failed", error=str(exc)[:150])
