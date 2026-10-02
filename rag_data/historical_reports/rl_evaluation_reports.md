---
document_id: DOC-HIST-004
filename: rl_evaluation_reports.md
category: historical_reports
document_type: evaluation_report
year: 2026
section: rl_benchmarks
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - backend/app/rl/
  - rl/evaluation/
---

# Reinforcement Learning Evaluation & Policy Benchmark Report

## 1. Experimental Training Setup
- **Algorithm:** Proximal Policy Optimization (PPO).
- **Environment:** BUP Fuel Supply Simulator Gymnasium wrapper (`FuelEnv`).
- **Observation Space:** 48-dimensional normalized state vector.
- **Evaluation Scenarios:** Normal operation, Demand Spike, Route Disruption, Depot Constraint, and Supply Cliff.

## 2. Quantitative Performance Comparison (3-Day Horizon)

| Metric | No-Op Policy | Greedy-v1 | LP-v2 Optimizer | Trained PPO Agent |
|---|---|---|---|---|
| Service Level (%) | 30.7% | 98.7% | 99.8% | 99.4% |
| Total Unmet Demand (L) | 142,500 L | 4,820 L | 340 L | 680 L |
| Route Disruption Failures | 0 | 8 | 0 | 0 |
| Depot Reserve Breaches | 0 | 0 | 0 | 0 |
| Action Inference Latency | <1 ms | 2 ms | 35 ms | 4 ms |

## 3. Key Findings
- The PPO agent successfully learns to avoid disrupted routes and respect depot reserves without requiring brute-force mathematical programming solvers.
- Policy inference completes in approximately 4 ms, making it over 8x faster than LP optimization under large multi-station topologies.
- For maximum system resilience, the RL agent operates with the deterministic LP-v2 and Greedy-v1 policies as verified fallbacks.
