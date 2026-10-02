---
document_id: DOC-EXT-003
filename: transportation_context.md
category: external_data
document_type: logistics_context
year: 2026
section: transport_geography
version: 1.0.0
last_updated: 2026-10-02 08:34:55 UTC
notice: "CORRIDOR REFERENCE CONTEXT: Operational geography reference for route logistics."
---

# Regional Logistics Corridors & Road Transportation Context

> [!NOTE]
> This document describes the physical transport corridors modeled in the FuelGuard simulation environment.

## 1. Dhaka North Distribution Corridor
- **Depot Hub:** Gazipur Depot (`depot-gazipur`).
- **Corridors:**
  - **Gazipur to Tongi (`route-gazipur-tongi`):** Heavy industrial traffic along the Dhaka-Mymensingh Highway. Dense freight movement; travel time 30 mins (2 ticks).
  - **Gazipur to Mirpur (`route-gazipur-mirpur`):** Arterial entry into the Dhaka metropolitan area via Ashulia and Gabtoli corridors; travel time 30 mins (2 ticks).

## 2. Chattogram & Southeastern Coastal Corridor
- **Depot Hub:** Patiya Depot (`depot-patiya`).
- **Corridors:**
  - **Patiya to Karnaphuli (`route-patiya-karnaphuli`):** Crosses Karnaphuli industrial and port arterial zone via Shah Amanat Bridge corridor; travel time 30 mins (2 ticks).
  - **Patiya to Cox's Bazar (`route-patiya-coxsbazar`):** Connects south along the N1 National Highway (Chattogram-Cox's Bazar Highway). Single-lane sections and terrain contribute to longer transit latency of 45 mins (3 ticks).

## 3. Inter-Division Arterial (Emergency Backup Corridor)
- **N1 Trunk Route:** The Dhaka-Chattogram Highway connects the two major economic divisions.
- **Cross-Division Links:** `route-gazipur-karnaphuli` and `route-patiya-mirpur`.
- Travel duration is 60 mins (4 ticks), with shipments capped at 5,000 L. These routes provide critical failover redundancy during localized depot stockouts or regional bridge closures.
