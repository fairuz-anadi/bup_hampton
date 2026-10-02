---
document_id: DOC-PROJ-004
filename: database_schema.md
category: project_documents
document_type: schema_specification
year: 2026
section: database_models
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/db/repo.py
  - backend/app/chat/repo.py
---

# PostgreSQL Relational Schema & Persistence Architecture

## 1. Storage Overview
FuelGuard utilizes PostgreSQL 16 (`fuelguard` database) managed via `asyncpg`. Reads are served from high-performance in-memory ring buffers so the frontend never experiences database read latency.

Writes persist asynchronously. If the database connection drops, writes are held in memory and written to a write-ahead JSONL log buffer (`/tmp/fuelguard-buffer.jsonl`), replaying automatically when the database reconnects.

## 2. Core Tables and DDL

### Table: `decisions`
Stores end-to-end decision records across all evaluation stages.
```sql
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
```

### Table: `policy_runs`
Records policy gauntlet execution runs and benchmarking results.
```sql
CREATE TABLE IF NOT EXISTS policy_runs (
    id          BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    policy      TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    run         JSONB NOT NULL
);
```

### Table: `chat_messages`
Stores operator conversations with the AI Chatbot Assistant.
```sql
CREATE TABLE IF NOT EXISTS chat_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id      TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    source          TEXT,
    suggested_prompts JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_conv_idx ON chat_messages (conversation_id, created_at ASC);
```

### Table: `rag_documents` and `rag_chunks`
Stores knowledge base documents, semantic chunks, and vector embeddings.
```sql
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
```
