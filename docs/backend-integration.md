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

## Hooks for your services

- **Forecaster:** set `FORECASTER_URL=http://forecaster:8090` and the backend adds it to `/api/health` (`GET /health`).
  Prometheus already scrapes `forecaster:8090/metrics`. The Chaos Lab calls `POST /chaos/disable {"seconds": n}` and
  `POST /chaos/exit` on it.
- **Other health probes:** append an async function returning `ComponentHealth` to `services.health_probes`.
- **Policy switch:** read `services.policy.active` (`greedy-v1` by default). `PUT /api/policy` switches it and
  `POST /api/policy/rollback` returns to the last accepted policy.
- **Fallback metric:** `from app.obs.metrics import FALLBACKS; FALLBACKS.labels("forecaster").inc()` whenever a
  fallback activates, plus `log_event("fallback.activated", component="forecaster")`.

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
