# Hour-one findings

Checked against the official simulator image `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`
(baseline scenario, seed 12345). Reproduce with `python scripts/hour_one.py`; it resets the simulator.

| Question | Answer | What we do about it |
|---|---|---|
| What happens when a shipment arrives at a full tank? | The POST capacity check ignores fuel already in transit, so both shipments are accepted. On arrival the tank is clipped at capacity and **the excess is lost**. In our test, 5,000 L was sent and only 1,240 L went in. | The allocation writer counts in-transit fuel against tank headroom (`PRECHECK_TANK_HEADROOM`). The optimizer must do the same. |
| Does a FAILED allocation refund depot stock? | **No.** Depot stock is deducted at POST. When the route is disrupted at departure the allocation goes `FAILED` (`ROUTE_UNAVAILABLE`) and the fuel is gone. | The writer refuses to post over a route with a disruption that is active or starts within one tick (`PRECHECK_ROUTE_DISRUPTED`). Scheduled events are visible in `/v1/events`. |
| Does `CONSTRAINED` lower dispatch capacity? | **No.** It is a label only. `dispatch_capacity_per_tick` stays at 12,000 and we could still dispatch the full 12,000 L. | Treat it as a signal. Don't model a lower capacity. |
| Idempotent replay: 201 or 200? | **201** for both a new allocation and a replay. | The writer treats 200 and 201 as success. |
| What counts against dispatch capacity? | Only allocations **created in the current tick**. Shipments from earlier ticks that are still on the road don't count. | `NetworkSnapshot.dispatched_this_tick` sums allocations with `created_tick == tick`. |

## Allocation lifecycle

`POST` gives `PENDING` and deducts depot stock at once. The next `/admin/step` departs it (`departure_tick` = created
tick, status `IN_TRANSIT`) and it is `ARRIVED` once `tick` passes `expected_arrival_tick = created + transit_ticks`.
So the window in which a `PENDING` allocation can be cancelled (and refunded) is only until the next step.

## Baseline behaviour (from `scripts/explore_sim.py` and `scripts/crisis_sim.py`)

| Policy | Horizon | Service level | Notes |
|---|---|---|---|
| Do nothing | 3 days | 30.7% | First stockout at tick 66 (Tongi diesel). Depots fill to capacity and waste incoming supply. |
| Refill below 40% | 3 days | 100% | No crisis: a simple rule is enough. |
| Refill below 40% | 6 days | 92.3% | The supply schedule ends at tick 212, so depots run dry from day 4. Unmet L per day: 0, 0, 0, 774, 15,126, 27,086. |
| Refill below 40% | 3 days + crises | 98.7% | Spike, disruption, shortfall and outage. The rule retried the disrupted route 8 times and never used the Patiya → Mirpur backup. |

So we compete on crisis response, rerouting and scarcity (containment), not on the calm baseline.
