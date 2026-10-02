# FuelGuard

A resilient decision-support system for fuel operations, built for the BUP CSE Fest 2026 hackathon finals.

> **Simulated environment.** FuelGuard runs only against the organizer-provided BUP Fuel Supply Simulator.
> It does not touch real fuel infrastructure, purchases or dispatches.

## Quick start

```bash
cp .env.example .env        # then set OPERATOR_KEY
docker compose up -d --build
```

| Service | URL |
|---|---|
| **Operator UI (Mission Control)** | http://localhost:3000 |
| Backend API docs | http://localhost:8080/docs |
| Backend health | http://localhost:8080/api/health |
| Grafana (Operations, Intelligence, Load test dashboards) | http://localhost:3001 (anonymous read-only; admin / `GRAFANA_ADMIN_PASSWORD`) |
| Prometheus (+ alert rules) | http://localhost:9090 |
| Simulator (official image) | http://localhost:8000/docs, dashboard at http://localhost:8000/admin |

The simulator starts **paused**. Step it with `POST /api/chaos/sim/step`, run it at demo pace with
`POST /api/pacer {"enabled": true}`, or run it at full speed with `POST /api/chaos/sim/run`.

## Architecture (backend lane)

```
official simulator ──REST + SSE──► simulator client ──► state store ──► /api/state ──► UI / intelligence
   (never modified)                timeouts, retries,    last-good snapshot,
                                   circuit breaker,      in-transit ledger,
                                   validation, stale     freshness per resource
        ▲
        │ POST /v1/allocations (idempotent)
 allocation writer ◄── decision service ◄── human approve / modify / reject ◄── recommendation (intelligence)
  pre-checks that          │
  prevent fuel loss        ├──► Postgres (decisions, policy runs; buffered in memory + JSONL when down)
                           └──► outcome check: actual vs Twin projection ─► fuelguard_twin_error_liters
Prometheus ◄── /metrics      Grafana ◄── Prometheus      JSON logs ─► stdout
```

## Repository layout

```
backend/            FastAPI backend
  app/contracts.py  shared Pydantic contracts: the source of truth for every lane
  app/sim/          simulator client, circuit breaker, allocation writer
  app/state/        last-known-good snapshot, in-transit ledger, REST poller + SSE listener
  app/decisions/    decision lifecycle, human review, outcome + Twin verification (service.py);
                    per-tick engine, confidence gate, autonomy modes, guardrails, autopilot (engine.py, gate.py)
  app/explain/      copilot: template explanations + LangGraph layer, faithfulness check, eval dataset
  app/db/           Postgres repo with outage buffering
  app/ops/          demo pacer, policy switch / rollback
  app/api/          /api/* routes, operator-key auth, Chaos Lab proxy
  app/obs/          Prometheus metrics, JSON logs
  tests/            unit tests (no Docker needed)
monitoring/         Prometheus config + alerts, Grafana provisioning + dashboards
loadtest/           k6 workloads and recorded results
deploy/             public deployment (Caddy HTTPS, VM setup) and Cloudflare Tunnel notes
fixtures/           recorded simulator data + example contracts for building against mocks
scripts/            smoke test, load-test runner, hour-one checks, fixture recorder
frontend/           React + TypeScript operator UI (Vite; nginx in Docker), mock-first
docs/               architecture, assumptions, data usage, demo script, hour-one findings, load-test report,
                    integration guide
```

Start with [docs/architecture.md](docs/architecture.md), then [docs/assumptions.md](docs/assumptions.md),
[docs/data.md](docs/data.md) and the [demo script](docs/demo-script.md).

The forecaster (`forecaster/`), intelligence (`backend/app/intel/`) and frontend (`frontend/`) lanes plug in as
described in [docs/backend-integration.md](docs/backend-integration.md).

