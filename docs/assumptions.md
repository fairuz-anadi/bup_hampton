# Assumptions and guardrails

## Scope

- FuelGuard runs **only** against the organizer-provided BUP Fuel Supply Simulator
  (`asifmahmoud414/bup-fuel-supply-simulator:1.0.0`). It never touches real fuel infrastructure, purchases or
  dispatches. Every screen carries a "Simulated environment" label.
- The simulator is the single source of truth. We never modify it and never treat its output as a forecast.
- `POST /v1/allocations` (and cancel) is the only way FuelGuard changes the simulated world.

## What we verified against the simulator

See [hour-one.md](hour-one.md): in-transit fuel is ignored by the simulator's capacity check and the excess is lost on
arrival; a FAILED allocation does not refund depot stock; `CONSTRAINED` is a label only; idempotent replays return 201;
only allocations created in the current tick count against dispatch capacity. The writer and the optimizer are built
around these facts.

## Projections are not outcomes

- The Decision Twin projects futures from forecast demand. Its numbers are **projections**, labelled as such in the UI
  and in explanations. We write "X L projected unmet demand avoided vs the no-action counterfactual", never
  "saved X L".
- Only the simulator's `/v1/metrics` (service level, unmet litres, failures) is reported as ground truth.

## Human review

- Consequential actions need a human: containment plans, large transfers, anything decided at low confidence or in
  Manual mode. Guardrails (no DISRUPTED route, no OUTAGE station, depot reserve) hold in every mode, including after
  an operator modifies a plan.
- Recommendations built on stale data are shown but cannot be executed.
- Autopilot only acts in Autonomous mode, which an operator must re-arm after every drop, and only on decisions the
  gate clears (inside every guardrail and limit). It approves through the same path as a human and is recorded as
  `by: autopilot`. Set `AUTOPILOT=false` to require a human for everything.
- Writes, approvals and mode changes require `X-Operator-Key`. With no key configured, writes are disabled.

## Placeholders we tune during rehearsal

| Setting | Value | Where |
|---|---|---|
| Confidence weights | fit 0.25, Twin 0.20, freshness 0.20, normality 0.15, health 0.10, crisis 0.10 | `decisions/gate.py` |
| Mode thresholds | Autonomous ≥ 0.80, Manual < 0.60, 3 healthy ticks per level | `decisions/gate.py` |
| Auto limits | 5,000 L per leg, 6,000 L per decision in Supervised, 8 legs per tick | `Guardrails` |
| Depot reserve | 10 % of capacity per fuel | `Guardrails` |
| Twin accuracy prior | 0.85 until the first self-check sample | `compute_factors` |

These are engineering choices, not measured optima. Load-test and model-accuracy numbers are reported only after we
measure them.

## The copilot

- Read-only: it has no tool that writes, and it never sits on the approval path.
- It receives structured facts only. An LLM answer that contains a number not present in the facts, or that says
  "saved", is discarded and the deterministic template is shown instead. Without an LLM key the template is used.

## Development-only tools

`scripts/fake_simulator.py` is a crude stand-in for UI work on machines without Docker. It is **not** the official
simulator, and nothing we report (Twin accuracy, Gauntlet scores, load tests, demo numbers) comes from it.
