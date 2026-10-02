---
document_id: DOC-RULE-004
filename: transportation_rules.md
category: rules_policies
document_type: transportation_policy
year: 2026
section: transport_logistics
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - fixtures/simulator_tick0.json
  - docs/hour-one.md
---

# Transportation Rules, Corridors & Transit Latency

## 1. Road Transit Matrix
Simulation time advances in 15-minute ticks. Travel durations depend on corridor distance:

| Route ID | Origin Depot | Destination Station | Transit Ticks | Physical Duration | Max Shipment |
|---|---|---|---|---|---|
| `route-gazipur-mirpur` | Gazipur | Mirpur | 2 ticks | 30 minutes | 7,000 L |
| `route-gazipur-tongi` | Gazipur | Tongi | 2 ticks | 30 minutes | 6,500 L |
| `route-patiya-karnaphuli` | Patiya | Karnaphuli | 2 ticks | 30 minutes | 7,000 L |
| `route-patiya-coxsbazar` | Patiya | Cox's Bazar | 3 ticks | 45 minutes | 6,000 L |
| `route-gazipur-karnaphuli` | Gazipur | Karnaphuli | 4 ticks | 60 minutes | 5,000 L |
| `route-patiya-mirpur` | Patiya | Mirpur | 4 ticks | 60 minutes | 5,000 L |

## 2. Cross-Division Emergency Rerouting Policy
Routes `route-gazipur-karnaphuli` and `route-patiya-mirpur` are high-latency emergency corridors (4 ticks).
- They should not be used under normal conditions due to higher transport transit costs.
- They become active when primary direct routes suffer disruption or when local depots face inventory depletion.

## 3. Allocation Cancellation Window
Once posted, an allocation has status `PENDING`. It remains pending until the next `/admin/step` call advances the simulation, transitioning it to `IN_TRANSIT`.
- Cancellation and depot refund is **only possible while status is PENDING**.
- Once in transit, an allocation cannot be recalled or refunded.
