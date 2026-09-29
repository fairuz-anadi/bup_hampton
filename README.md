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
| Backend API docs | http://localhost:8080/docs |
| Backend health | http://localhost:8080/api/health |
| Backend metrics | http://localhost:8080/metrics |
| Simulator (official image) | http://localhost:8000/docs, dashboard at http://localhost:8000/admin |

The simulator starts **paused**. Step it with `curl -X POST localhost:8000/admin/step` or run it with `/admin/run`.

## Repository layout

```
backend/            FastAPI backend (Anadi)
  app/contracts.py  shared Pydantic contracts: the source of truth for every lane
  app/sim/          simulator client, circuit breaker, allocation writer
  app/state/        last-known-good snapshot, in-transit ledger, REST poller + SSE listener
  app/api/          /api/* routes, operator-key auth
  app/obs/          Prometheus metrics, JSON logs
  tests/            unit tests (no Docker needed)
fixtures/           recorded simulator data + example contracts for building against mocks
scripts/            smoke test, hour-one checks, fixture recorder, exploration scripts
docs/               hour-one findings and design notes
```

The forecaster (`forecaster/`), intelligence (`backend/app/intel/`) and frontend (`frontend/`) lanes build against
`app/contracts.py` and the files in `fixtures/`.

## Backend API (so far)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/state` | Current `NetworkSnapshot`, served from cache. Includes freshness per resource. |
| GET | `/api/state/in-transit` | PENDING + IN_TRANSIT legs |
| GET | `/api/demand-history?limit=&station_id=` | Proxied, cached per tick, last good copy while the simulator is down |
| POST | `/api/allocations` | `X-Operator-Key` required. Splits, pre-checks, posts with idempotency keys `fg-{decision}-{leg}` |
| POST | `/api/allocations/{id}/cancel` | `X-Operator-Key` required. PENDING only |
| GET | `/api/health` | Aggregated component health; tells "simulator faulted" apart from "simulator down" |
| GET | `/metrics` | Prometheus |

## How the backend stays up when the simulator doesn't

- Every simulator call has a timeout and jittered retries. Domain errors (409) are not retried.
- A circuit breaker opens after 5 failures in 10 s and probes again after 15 s. While it is open the UI gets the
  cached snapshot marked stale, and allocation writes are held.
- `X-Simulator-Stale` marks the snapshot stale.
- Every response is validated. Invalid bodies are rejected and the last good copy is kept.
- SSE drops reconnect with backoff; REST polling continues regardless (REST is the source of truth).
- The writer blocks shipments that would lose fuel: tank overflow counting in-transit fuel, and routes with a
  disruption at departure time. See [docs/hour-one.md](docs/hour-one.md) for why.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements-dev.txt   # Windows
cd backend && ../.venv/Scripts/python -m pytest -q && ../.venv/Scripts/ruff check app tests
```

End-to-end smoke test against a running stack (resets the simulator):

```bash
python scripts/backend_smoke.py --key <your OPERATOR_KEY>
```

CI (`.github/workflows/ci.yml`) runs lint and unit tests, then builds the images, starts the stack with the official
simulator, waits for the health check and runs the smoke test.
