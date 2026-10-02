---
document_id: DOC-EXT-001
filename: data_sources.md
category: external_data
document_type: data_catalog
year: 2026
section: telemetry_and_sources
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/sim/client.py
  - backend/app/state/sync.py
  - fixtures/
---

# Data Sources Catalog & Telemetry Ingestion Specifications

## 1. Verified System Ingestion Feeds
FuelGuard consumes data across four primary channels:

1. **Simulator REST API (`http://simulator-api:8000`):**
   - Poll cadence: 1.0 second.
   - Endpoints: `/v1/instance`, `/v1/depots`, `/v1/stations`, `/v1/routes`, `/v1/supply-arrivals`, `/v1/events`, `/v1/allocations`, `/v1/metrics`.
   - Authoritative ground truth for all simulation states.
2. **Simulator Server-Sent Events (SSE):**
   - Stream endpoint: `/v1/events/stream`.
   - Delivers real-time notifications for simulation step advancement, new event injection, and allocation arrivals.
3. **Forecaster Service (`http://forecaster:8090`):**
   - Endpoint: `POST /forecast`.
   - Provides 24-tick horizon mean, p10, and p90 demand predictions.
4. **Historical Demand Archive (`fixtures/demand_history.json`):**
   - 1,176+ tick records used for baseline calibration, residual calculation, and offline RAG benchmarking.
