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

See [docs/hour-one.md](docs/hour-one.md) for the simulator behaviour behind these rules, and
[docs/load-test.md](docs/load-test.md) for measured limits.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt   # Windows
cd backend && ../.venv/Scripts/python -m pytest -q && ../.venv/Scripts/ruff check app tests
```

Against a running stack (both reset the simulator):

```bash
python scripts/backend_smoke.py --key <OPERATOR_KEY> --docker
python scripts/run_loadtest.py dashboard-read -e VUS=200
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

## Deployment

- **Demo / judging:** `docker compose up -d --build` on the demo laptop.
- **Live link:** see [deploy/README.md](deploy/README.md): Cloudflare Tunnel from the laptop, or a VM with Caddy HTTPS
  where only ports 80/443 are public and the simulator stays private.
