---
document_id: DOC-RULE-005
filename: decision_approval_policies.md
category: rules_policies
document_type: approval_policy
year: 2026
section: decision_governance
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/decisions/gate.py
  - backend/app/decisions/engine.py
---

# Decision Approval Governance, Confidence Scoring & Autonomy Modes

## 1. Autonomy State Machine
FuelGuard implements three operational autonomy tiers:
1. **MANUAL:** Operator explicitly reviews and approves every single dispatch plan.
2. **SUPERVISED:** Routine plans auto-execute if confidence is high; consequential or anomalous plans require operator sign-off.
3. **AUTONOMOUS:** Fully automated execution via `autopilot` for all gate-cleared decisions inside guardrails.

### Mode Transition Rules:
- **Instant Downgrade:** If confidence drops below 0.80, mode immediately steps down to SUPERVISED. If confidence drops below 0.60 or data is stale, mode immediately drops to MANUAL.
- **Gradual Promotion:** Climbing from MANUAL to SUPERVISED, or SUPERVISED to AUTONOMOUS, requires **3 consecutive healthy ticks** (`HEALTHY_TICKS_TO_CLIMB = 3`).
- **Operator Re-arm Required:** After any downgrade from AUTONOMOUS, the system will never re-enter AUTONOMOUS automatically; an operator must explicitly call `POST /api/autonomy/rearm`.

## 2. Six-Factor Confidence Scoring Formulation
Confidence score (0.0 to 1.0) is a weighted sum of observable operational health indicators:
- **Forecast Fit (Weight 0.25):** Evaluates anomaly signals; penalizes demand spikes or model mismatch.
- **Twin Accuracy (Weight 0.20):** Evaluates Decision Twin projection accuracy over the last 20 verified decisions ($1 - \text{mean relative error}$).
- **Data Freshness (Weight 0.20):** 1.0 for fresh data; 0.0 for stale data or open circuit breaker; 0.5 for HALF_OPEN.
- **Demand Normality (Weight 0.15):** Measures departure from baseline historical consumption bands.
- **Component Health (Weight 0.10):** Ratio of healthy backend, forecaster, and database probes.
- **No Active Crisis (Weight 0.10):** 1.0 during calm operations; reduced proportionally by active crisis events.

## 3. Mandatory Human Review Triggers
A recommendation is gated for mandatory human approval (`requires_human = True`) if:
- Current mode is `MANUAL`.
- Confidence score is below `0.80`.
- The recommendation is in `containment` mode (active crisis).
- Any single allocation leg exceeds **5,000 L** (`max_auto_leg_litres`).
- Total decision volume in SUPERVISED mode exceeds **6,000 L** (`routine_total_litres`).
- Number of legs in a single tick exceeds 8 (`max_legs_per_tick`).
- Any allocation leg was blocked by a guardrail.