## Backend API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/state` | Current `NetworkSnapshot` from cache, with freshness per resource |
| GET | `/api/state/in-transit` | PENDING + IN_TRANSIT legs |
| GET | `/api/demand-history` | Proxied, cached per tick, last good copy while the simulator is down |
| GET | `/api/health` | Component health, version, active policy; tells "simulator faulted" from "down" |
| POST | `/api/recommendations?policy=` | Dry run of the decision engine (detect → forecast → risk → LP/greedy → Twin) |
| GET | `/api/recommendations/latest` | Last engine result |
| GET/POST | `/api/policy-runs` | Policy Gauntlet results |
| GET/POST | `/api/decisions` | History / register a recommendation for review |
| POST | `/api/decisions/{id}/approve` · `/reject` | Human review; approve can carry modified legs |
| POST | `/api/allocations` · `/api/allocations/{id}/cancel` | Direct writes with pre-checks and idempotency keys |
| POST | `/api/chaos/events` · `/faults` · `/faults/clear` · `/sim/{action}` · `/forecaster/{action}` | Chaos Lab |
| GET | `/api/chaos/timeline` | Recent events and faults |
| GET/POST | `/api/pacer` | Step the simulator at a human pace for demos |
| GET/PUT | `/api/policy`, POST `/api/policy/rollback` | Active policy and rollback |
| GET | `/api/recommendations/current` | Recommendation for the current tick + confidence gate + autonomy mode |
| GET/POST | `/api/autonomy`, `/api/autonomy/rearm`, `/api/autonomy/mode` | Mode, confidence factors, transition log; re-arm / step down |
| POST | `/api/explain` · `/api/copilot/investigate` · `/api/copilot/summary` | Copilot (read-only): explain a decision, ask about a station, summarize |
| GET | `/api/copilot/incident-report?from_tick=&to_tick=` · `/api/copilot/info` | Incident report from events + decisions; copilot / tracing status |
| GET | `/api/scoreboard` | Counterfactual scoreboard (projected) + verified Twin error |
| POST | `/api/rag/ingest` | Ingest or update documents from `rag_data/` into vector store |
| POST | `/api/rag/search` | Semantic similarity search with category and metadata filtering |
| POST | `/api/rag/ask` | Grounded question answering citing verified policy and project sources |
| GET | `/api/rag/stats` | RAG knowledge base statistics and indexed document counts |
| POST | `/api/rl/recommend` | Generate a safe RL dispatch recommendation from current state |
| GET | `/api/rl/stats` | RL model metadata, observation/action specs, and health status |
| GET | `/metrics` | Prometheus |

Every POST/PUT needs `X-Operator-Key`. With no key configured, writes are disabled (fail closed).

## Resilience: what happens when something fails

| Failure | Behaviour | Tested by |
|---|---|---|
| Simulator 503 / timeouts | Jittered retries; the circuit opens after 5 failures in 10 s; cached snapshot marked stale; writes held | smoke test, `degraded-read` load test |
| Random simulator errors (25%) | Retries absorb them: 0 errors reached operators in 7,913 requests | `degraded-read` |
| `X-Simulator-Stale` | Snapshot marked stale; stale recommendations can't be approved | smoke test |
| Invalid simulator response | Rejected by validation, last good copy kept, alert logged | unit tests |
| SSE stream drop | Reconnect with backoff; REST polling continues | health "Event stream" |
| Postgres down | Decisions keep working; records buffer in memory + JSONL and flush on reconnect | smoke test `--docker` |
| Shipment that would destroy fuel | Blocked before posting (tank overflow incl. in-transit; disruption at departure) | smoke + unit tests |
| Retried approval / POST | Idempotency keys; 0 duplicates under load | `e2e-submit` |
| Client abandoning a simulator request | Never happens: 30 s read timeout, ≤4 requests in flight, no retry on read timeout. An abandoned request leaks a simulator DB connection, and 15 of them hang it for good | 5 s `latency` fault test |

