# Load testing

We load-test our own backend, not the organizer's simulator. The brief asks us to understand the behaviour and limits
of our system, so each workload answers one question.

**Setup.** Full `docker compose` stack on one laptop (Windows 11, Docker Desktop / WSL2). k6 `grafana/k6:0.54.0` runs
in a container on the compose network. CPU and memory come from `docker stats`, sampled every 2 s during the run.
The backend is a single uvicorn process. Raw results are in [`loadtest/results/`](../loadtest/results/).

Run one yourself (resets the simulator for `e2e-submit`):

```bash
python scripts/run_loadtest.py dashboard-read -e VUS=200
```

## Results

| Workload | Peak VUs | Requests | Throughput | avg | p50 | p95 | p99 | Errors | Backend CPU peak | Backend mem peak |
|---|---|---|---|---|---|---|---|---|---|---|
| dashboard-read | 200 | 29,384 | 224.6 req/s | 9.7 ms | 4.6 ms | 37.1 ms | 59.9 ms | 0.00% | 66% | 66 MiB |
| dashboard-read | 600 | 95,586 | 593.8 req/s | 48.5 ms | 24.2 ms | 176.5 ms | 287.1 ms | 0.00% | 104% | 79 MiB |
| degraded-read (simulator error_rate 25%) | 50 | 7,913 | 65.4 req/s | 12.9 ms | 7.5 ms | 38.7 ms | 61.7 ms | **0.00%** | 44% | 70 MiB |
| e2e-submit (before fix) | 10 | 2,189 | 23.9 req/s | 346.5 ms | 6.5 ms | 1,472 ms | 1,576 ms | 0.00% | 69% | 67 MiB |
| e2e-submit (after fix) | 10 | 7,703 | 85.2 req/s | 97.4 ms | 3.2 ms | 379.3 ms | 524.2 ms | 0.00% | 91% | 81 MiB |

`e2e-submit` also tracks **approve → submitted**, the time from the operator's click to a PENDING allocation in the
official simulator: avg 1.03 s and p95 1.55 s before the fix, **avg 286 ms and p95 483 ms after**.

## What we learned

**1. The snapshot cache protects the simulator.** At 600 simulated operators the simulator's CPU stayed at 12–15%:
dashboard reads are served from our snapshot, and the simulator only sees our poller (about one refresh per second).

**2. Our read limit is one CPU core.** Between 200 and 600 operators, p95 goes from 37 ms to 177 ms, and backend CPU
reaches 104%, i.e. one core, because the backend is a single process. There were still no errors. More uvicorn
workers would raise this, but each worker would run its own poller against the simulator. For an operations centre
with a handful of operators, 200+ concurrent viewers is plenty, so we kept one process.

**3. Retries hide a flaky simulator from operators.** With the simulator failing 25% of calls (`error_rate` fault),
our API returned 0 errors out of 7,913 requests. Backend metrics show 256 injected 503s absorbed by 241 retries,
and the circuit breaker never opened. That is the intended behaviour: retries cover intermittent errors and the
breaker is kept for real outages.

**4. The first bottleneck we found was the simulator itself, and we caused it.** In the first `e2e-submit` run
the simulator container sat at ~100% CPU and approvals took ~1 s. Two causes:
- `GET /v1/allocations` returns the full, ever-growing list: 157 ms at ~900 rows. The writer re-read it before
  every submission, and it grows during a run.
- Every allocation fires SSE events, and each event triggered another full refresh. The single-threaded
  simulator spent its time serving our refreshes and queued everyone's writes behind them.

Fixes: the writer now re-reads only the five cheap resources and merges in the allocations it just posted (they
expire after 15 s or once the simulator lists them). The poller keeps at least 0.5 s between refreshes however many
events arrive. Result: 3.6× the throughput, and approve → submitted p95 fell from 1.55 s to 483 ms.

**5. No duplicate shipments under load.** Each e2e iteration also retries its approval; all retries were refused
with 409, and the simulator held 0 duplicate idempotency keys. Once a depot had used its 12,000 L dispatch capacity
for the (paused) tick, 1,646 legs were blocked by our pre-check before posting. No fuel was lost to rejected or
failed shipments.

## Workloads

| File | Question it answers | Shape |
|---|---|---|
| `dashboard-read.js` | How many operators can watch live before latency degrades? Does the cache protect the simulator? | ramp to N VUs; each polls `/api/state` every second, plus health and history |
| `degraded-read.js` | Do cached reads keep our error rate low while the simulator is flaky? | 50 VUs for 2 min with `error_rate 0.25` injected |
| `e2e-submit.js` | How long from approval to a PENDING allocation? Any duplicates under retry? | ramp to 10 VUs; each registers + approves a decision, then retries the approval |
| decision dry-run | Where does the decision engine saturate: forecaster, LP or Twin? | **Pending**: needs the intelligence lane's recommendation endpoint |

Grafana's **FuelGuard · Load test** dashboard (http://localhost:3001) shows throughput, latency, CPU and memory live
during a run.
