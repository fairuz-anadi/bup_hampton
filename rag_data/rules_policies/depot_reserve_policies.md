---
document_id: DOC-RULE-002
filename: depot_reserve_policies.md
category: rules_policies
document_type: reserve_policy
year: 2026
section: depot_reserves
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/decisions/gate.py
  - backend/app/contracts.py
---

# Depot Strategic Reserve Policies & Dispatch Limits

## 1. Mandatory 10% Minimum Reserve Policy
To protect against refinery delivery delays and supply chain shocks, depots must never be drawn below 10% of their nameplate fuel capacity (`rails.depot_reserve_fraction = 0.10`).

### Depot Reserve Thresholds:
1. **Gazipur Depot (`depot-gazipur`):**
   - Diesel Capacity: 90,000 L $\rightarrow$ **Minimum Reserve: 9,000 L**
   - Petrol Capacity: 70,000 L $\rightarrow$ **Minimum Reserve: 7,000 L**
   - Octane Capacity: 45,000 L $\rightarrow$ **Minimum Reserve: 4,500 L**
2. **Patiya Depot (`depot-patiya`):**
   - Diesel Capacity: 85,000 L $\rightarrow$ **Minimum Reserve: 8,500 L**
   - Petrol Capacity: 65,000 L $\rightarrow$ **Minimum Reserve: 6,500 L**
   - Octane Capacity: 40,000 L $\rightarrow$ **Minimum Reserve: 4,000 L**

## 2. Dispatch Capacity Limits
Each depot has a hard throughput ceiling per 15-minute simulation tick:
- Gazipur Depot: Maximum 12,000 L per tick.
- Patiya Depot: Maximum 11,000 L per tick.

*Verification Rule:* Only allocations created in the current simulation tick count against this threshold. Allocations departing or in transit from prior ticks do not restrict new dispatches.

## 3. Semantics of `CONSTRAINED` Status
When a depot status changes to `CONSTRAINED`, the simulator indicates an upstream logistics bottleneck. **Verified finding:** The simulator does not decrease physical throughput during `CONSTRAINED` status. FuelGuard treats this status as a warning signal to prioritize essential routes.
