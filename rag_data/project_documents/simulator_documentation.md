---
document_id: DOC-PROJ-003
filename: simulator_documentation.md
category: project_documents
document_type: simulator_specification
year: 2026
section: simulation_environment
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - docs/hour-one.md
  - fixtures/simulator_tick0.json
  - backend/app/sim/client.py
---

# BUP Fuel Supply Simulator Specification & Verified Quirks

## 1. Environment & Entity Topology
The official simulator runs as `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` exposing REST endpoints on port 8000 and Server-Sent Events (SSE) on `/v1/events/stream`.

### Entities:
- **Regions:**
  - `region-dhaka`: Demand factor 1.00.
  - `region-chattogram`: Demand factor 1.08.
- **Depots:**
  - `depot-gazipur` (Dhaka): Dispatch capacity 12,000 L/tick. Capacity: Diesel 90k L, Petrol 70k L, Octane 45k L. Starting inventory: Diesel 60k L, Petrol 45k L, Octane 26k L.
  - `depot-patiya` (Chattogram): Dispatch capacity 11,000 L/tick. Capacity: Diesel 85k L, Petrol 65k L, Octane 40k L. Starting inventory: Diesel 55k L, Petrol 42k L, Octane 24k L.
- **Retail Stations:**
  - `station-mirpur`: Urban High profile (high Petrol/Octane); capacity 15k/14k/9k L.
  - `station-tongi`: Industrial profile (heavy Diesel demand); capacity 18k/9k/6k L.
  - `station-karnaphuli`: Highway profile (freight Diesel/Petrol); capacity 14k/15k/9k L.
  - `station-coxsbazar`: Regional profile; capacity 12k/12k/7k L.
- **Routes:**
  - Direct routes (2 ticks transit): `route-gazipur-mirpur` (max 7,000 L), `route-gazipur-tongi` (max 6,500 L), `route-patiya-karnaphuli` (max 7,000 L).
  - Regional route (3 ticks transit): `route-patiya-coxsbazar` (max 6,000 L).
  - Cross-division backup routes (4 ticks transit): `route-gazipur-karnaphuli` (max 5,000 L), `route-patiya-mirpur` (max 5,000 L).

## 2. Verified Simulator Mechanics (from `docs/hour-one.md`)
1. **In-Transit Overflow Loss:** The simulator's `POST /v1/allocations` check validates against current tank stock only and **ignores fuel in transit**. If arriving fuel exceeds physical tank capacity, the tank clips at max capacity and all excess fuel is permanently destroyed. *FuelGuard Mitigation: The allocation writer strictly checks available headroom minus in-transit totals.*
2. **Non-Refundable Disruption Losses:** When an allocation departs on a disrupted route, status turns `FAILED` (`ROUTE_UNAVAILABLE`). The deducted depot stock is **never refunded**. *FuelGuard Mitigation: Never plan or dispatch over a route that is disrupted or scheduled to be disrupted within 1 tick.*
3. **CONSTRAINED Depot Status is Informational:** A status of `CONSTRAINED` on a depot does not alter physical dispatch capacity in the simulator code. It acts as an operational signal.
4. **Idempotency Response:** Resubmitting an allocation with the same `idempotency_key` returns HTTP 201 with the original allocation record.
5. **Connection Pool Leak Hazard:** If client connections drop prematurely, the simulator leaks connections from its 5+10 connection pool, causing all endpoints including `/v1/health` to freeze permanently. *FuelGuard Mitigation: 30s read timeouts, 2s connect timeouts, and concurrency strictly capped at 4 requests.*
