# Building on the backend (for the intelligence and frontend lanes)

Everything below runs today with `docker compose up -d --build`. Shapes are defined in
[`backend/app/contracts.py`](../backend/app/contracts.py); example payloads are in [`fixtures/`](../fixtures/).
Interactive docs: http://localhost:8080/docs.

## Reading the world

| Endpoint | Use it for |
|---|---|
| `GET /api/state` | The whole `NetworkSnapshot`: depots, stations, routes, supply, events, metrics, `in_transit`, `in_transit_totals`, `dispatched_this_tick`, and `freshness`. Served from cache, never blocks on the simulator. |
| `GET /api/demand-history?limit=&station_id=` | Forecaster training / residuals. Newest first, up to 2,000 rows (12 rows per tick). |
| `GET /api/health` | System Health page. Component list + `version`, `active_policy`, `pacer_running`. |
| `GET /api/decisions`, `GET /api/decisions/{id}` | Decision history / audit view. |
| `GET /api/chaos/timeline` | Recent events and faults for the Crises screen. |

**Always check `freshness.stale`.** When it is true the snapshot is cached (circuit open, 503s or the
`X-Simulator-Stale` header). Show the age (`freshness.resources[*].age_seconds`) and don't auto-execute anything built
on it. Set `built_on_stale_data: true` on the recommendation, and the backend will refuse to approve it.

## Facts the optimizer must respect (verified, see [hour-one.md](hour-one.md))

- Count `in_transit_totals` against tank headroom. The simulator doesn't, and overflow is **lost**.
- Never plan over a route with a `route_disruption` that is active or starts within one tick: a FAILED shipment
  is **not refunded**.
- Dispatch capacity only counts allocations created this tick: `dispatched_this_tick[depot]`.
- `CONSTRAINED` depots still dispatch their full capacity.
- `route.max_shipment` caps one leg; the writer splits larger legs, but plan with it anyway.

The writer enforces all of this again before posting (`PRECHECK_*` codes), so a bad plan is blocked, not executed.

## Submitting a decision (intelligence lane → human → simulator)

1. Build a `Recommendation` (candidates, `selected_candidate_id`, Twin `futures`, risks, signals, confidence).
2. `POST /api/decisions` with `{"recommendation": ..., "gate": {...}, "mode": "SUPERVISED"}` and `X-Operator-Key`.
   In-process code can call `services.decisions.create(rec, gate, mode)` instead.
3. The operator approves in the UI: `POST /api/decisions/{id}/approve` `{"by": "...", "reason": "..."}`, or passes
   modified `legs` to send something else. `POST /api/decisions/{id}/reject` needs a `reason`.
4. The backend posts the legs with idempotency keys `fg-{decision_id}-{leg}`. The record moves to `submitted`, with a
   result per leg: `accepted`, `skipped` (pre-check), `rejected` (simulator 409) or `held` (simulator down).
5. When the Twin horizon has passed, the backend reads actual unmet demand from the simulator. It moves the record to
   `verified` with `twin_check = {predicted_l, actual_l, error_l}`, and exports `fuelguard_twin_error_liters`.

## How the intelligence lane is wired in

`backend/app/decisions/engine.py` calls `IntelligenceService.evaluate_and_recommend(snapshot, demand_history)`
without touching `backend/app/intel/`:
- It imports either `app.intel` or `backend.app.intel`, so both import styles work. An import error shows as
  "Decision engine: down" in health instead of crashing the backend.
- It runs in a worker thread, one run at a time, with a 5 s budget (`INTEL_TIMEOUT_SECONDS`).
- It converts the result through `model_dump` → `model_validate`, so the two copies of `contracts.py` don't clash.
- It fills in what the review flow needs: `candidates` (noop / greedy-v1 / lp-v2 from the Twin futures), `label`,
  `built_on_stale_data`, `fallback_used`, `versions`.
- Demand history is **not** passed yet (`INTEL_USE_DEMAND_HISTORY=false`), because the intel code reads `demand` /
  `fuel` while the API returns `demand_liters` / `fuel_type`. Flip it once that's fixed.
- Python packages the intel code needs go in `backend/requirements-intel.txt`. The Docker image and CI install it.
- The backend image is built from the repo root and contains `forecaster/`. `/app/backend` is a symlink to `/app`,
  so `backend.app.*` imports resolve too.

The decision loop runs the engine on every new tick. When a station is at risk and nothing is already waiting
for review, it registers the recommendation as a `gated` decision for the operator. It never approves anything
itself. Pending decisions expire after 8 ticks.

## Hooks for your services

