# Policy Gauntlet on the official simulator (961c6f3)

Candidate `lp-v2` vs baseline `greedy-v1`. Pass = service level drop ≤ 0.5 pp, unmet rise ≤ 2%, no extra allocation failures.

| Scenario | noop SL / unmet L | rule-40 SL / unmet L | greedy-v1 SL / unmet L | lp-v2 SL / unmet L | Result |
|---|---|---|---|---|---|
| Normal Operations (96 Ticks) | 87.92% / 11,245 | 100.00% / 0 | 100.00% / 0 | 100.00% / 0 | ✅ pass |
| Demand Spike in Dhaka (1.8x) | 83.48% / 16,621 | 100.00% / 0 | 100.00% / 0 | 100.00% / 0 | ✅ pass |
| Route Disruption (Gazipur -> Mirpur) | 87.92% / 11,245 | 100.00% / 0 | 100.00% / 0 | 100.00% / 0 | ✅ pass |
| Station Outage (Tongi Outage) | 86.41% / 12,643 | 95.39% / 4,289 | 95.39% / 4,289 | 95.39% / 4,289 | ✅ pass |
| Depot Constraint (Gazipur Throughput Restricted) | 87.92% / 11,245 | 100.00% / 0 | 100.00% / 0 | 100.00% / 0 | ❌ decision engine failed on 24 ticks |
| Shipment Delay (Gazipur Petrol Resupply +8 Ticks) | 87.92% / 11,245 | 100.00% / 0 | 87.92% / 11,245 | 87.92% / 11,245 | ❌ decision engine failed on 85 ticks ⚠ worse than the naive rule-40 by 12.1 pp |
| Combined Crisis (Spike + Disruption + Delay) | 81.52% / 19,142 | 100.00% / 0 | 82.97% / 17,642 | 82.44% / 18,192 | ❌ service level -0.53 pp vs greedy-v1; unmet demand +3.1% vs greedy-v1; decision engine failed on 70 ticks ⚠ worse than the naive rule-40 by 17.6 pp |
| Tongi cut off during a long Dhaka spike (single-route station) | 38.65% / 136,350 | 96.01% / 8,864 | 95.97% / 8,967 | 95.00% / 11,117 | ❌ service level -0.97 pp vs greedy-v1; unmet demand +24.0% vs greedy-v1 ⚠ worse than the naive rule-40 by 1.0 pp |
| Network-wide supply shortfall + late deliveries (scarcity) | 30.73% / 193,586 | 100.00% / 0 | 48.40% / 144,204 | 48.42% / 144,162 | ❌ decision engine failed on 228 ticks ⚠ worse than the naive rule-40 by 51.6 pp |
| Chattogram crisis: Patiya constrained, Patiya → Karnaphuli closed, highway spike | 40.18% / 127,873 | 96.73% / 7,000 | 80.25% / 42,227 | 80.27% / 42,179 | ❌ decision engine failed on 60 ticks ⚠ worse than the naive rule-40 by 16.5 pp |

Legs sent (accepted) per run: scenario-01-normal: noop 0, rule-40 17, greedy-v1 80, lp-v2 144; scenario-02-spike: noop 0, rule-40 20, greedy-v1 87, lp-v2 158; scenario-03-route: noop 0, rule-40 17, greedy-v1 80, lp-v2 144; scenario-04-outage: noop 0, rule-40 17, greedy-v1 69, lp-v2 128; scenario-05-depot: noop 0, rule-40 17, greedy-v1 80, lp-v2 144; scenario-06-delay: noop 0, rule-40 17, greedy-v1 0, lp-v2 0; scenario-07-combined: noop 0, rule-40 19, greedy-v1 4, lp-v2 4; scenario-08-tongi-cutoff: noop 0, rule-40 40, greedy-v1 338, lp-v2 568; scenario-09-shortfall: noop 0, rule-40 50, greedy-v1 84, lp-v2 249; scenario-10-chattogram: noop 0, rule-40 39, greedy-v1 213, lp-v2 417
