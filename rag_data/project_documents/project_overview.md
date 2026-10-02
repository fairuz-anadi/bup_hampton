---
document_id: DOC-PROJ-001
filename: project_overview.md
category: project_documents
document_type: system_overview
year: 2026
section: executive_summary
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - README.md
  - docs/architecture.md
  - docs/assumptions.md
  - backend/app/main.py
---

# FuelGuard: Resilient Decision-Support System for Fuel Supply Operations

## 1. Project Mission & Objectives
FuelGuard is an automated, resilient decision-support and control system engineered specifically around the official BUP Fuel Supply Simulator (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`) for the BUP CSE Fest 2026 hackathon.

The overarching mission of FuelGuard is to maintain continuous petroleum fuel distribution across Bangladesh regional networks under both normal operational cadence and severe crisis disruptions (demand spikes, route bridge severances, depot constraints, refinery shipment shortfalls, and station outages).

### Core Goals:
1. **Prevent Retail Fuel Stockouts:** Maximize the network-wide service level across all fuel types (Diesel, Petrol, Octane).
2. **Deterministic Constraint Enforcement:** Ensure every recommended dispatch respects physical depot dispatch capacities, route maximum shipment sizes, tank headroom limits, and depot minimum reserves.
3. **Decision Twin Projections:** Quantify the projected unmet demand avoided (liters) of proposed allocation plans against the "No-Action" counterfactual before execution.
4. **Autonomous Operation with Safety Gating:** Operate in Autonomous or Supervised modes with a multi-factor confidence scoring mechanism that immediately steps down to Manual review when risk is elevated or data becomes stale.
5. **Human-in-the-Loop Operator Experience:** Provide transparent, auditable approval workflows and conversational AI Copilot explanations backed by verifiable facts and retrieved policies.

## 2. Simulated Environment Scope
> [!IMPORTANT]
> FuelGuard operates strictly in a **simulated environment**. It connects directly to the BUP Fuel Supply Simulator REST and SSE interfaces. No physical petroleum infrastructure, pipeline scada, or financial banking rails are contacted.

## 3. High-Level System Workflow
1. **Observe:** The state store continuously polls the simulator clock (`GET /v1/instance`), network topology, inventory levels, in-transit shipments, and event streams.
2. **Detect & Forecast:** The intelligence layer detects demand anomalies and route outages while the forecaster predicts future consumption across a 24-tick horizon.
3. **Optimize & Learn:** Solvers (Linear Programming LP-v2 and Greedy-v1) and Reinforcement Learning agents formulate candidate allocation plans.
4. **Simulate (Decision Twin):** A three-future simulation assesses No-Op, Baseline, and Candidate policies to estimate unmet demand prevention.
5. **Confidence Gate:** Live telemetry, forecast fit, Twin accuracy, data freshness, and component health produce a confidence score (0.0 to 1.0) governing the autonomy mode.
6. **Execute or Review:** Safe routine plans in Autonomous mode auto-execute via `autopilot`; large, anomalous, or containment decisions await operator review (`POST /api/decisions/{id}/approve`).
7. **Submit & Audit:** The Allocation Writer decomposes legs, performs idempotent pre-checks, and writes dispatches to `POST /v1/allocations`.
