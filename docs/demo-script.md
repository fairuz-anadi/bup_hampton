# Demo script (14 steps, about 10 minutes)

A proposed run of the judged demo. Rehearse it twice before the freeze; adjust wording, not the order.
Everything happens in the operator UI (`/`) except where a step names another tool. Keep the
"Simulated environment" strip visible the whole time.

**Setup (before judges arrive):** `docker compose up -d`, open the UI, Grafana (`:3001`) in a second tab.
Reset the world from Chaos Lab → Simulation → *Reset world*, then turn on *Pacer: 1 tick/s* so humans can follow.
Unlock the Chaos Lab with the operator key. Policy: `lp-v2` (Chaos Lab → Allocation policy).

| # | Screen | Do | Say (one line) | Shows |
|---|---|---|---|---|
| 1 | Mission Control | Nothing; point at the seven cards | "One screen answers the seven operator questions: are we OK, what is wrong, what is likely, what to do, why, what if we do nothing, is our system healthy." | §6 operator information |
| 2 | Network | Hover a shipment dot, click Mirpur | "Two depots, four stations, six routes. Tongi and Cox's Bazar have a single route." | Live state from the simulator |
| 3 | Chaos Lab | *Demand spike* → Inject | "Judges can break it. We inject the Dhaka demand spike from the organizer's own admin API." | Chaos Lab, §19.9 |
| 4 | Mission Control | Wait 2-3 ticks | "Detected: demand above forecast. Risk ranking changes; Mirpur now runs out in hours." | Detection, forecast, risk |
| 5 | Recommendation | Read the plan and three futures | "Three futures from the Decision Twin: do nothing, greedy, LP. Network-wide, not just one station. These are projections." | Decision Twin |
| 6 | Recommendation | Point at the gate and confidence factors | "Confidence fell because demand is abnormal and a crisis is active, so the mode is Supervised: a human must approve." | Adaptive autonomy, human review |
| 7 | Recommendation | Ask the copilot "Why not send 5,000 L?" | "The copilot only restates facts; if it invents a number we throw its answer away and show the template." | Explainability, LangGraph |
| 8 | Recommendation | *Modify* one leg, *Approve modified plan* | "Operators stay in charge. The modified plan is re-checked by guardrails and sent with idempotency keys." | Human review, safe writes |
| 9 | Chaos Lab | *Route disruption* (Gazipur → Mirpur) → Inject | "Now a route closes. Guardrails block it; the plan reroutes over Patiya's 4-tick route." | Crisis playbook, rerouting |
| 10 | Chaos Lab | *Stale data* 60 s → Inject; watch Live reaction | "The simulator says its data may be stale: red banner, Manual mode, recommendations locked." | Resilience, safe mode |
| 11 | Chaos Lab | *API unavailable* 45 s → Inject; open System Health | "The API refuses requests. Circuit opens, cached state with its age, writes held. Health says degraded, not down." | Circuit breaker, degraded mode |
| 12 | Recommendation | After faults expire, *Re-arm Autonomous* | "Mode climbs back one level per 3 healthy ticks; only an operator can re-arm Autonomous." | Recovery |
| 13 | Crises | *Generate report* | "An incident report from the audit log and events: what happened, what we decided, what is open." | Auto incident report |
| 14 | History + Grafana | Replay a verified decision; show dashboards | "Every decision is an audit record we can replay, and the Twin checks itself: projected vs actual. Grafana shows latency, fallbacks, confidence and Twin error." | Audit, self-checking Twin, observability |

**If something breaks live:** say what the system is doing about it (that is the point of the demo), open
System Health, and continue from the next step. The backup is `?mock=1` on the UI, which shows the shared
fixtures clearly labelled "Mock data".

**Timing:** steps 1-2 one minute, 3-8 four minutes, 9-12 three minutes, 13-14 two minutes.
