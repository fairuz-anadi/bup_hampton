---
document_id: DOC-PROJ-005
filename: api_documentation.md
category: project_documents
document_type: api_reference
year: 2026
section: api_specifications
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/main.py
  - backend/app/api/routes.py
  - backend/app/api/control_routes.py
  - backend/app/decisions/routes.py
  - backend/app/chat/routes.py
---

# FuelGuard REST API Reference

All backend API routes are prefixed with `/api` unless otherwise noted.

## 1. Operational State & Telemetry
- `GET /api/state`: Returns the full cached `NetworkSnapshot` (depots, stations, routes, arrivals, active events, in-transit ledger, freshness status). Never blocks on simulator.
- `GET /api/demand-history`: Fetches past demand observation records (supports `limit` and `station_id` query parameters).
- `GET /api/health`: Comprehensive health report covering database, simulator connection, event stream, and decision engine status.
- `GET /metrics`: Prometheus metric scrape endpoint.

## 2. Decisions & Autonomy Lifecycle
- `GET /api/recommendations/current`: Computes and retrieves current tick recommendation, confidence factors, and gate result.
- `POST /api/decisions`: Create a new decision record (requires `X-Operator-Key`).
- `GET /api/decisions`: Retrieve past decision audit records (filter by `limit` or `stage`).
- `GET /api/decisions/{id}`: Fetch single decision record with full stage history.
- `POST /api/decisions/{id}/approve`: Approve candidate allocation plan (optional override `legs`).
- `POST /api/decisions/{id}/reject`: Reject candidate allocation with mandatory `reason`.
- `GET /api/autonomy`: View current autonomy mode (MANUAL, SUPERVISED, AUTONOMOUS) and confidence factors.
- `POST /api/autonomy/rearm`: Re-arm autonomous execution mode after confidence recovery.
- `POST /api/autonomy/mode`: Manually step down autonomy mode (e.g. to MANUAL).
- `GET /api/scoreboard`: Counterfactual scoreboard of unmet demand avoided.

## 3. RAG & Knowledge Retrieval
- `POST /api/rag/ingest`: Re-scan `rag_data/` and update document vector embeddings.
- `POST /api/rag/search`: Semantic similarity search across knowledge base (`query`, `category`, `top_k`).
- `POST /api/rag/ask`: Question answering grounded strictly on retrieved source citations.

## 4. Reinforcement Learning
- `POST /api/rl/recommend`: Formulates current state observation vector and generates a validated allocation recommendation using the trained RL policy.

## 5. Conversational Copilot & Chat
- `POST /api/explain`: Generate structured explanation of a decision using LangGraph or deterministic template.
- `POST /api/copilot/investigate`: Deep-dive station risk analysis.
- `POST /api/copilot/summary`: 2-4 sentence executive network summary.
- `POST /api/chat`: Multi-turn conversational chatbot message endpoint.
- `GET /api/chat/history/{conversation_id}`: Retrieve conversation history.
