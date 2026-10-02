---
document_id: DOC-HIST-001
filename: simulation_performance.md
category: historical_reports
document_type: benchmark_report
year: 2026
section: simulation_benchmarks
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - docs/hour-one.md
  - scripts/explore_sim.py
  - scripts/crisis_sim.py
---

# Simulation Performance & Benchmark Baseline Results

## 1. Verified Benchmark Experiments
Simulated on the official BUP Fuel Supply Simulator (`seed 12345`, baseline scenario):

| Policy / Experiment | Horizon | Service Level | Unmet Demand (L) | Key Operational Observations |
|---|---|---|---|---|
| **No-Op (Do Nothing)** | 3 Days (288 ticks) | 30.7% | >140,000 L | First stockout occurs at tick 66 (Tongi diesel). Depots reach full capacity and waste inbound refinery supply. |
| **Heuristic Refill (<40%)** | 3 Days (Calm) | 100.0% | 0 L | Under calm baseline conditions with no crisis events, a simple inventory threshold rule achieves perfect service level. |
| **Heuristic Refill (<40%)** | 6 Days (Supply Cliff) | 92.3% | 42,986 L | Refinery supply ends at tick 212. Depots run dry from Day 4. Daily unmet: Day 1-3: 0 L, Day 4: 774 L, Day 5: 15,126 L, Day 6: 27,086 L. |
| **Heuristic Refill (<40%)** | 3 Days + Crises | 98.7% | 4,820 L | Exposed to demand spike, route disruption, and station outage. Policy repeatedly retried disrupted route (8 failures) and failed to utilize Patiya-Mirpur backup. |
| **FuelGuard LP-v2 Candidate** | 3 Days + Crises | 99.8% | <350 L | Successfully engaged cross-division backup corridors and proactive inventory balancing. |

## 2. Key Learnings
Competence in fuel supply management is proven under **crisis response, corridor rerouting, and supply scarcity (containment)**, not during calm operations.
