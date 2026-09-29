# Demo script (14 steps, about 10 minutes)

A proposed run of the judged demo. Rehearse it twice before the freeze; adjust wording, not the order.
Everything happens in the operator UI (`/`) except where a step names another tool. Keep the
"Simulated environment" strip visible the whole time.

**Setup (before judges arrive):** `docker compose up -d`, open the UI, Grafana (`:3001`) in a second tab.
Reset the world from Simulation Lab → *Reset world*, then turn on *Pacer 1 tick/s* so humans can follow.
Unlock the Simulation Lab with the operator key. Policy: `lp-v2` (Simulation Lab → Allocation policy).

| # | Screen | Do | Say (one line) | Shows |
|---|---|---|---|---|
| 1 | Overview | Nothing; point at the four numbers and the map | "Service level, stations at risk, active events, system mode. When nothing is wrong, the screen says so." | §6 operator information |
| 2 | Overview → station | Click Tongi on the map | "Two depots, four stations, six routes. Tongi and Cox's Bazar have a single route." | Live state from the simulator |
| 3 | Simulation Lab | *Demand spike* → Run scenario | "Judges can break it. We inject the Dhaka demand spike from the organizer's own admin API." | Chaos Lab, §19.9 |
| 4 | Simulation Lab | Watch the chain fill in | "Injected, active, anomaly detected, shortage predicted, allocation generated, waiting for review. Each tick is checked against live state." | Detection, forecast, risk |
| 5 | Intelligence | Step through Detect → Predict → Decide | "Detect: demand against its normal band. Predict: when Tongi runs out and how likely. Decide: the shipment, the evidence, and every constraint checked." | Detection, prediction, decision |
| 6 | Intelligence | Open the confidence pill | "Confidence fell because demand is abnormal and a crisis is active, so the mode is Supervised: a human must approve." | Adaptive autonomy, human review |
| 7 | Intelligence → Decide | Ask the copilot "Why not send 5,000 L?" | "The copilot only restates facts; if it invents a number we throw its answer away and show the template." | Explainability, LangGraph |
| 8 | Intelligence → Simulate, Approve | *Simulate impact*, then *Modify* one leg and *Approve modified plan* | "The Decision Twin projects doing nothing, greedy and our plan across the whole network; these are projections. Then the operator decides: the modified plan is re-checked by guardrails and sent with idempotency keys." | Decision Twin, human review, safe writes |
| 9 | Simulation Lab | *Route disruption* → Run scenario | "Now a route closes. Guardrails block it; the plan reroutes over Patiya's 4-tick route." | Crisis playbook, rerouting |
| 10 | Simulation Lab | More scenarios → *Stale data* | "The simulator says its data may be stale: red banner, Manual mode, recommendations locked." | Resilience, safe mode |
| 11 | Simulation Lab → System Health | More scenarios → *API unavailable*; open System Health | "The API refuses requests. Circuit opens, cached state with its age, writes held. Health says degraded, not down." | Circuit breaker, degraded mode |
| 12 | Intelligence | After faults expire, confidence pill → *Re-arm Autonomous* | "Mode climbs back one level per 3 healthy ticks; only an operator can re-arm Autonomous." | Recovery |
| 13 | Simulation Lab | *Incident report* | "An incident report from the audit log and events: what happened, what we decided, what is open." | Auto incident report |
| 14 | Intelligence → decision history, Grafana | Replay a verified decision; show dashboards | "Every decision is an audit record we can replay, and the Twin checks itself: projected vs actual. Grafana shows latency, fallbacks, confidence and Twin error." | Audit, self-checking Twin, observability |

**If something breaks live:** say what the system is doing about it (that is the point of the demo), open
System Health, and continue from the next step. The backup is `?mock=1` on the UI, which shows the shared
fixtures clearly labelled "Mock data".

**Timing:** steps 1-2 one minute, 3-8 four minutes, 9-12 three minutes, 13-14 two minutes.