See [docs/hour-one.md](docs/hour-one.md) for the simulator behaviour behind these rules,
[docs/load-test.md](docs/load-test.md) for measured limits, [docs/architecture.md](docs/architecture.md) for the
diagrams, [docs/crisis-rehearsal.md](docs/crisis-rehearsal.md) for every crisis run through the full stack, and
[gauntlet/results/latest.md](gauntlet/results/latest.md) for the Policy Gauntlet on the official simulator.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt   # Windows
cd backend && ../.venv/Scripts/python -m pytest -q && ../.venv/Scripts/ruff check app tests
```

Against a running stack (all of these reset the simulator):

```bash
python scripts/backend_smoke.py --key <OPERATOR_KEY> --docker
python scripts/run_loadtest.py dashboard-read -e VUS=200
python scripts/gauntlet_official.py --ticks 288        # policies vs each other on every crisis scenario
python scripts/rehearse_crises.py                      # detect / respond / safe / recover for every crisis
```

### Operator UI

```bash
cd frontend && npm install
npm run dev          # http://localhost:5173, proxies /api to BACKEND_URL (default http://localhost:8080)
npm run build        # type-check + production build into dist/
```

Four pages tell one story (what is happening → what is at risk → what the system recommends → why → what if I act):
**Overview** (KPIs, live network, what needs attention), **Intelligence** (Detect → Predict → Decide → Simulate →
Approve, each with its evidence, plus decision confidence and the autonomy state machine), **Simulation Lab** (run a
scenario and watch the response chain; faults, simulator controls, policy switch, incident report; operator key) and
**System Health**, plus an **Architecture** page. Station, network and decision-history pages are drill-downs. With no backend reachable the UI shows the shared fixtures labelled **Mock data**
(`?mock=1` forces it); if the backend drops after being live, it keeps the last snapshot with its age and pauses
approvals.

### Without Docker

```bash
python scripts/fake_simulator.py     # dev-only stand-in for the simulator on :8000 (NOT for any reported number)
python scripts/dev_backend.py        # backend on :8080 with the intelligence lane importable; creates .env if missing
cd frontend && npm run dev
```

The fake simulator supports `/admin/step|run|pause|toggle|reset`, all six `/admin/events` types and all five
`/admin/faults` types, so the Chaos Lab works end to end.

### Copilot evaluation

```bash
python scripts/copilot_eval.py               # faithfulness dataset: templates, or the LLM when OPENAI_API_KEY is set
python scripts/copilot_eval.py --langsmith   # also upload the dataset and record an experiment (LANGSMITH_API_KEY)
```

CI (`.github/workflows/ci.yml`): secret scan (gitleaks) → lint + unit tests → build images tagged with the git SHA →
deploy the stack with the official simulator → health checks → check the deployed version → end-to-end smoke test
(including a database outage) → short load test, with results uploaded as an artifact.

## Retrieval-Augmented Generation (RAG) & Knowledge Base

FuelGuard incorporates a specialized RAG engine that provides verifiable domain policies, simulator specifications, and historical reports to operators, decision engines, and conversational copilots.

### Knowledge Base Organization (`rag_data/`)
- `project_documents/`: System architecture, simulator documentation, database schemas, API specs, and RL design.
- `rules_policies/`: Fuel allocation rules, 10% minimum depot reserves, safety constraints, transportation latency rules, and decision approval governance.
- `historical_reports/`: Ground-truth simulation benchmarks (e.g. no-op 30.7%, 3-day calm 100%, 6-day 92.3%), allocation audit history, demand patterns from `fixtures/demand_history.json`, and RL evaluation benchmarks.
- `external_data/`: Verified data sources catalog, Bangladesh downstream petroleum context (BPC/ERL/OMCs), regional transport geography, and Department of Explosives handling safety standards.

### Self-Documenting Maintenance & Automated Re-ingestion
1. **Regenerate from Codebase:** Run `python scripts/generate_rag_docs.py` to re-extract live configurations and schemas into `rag_data/`. Files with `manual_edit: true` frontmatter are safeguarded from being overwritten.
2. **Trigger Vector Ingestion:**
   ```bash
   curl -X POST http://localhost:8080/api/rag/ingest -H "Content-Type: application/json" -d '{"force": false}'
   ```
3. **Query the Knowledge Base:**
   ```bash
   curl -X POST http://localhost:8080/api/rag/search -H "Content-Type: application/json" \
     -d '{"query": "What policy applies to minimum depot reserve?", "category": "rules_policies", "top_k": 3}'
   ```

### Storage & Resilience
- Primary storage in PostgreSQL (`rag_documents` and `rag_chunks` tables) with pgvector or array cosine distance.
- High-performance in-memory cache for zero-latency retrieval during operational decision cycles.
- Persistent JSONL disk journal (`/tmp/fuelguard-rag-store.jsonl`) guarantees knowledge survives restarts even if the database is temporarily offline.
- Deterministic in-process dense feature hashing fallback ensures semantic search and tests run completely offline without requiring external API keys.

## Reinforcement Learning (RL) Decision Subsystem

FuelGuard integrates deep Reinforcement Learning to learn fuel allocation and dispatch policies directly from the dynamic simulator environment.

### Gymnasium Environment (`FuelSupplyEnv`)
- **State Space (`dim=58`)**: Vectorizes `NetworkSnapshot` into continuous normalized features:
  - Depot inventories across all fuels (Diesel, Petrol, Octane) and dispatch capacities.
  - Station inventories, capacities, and short-term rolling demands.
  - LightGBM demand forecasts for the upcoming 4 ticks.
  - Route statuses (`AVAILABLE` vs `DISRUPTED`) and transit latencies.
  - Current in-transit fuel quantities headed to each station.
  - Active disruption flags and cyclical time features ($\sin, \cos$ of day).
- **Action Space**: Discrete combination space (25 actions) mapping to `(source_depot, destination_station, fuel_type, quantity)`.
- **Multi-Objective Reward Formulation**:
  $$R_t = w_{\text{served}} \cdot \text{ServedLiters} - w_{\text{unmet}} \cdot \text{UnmetLiters} - w_{\text{cost}} \cdot \text{Cost} - w_{\text{res}} \cdot \mathbb{I}_{\text{reserve\_violation}} - w_{\text{inv}} \cdot \mathbb{I}_{\text{invalid\_action}}$$
  Configurable via `backend/app/rl/training/config.yaml`:
  - `w_served: 2.0` (incentivizes high service level)
  - `w_unmet: 5.0` (penalizes stockouts heavily)
  - `w_cost: 0.05` (optimizes logistics efficiency)
  - `w_reserve: 10.0` (enforces Bangladesh 10% depot reserve mandate)
  - `w_invalid: 20.0` (strongly deters proposing illegal actions)

### Safety-First Architecture & Guardrails
- **The RL agent is strictly a candidate generator**: It **never** executes dispatches autonomously to the simulator.
- **Deterministic Action Validation**: Every proposed action is checked before reaching the decision engine or operator UI:
  1. Source depot inventory $\ge 10\%$ reserve floor after dispatch.
  2. Route status is `AVAILABLE` (no active disruptions).
  3. Dispatch quantity $\le$ depot's remaining per-tick capacity.
  4. Quantity $\le$ station tank headroom minus existing in-transit shipments.
  5. Quantity $> 0$.
- **Automated Fallback**: If an RL action violates any guardrail, is uncertain (confidence $< 0.80$), or the model is unavailable, FuelGuard automatically falls back to the Linear Programming optimizer (`lp-v2`) or Greedy baseline (`greedy-v1`).

### Training, Evaluation, and Notebooks
- **Standalone Training:** `python backend/app/rl/training/train.py --timesteps 50000 --save-path backend/app/rl/models/fuel_ppo_v1.pt`
- **Benchmark Evaluation:** `python backend/app/rl/training/evaluate.py --episodes 20`
- **Interactive Walkthrough Notebook:** Explore training dynamics, policy gradients, and state embeddings in `rl/notebooks/fuel_supply_rl_walkthrough.ipynb`.
- **Sample Scenarios:** Evaluated against crisis datasets in `rl/datasets/sample_scenarios.json`.

### Operator UI: RL & RAG Intelligence Center (`#/intelligence`)
The frontend provides a dedicated mission-control screen:
- Live RL recommendation display with source, destination, fuel type, litres, and route.
- Confidence score and real-time PyTorch inference latency.
- Deterministic guardrail check indicators (Pass/Fail).
- Applicable operational policies retrieved via RAG with citation links.
- Interactive operator review controls (**Approve Recommendation** / **Reject Recommendation with Reason**) that clearly distinguish AI recommendations from certified operational decisions.
- Interactive RAG Knowledge Assistant for natural language querying of project rules.

## Deployment

- **Demo / judging:** `docker compose up -d --build` on the demo laptop.
- **Live link:** see [deploy/README.md](deploy/README.md): Cloudflare Tunnel from the laptop, or a VM with Caddy HTTPS
  where only ports 80/443 are public and the simulator stays private.
