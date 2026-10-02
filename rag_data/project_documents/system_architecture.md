---
document_id: DOC-PROJ-002
filename: system_architecture.md
category: project_documents
document_type: architecture_specification
year: 2026
section: system_architecture
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - docs/architecture.md
  - docs/backend-integration.md
  - backend/app/main.py
  - backend/app/config.py
---

# FuelGuard System Architecture & Lane Decomposition

## 1. Architectural Topology
FuelGuard is organized into three collaborative engineering lanes operating against a centralized state and persistence backbone:

1. **Backend & Platform Lane (`backend/app/`):** FastAPI application running on port 8080, handling simulator integration, state synchronization, circuit breakers, allocation writes, PostgreSQL persistence, and Prometheus observability.
2. **Intelligence & Forecaster Lane (`backend/app/intel/`, `forecaster/`, `backend/app/rl/`):** Demand forecasting (`fc-v1`), anomaly detection, risk assessment, LP optimization, Greedy dispatch, Decision Twin simulation, and RL policy inference.
3. **Frontend, Copilot & Operator Lane (`frontend/`, `backend/app/explain/`, `backend/app/chat/`):** React/TypeScript Mission Control dashboard (nginx port 3000), LangGraph/template explanation copilot, and conversational AI assistant.

## 2. Component Directory Breakdown
- `backend/app/sim/`: Simulator HTTP client (`client.py`), circuit breaker (`breaker.py`), and idempotent allocation writer (`allocations.py`).
- `backend/app/state/`: Live state cache (`store.py`) producing `NetworkSnapshot` and event-driven synchronization (`sync.py`).
- `backend/app/intel/`: Detection engine (`detection.py`), time-to-stockout risk engine (`risk.py`), LP optimizer (`lp.py`), greedy solver (`greedy.py`), Decision Twin (`twin.py`), and multi-agent coordinator (`multiagent.py`).
- `backend/app/decisions/`: Per-tick decision cycle engine (`engine.py`), confidence & autonomy gate (`gate.py`), and decision audit lifecycle service (`service.py`).
- `backend/app/rag/`: Retrieval-Augmented Generation ingestion, vector embedding, and hybrid retrieval pipeline.
- `backend/app/rl/`: Reinforcement learning gymnasium environment, state/action representations, reward function, and policy inference.
- `backend/app/explain/`: Explainer service and LangGraph copilot with strict faithfulness verification.
- `backend/app/db/`: Asynchronous PostgreSQL repository (`repo.py`) with memory buffering and JSONL journal failover.
- `backend/app/obs/`: Structured logging (`logging.py`) and Prometheus metrics registry (`metrics.py`).

## 3. Resilience and Failover Architecture
- **Simulator Client Breaker:** Tripped after 5 consecutive failures within 10 seconds. Enters HALF_OPEN cooldown after 15 seconds.
- **Database Disconnection Buffer:** If PostgreSQL is unreachable, writes are retained in an in-memory queue (up to 2,000 records) and appended to a persistent JSONL journal file (`/tmp/fuelguard-buffer.jsonl`). Background task flushes upon reconnection.
- **Forecaster Fallback:** If the external HTTP forecaster (:8090) times out or fails, `IntelligenceService` seamlessly switches to an internal baseline profile predictor.
- **Decision Engine Fallback:** If optimization or RL solvers fail, the engine falls back to greedy heuristic rationing or no-op, preserving system stability.
