---
document_id: DOC-HIST-003
filename: demand_analysis.md
category: historical_reports
document_type: empirical_analysis
year: 2026
section: demand_patterns
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
source_files:
  - fixtures/demand_history.json
  - forecaster/
---

# Empirical Demand Analysis & Retail Station Profiles

## 1. Historical Dataset Overview
Derived from analysis of 1176 historical demand observation records (`fixtures/demand_history.json`). Observations record tick-by-tick fuel consumption across all 4 stations and 3 fuel types.

## 2. Station Consumption Characteristics
1. **Mirpur Station (`station-mirpur`):**
   - Profile: `urban_high`. Characterized by steep morning (08:00–10:00) and evening (17:00–20:00) commuter traffic. High Petrol and Octane ratio.
   - Diesel: Mean 84.1 L/tick, Max 140.6 L/tick.
2. **Tongi Station (`station-tongi`):**
   - Profile: `industrial`. High, continuous Diesel consumption powering industrial transport and standby generation.
   - Diesel: Mean 145.26 L/tick, Max 244.06 L/tick. Highest vulnerability to rapid stockout if Gazipur dispatch is interrupted.
3. **Karnaphuli Station (`station-karnaphuli`):**
   - Profile: `highway`. Steady heavy freight transport corridor connecting Chattogram port. Subject to regional demand factor 1.08.
   - Diesel: Mean 115.27 L/tick, Max 178.58 L/tick.
4. **Cox's Bazar Station (`station-coxsbazar`):**
   - Profile: `regional`. Tourist and long-distance passenger coaches.
   - Diesel: Mean 80.18 L/tick, Max 111.34 L/tick. Longest delivery lead time (3 ticks).

## 3. Diurnal and Weekly Seasonality
Demand exhibits predictable 24-hour periodicity with cyclical midday troughs and commuter peaks. Unmet demand rises sharply whenever consecutive delivery intervals exceed 6 ticks.
