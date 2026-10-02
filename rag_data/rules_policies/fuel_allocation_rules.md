---
document_id: DOC-RULE-001
filename: fuel_allocation_rules.md
category: rules_policies
document_type: operational_rules
year: 2026
section: allocation_rules
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/sim/allocations.py
  - backend/app/decisions/gate.py
  - docs/hour-one.md
---

# Fuel Allocation Rules & Physical Validation Logic

## 1. Verified Simulator Constraints vs FuelGuard Policies
It is critical to distinguish official simulator engine constraints from FuelGuard system guardrails:

| Rule Description | Enforced By | Error Code | Consequence if Ignored |
|---|---|---|---|
| In-Transit Headroom Limit | FuelGuard Writer & Gate | `PRECHECK_TANK_HEADROOM` | Simulator clips at capacity; excess fuel permanently lost |
| Route Disruption Prohibition | FuelGuard Writer & Gate | `PRECHECK_ROUTE_DISRUPTED` | Allocation fails; deducted depot fuel is NOT refunded |
| Depot Minimum Reserve | FuelGuard Gate & Writer | `PRECHECK_DEPOT_RESERVE` | Depletion of emergency strategic reserves |
| Station Outage Prohibition | FuelGuard Writer & Gate | `PRECHECK_STATION_OUTAGE` | Shipments arrive at closed station |
| Single-Tick Dispatch Limit | Simulator & FuelGuard | `PRECHECK_DISPATCH_CAPACITY` | Simulator rejects with HTTP 409 |
| Route Max Shipment Cap | FuelGuard Writer Split | Automated Split | Simulator rejects shipments exceeding route maximum |

## 2. In-Transit Ledger Accounting Rule
The allocation writer calculates true available capacity prior to dispatch:
$$\text{Available Headroom} = \text{Station Capacity} - (\text{Current Inventory} + \text{In-Transit Inflow})$$
Any allocation exceeding available headroom is rejected or truncated to prevent catastrophic fuel loss.

## 3. Shipment Splitting Rule
When an allocation leg exceeds `route.max_shipment`:
- Gazipur to Mirpur: Cap 7,000 L.
- Gazipur to Tongi: Cap 6,500 L.
- Patiya to Karnaphuli: Cap 7,000 L.
- Patiya to Cox's Bazar: Cap 6,000 L.
- Cross-division backup routes: Cap 5,000 L.
The allocation writer automatically splits larger requests into multiple independent shipments using deterministic sub-keys (`fg-{decision_id}-{leg_index}-part1`).

## 4. Idempotency Rule
Every dispatch must carry a deterministic `idempotency_key` formatted as `fg-{decision_id}-{leg_index}`. Replaying an accepted request returns HTTP 201 without creating duplicate shipments.
