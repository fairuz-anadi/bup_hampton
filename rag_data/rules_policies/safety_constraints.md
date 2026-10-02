---
document_id: DOC-RULE-003
filename: safety_constraints.md
category: rules_policies
document_type: safety_specification
year: 2026
section: safety_guardrails
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/decisions/gate.py
  - backend/app/sim/breaker.py
  - backend/app/sim/allocations.py
---

# Safety Constraints, Circuit Breakers & Rollback Protocols

## 1. Hard Guardrails (Zero-Tolerance)
Enforced in all operating modes (Manual, Supervised, Autonomous):
1. **Disrupted Corridor Lock:** No fuel allocation may be submitted over a route with status `DISRUPTED` or scheduled for disruption within 1 tick.
2. **Station Outage Lock:** No fuel allocation may be targeted to a retail station with status `OUTAGE`.
3. **Reserve Violation Lock:** No allocation that draws depot inventory below the 10% reserve threshold is permitted.
4. **Stale Data Execution Lock:** If operational snapshot data is older than threshold or circuit breaker is open, all automated dispatches are locked. Recommendations are restricted to "recommend-only" mode.

## 2. Simulator Circuit Breaker Specification
To prevent cascade failures and simulator connection pool exhaustion:
- **Failure Threshold:** 5 consecutive failures.
- **Monitoring Window:** 10.0 seconds.
- **Cooling Cooldown:** 15.0 seconds before attempting HALF_OPEN trial.
- **Timeout Protection:** Client read timeout fixed at 30 seconds to mirror simulator database pool timeout; client requests are never cancelled prematurely.

## 3. Policy Rollback Mechanism
FuelGuard supports immediate rollback of active allocation policies via `POST /api/policy/rollback`. If a newly deployed candidate policy (e.g. RL or LP-v2) generates suboptimal decisions or triggers guardrail blocks, the system reverts to the last accepted baseline (`greedy-v1`).