- **Forecaster:** set `FORECASTER_URL=http://forecaster:8090` and the backend adds it to `/api/health` (`GET /health`).
  Prometheus already scrapes `forecaster:8090/metrics`. The Chaos Lab calls `POST /chaos/disable {"seconds": n}` and
  `POST /chaos/exit` on it.
- **Other health probes:** append an async function returning `ComponentHealth` to `services.health_probes`.
- **Policy switch:** read `services.policy.active` (`greedy-v1` by default). `PUT /api/policy` switches it and
  `POST /api/policy/rollback` returns to the last accepted policy.
- **Fallback metric:** `from app.obs.metrics import FALLBACKS; FALLBACKS.labels("forecaster").inc()` whenever a
  fallback activates, plus `log_event("fallback.activated", component="forecaster")`.

## RAG Knowledge Subsystem Integration

The RAG pipeline provides grounded domain rules and policies to the intelligence service, decision explanations, copilot, and operator UI.

### API Endpoints
| Endpoint | Method | Payload / Description |
|---|---|---|
| `/api/rag/search` | POST | `{"query": str, "category": str?, "top_k": int}` · Returns ranked semantic + BM25 chunks. |
| `/api/rag/ask` | POST | `{"query": str, "category": str?, "top_k": int}` · Returns grounded answer with cited document sources. |
| `/api/rag/ingest` | POST | `{"data_dir": str?, "force": bool}` · Scans documents, hashes SHA-256, chunks, embeds, persists. |
| `/api/rag/stats` | GET | Knowledge store statistics, document counts per category, and health status. |

### Adding Knowledge Documents
Add markdown, text, JSON, PDF, or DOCX documents to `rag_data/` under appropriate subdirectories:
- `rag_data/rules_policies/`: Regulatory constraints, reserve mandates, ullage rules.
- `rag_data/project_documents/`: Architecture specifications, schemas, API contracts.
- `rag_data/historical_reports/`: Crisis benchmarks, demand profiles, and simulation audits.
- `rag_data/external_data/`: Bangladesh geographic context and petroleum distribution standards.

Optional YAML frontmatter:
```yaml
---
document_id: rules_policies/custom_rule.md
title: Special Tank Safety Standard
category: rules_policies
manual_edit: true
---
```

## Reinforcement Learning (RL) Integration

The RL agent learns dispatch decisions using Proximal Policy Optimization (PPO) over the gymnasium `FuelSupplyEnv`.

### API Endpoints
| Endpoint | Method | Description |
|---|---|---|
| `/api/rl/recommend` | POST | `{"snapshot": NetworkSnapshot?}` · Generates validated dispatch recommendation or fallback status. |
| `/api/rl/stats` | GET | Returns active model name, version, observation/action dimensions, and health status. |

### Guardrails & Safety Isolation
The RL agent **never** bypasses safety checks or directly moves fuel:
1. `validate_action()` validates depot 10% reserve floor, route availability, dispatch capacity, and station tank headroom.
2. If invalid, the action is rejected and does not reach the allocation writer.
3. If RL is unhealthy, stale, or fails, the system automatically falls back to `lp-v2` or `greedy-v1`.

### Training & Retraining
- Retrain agent: `python backend/app/rl/training/train.py --timesteps 50000 --save-path backend/app/rl/models/fuel_ppo_v1.pt`
- Evaluate policy: `python backend/app/rl/training/evaluate.py --episodes 20`
- Model checkpoints and metadata are stored in `backend/app/rl/models/` and `rl/models/`.

## Chaos Lab and demo controls (all need `X-Operator-Key`)

| Endpoint | Body |
|---|---|
| `POST /api/chaos/events` | `{"type": "demand_spike", "start_in_ticks": 1, "duration_ticks": 12, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}}` |
| `POST /api/chaos/faults` | `{"type": "stale_data", "duration_seconds": 60}`: `latency`, `unavailable`, `error_rate`, `stale_data`, `stream_disconnect` |
| `POST /api/chaos/faults/clear` | none |
| `POST /api/chaos/sim/{pause,run,toggle,step,reset}` | none |
| `POST /api/pacer` | `{"enabled": true, "interval_ms": 1000}`: steps the simulator at a human pace for the demo |

## Frontend notes

- Call the backend with **relative** URLs (`fetch("/api/state")`), never `http://localhost:8080`. In dev, proxy
  `/api` to `localhost:8080` in `vite.config.ts`; in the container, nginx proxies `/api/` to `backend:8080`. That way
  one Cloudflare Tunnel or Caddy hostname serves the UI and the API together.
- Types: `npx openapi-typescript http://localhost:8080/openapi.json -o src/api/types.ts`.
- Show the `SIMULATED` label on every screen.
