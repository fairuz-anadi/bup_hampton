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

## ⚠️ The simulator hangs if clients abandon requests

Found while running the Gauntlet. Every request opens a database session in the simulator's fault middleware.
If the client disconnects before the response (a timeout, a cancelled fetch, a closed tab), that session is
never returned to the pool. The pool holds 5 + 10 connections. Once 15 have leaked, **every endpoint, including
`/v1/health`, hangs for good** (`QueuePool limit of size 5 overflow 10 reached`). It doesn't recover, even with no
traffic. Only `docker compose restart simulator-api` fixes it, and that resets the world.

Reproduce (on a throwaway simulator): 25 requests with a 5 ms client timeout, then `/v1/health` times out.

What we do about it:
- The backend never gives up on a request the simulator has started. Read timeout is 30 s, the same as the
  simulator's own pool timeout. Connect timeout is 2 s (a request that never connected costs nothing).
- At most 4 requests are in flight to the simulator; the rest queue inside the backend.
- A read timeout is not retried.
- Health is probed in the background, one probe at a time. `/api/health` never waits on the simulator.
- Verified: with a 5 s `latency` fault injected, the simulator stays healthy (with a 3 s timeout it hung).

**For the whole team:** the frontend, scripts and load tests must never call the simulator directly with short
timeouts. Go through the backend. If the simulator does hang during a demo, restart it with the command above.

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
