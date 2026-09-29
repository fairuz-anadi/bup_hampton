# Data usage

All data comes from the organizer's simulator. FuelGuard uses no external datasets and no personal data.

| Data | Source | Used for | Kept where |
|---|---|---|---|
| Regions, depots, stations, routes | `/v1/regions`, `/v1/depots`, `/v1/stations`, `/v1/routes` | Network state, capacities, route limits | In memory (`NetworkSnapshot`), refreshed every tick |
| Supply arrivals, events | `/v1/supply-arrivals`, `/v1/events` | Depot projections, crisis detection, route pre-checks | In memory |
| Allocations | `/v1/allocations` | In-transit ledger, dispatch capacity used this tick | In memory |
| Demand history | `/v1/demand-history` | Forecaster history (EWMA level correction, v2 training), Station demand chart | Forecaster memory; cached per tick in the backend |
| Metrics | `/v1/metrics` | Ground-truth service level and unmet demand | In memory; exported to Prometheus |
| Decision records | Produced by FuelGuard | Audit history, replay, incident reports, Twin self-check, confidence | Postgres (`decisions` table); buffered in memory + JSONL while the database is down |
| Copilot inputs | Structured facts built from the above | Explanations | Sent to the LLM provider only when `OPENAI_API_KEY` is set; traced in LangSmith when enabled |

## Training history

Forecasting uses demand the simulator itself produced: `/v1/demand-history` observations (station, fuel, tick,
demand, served, unmet). The forecaster (`forecaster/`) documents how history is generated for model v2 and when
v2 is allowed to replace v1 (only if it beats v1 on held-out ticks).

## Fixtures

`fixtures/` holds recorded simulator responses and example contracts so each lane can build without a running
simulator. `fixtures/recommendation.json` is illustrative example data; the UI labels it "Mock data" whenever it is
shown.

## What leaves the machine

Nothing, unless an LLM key is configured. With a key, the copilot sends the structured facts for one decision
(station names, litres, risk figures, signal messages) to the LLM provider. No credentials, operator keys or
personal data are ever included.
