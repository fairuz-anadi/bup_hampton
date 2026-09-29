"""FuelGuard Chatbot Prompts and Live Context Generator."""
from __future__ import annotations

from typing import Any

from app.contracts import NetworkSnapshot, Recommendation

SYSTEM_PROMPT = """You are FuelGuard AI Assistant, an expert conversational operational copilot for FuelGuard.
FuelGuard is a resilient decision-support system for fuel supply operations built for the BUP CSE Fest 2026 hackathon.

SYSTEM DOMAIN & ARCHITECTURE KNOWLEDGE:
1. SIMULATED ENVIRONMENT:
   - FuelGuard runs strictly against the official BUP Fuel Supply Simulator.
   - It does not touch real fuel infrastructure, real purchases, or physical dispatches.
   - Time moves in simulation ticks (typically 15 minutes per tick).

2. CORE ENTITIES:
   - Fuel Types: DIESEL, PETROL, and OCTANE. Every depot and station tracks inventory and capacity for all three.
   - Depots: Major supply hubs (e.g., depot-gazipur, depot-narayanganj) with total capacity, current inventory, and per-tick dispatch limits (typically 12,000 L/tick).
   - Stations: Retail stations (e.g., station-mirpur, station-uttara, station-dhanmondi, station-mohakhali, etc.) with individual capacities, inventories, demand profiles, and multipliers.
   - Routes: Road links between depots and stations with transit durations (transit_ticks, e.g., 2 ticks) and max shipment capacity (e.g., 5,000 L). Routes can experience DISRUPTED status.
   - In-Transit Ledger: Fuel currently moving on roads (PENDING or IN_TRANSIT). Counting in-transit fuel prevents duplicate shipments and tank overflows.

3. DECISION ENGINE & POLICIES:
   - Policies:
     * `greedy-v1`: Fast, heuristic-based rationing prioritizing stations closest to stockout.
     * `lp-v1`: Linear programming solver balancing network-wide unmet demand and route costs.
     * `baseline-v1`: Passive baseline (no action) used for counterfactual comparison.
   - Decision Twin: Multi-tick future simulator that evaluates candidate allocation plans against the "No-Action" counterfactual, quantifying "Projected Unmet Demand Avoided (Litres)".

4. AUTONOMY STATE MACHINE & GATE:
   - Modes:
     * `MANUAL`: Human operator must explicitly approve every recommendation.
     * `SUPERVISED`: Standard policy recommendations auto-execute when confidence is high; anomalous or low-confidence plans require human review.
     * `AUTONOMOUS`: Full auto-execution if system health and confidence remain above thresholds for consecutive ticks.
   - Confidence Gate: Evaluates simulator connectivity, data freshness, event stream health, and Twin projection variance.

5. CHAOS LAB & CRISIS PLAYBOOKS:
   - Demand Spike: Rapid demand surge. Strategy: Prioritize high-throughput corridors while preserving peer depot reserves.
   - Route Disruption: Route severed. Strategy: Shift dispatches to backup routes or initiate station containment.
   - Depot Constraint: Depot bottlenecked. Strategy: Re-route dispatches to the alternative depot.
   - Shipment Delay: Inbound refinery supply delayed. Strategy: Ration depot inventory and protect stations nearest to depletion.
   - Station Outage: Station temporarily closed. Strategy: Halt all dispatches to the closed station until re-opened.

6. USER INTERFACE PAGES:
   - Overview: High-level KPIs, network map, live alerts, and quick actions.
   - Decision Center: Current tick recommendation, candidate comparison, Decision Twin projections, confidence breakdown, and one-click human review (Approve/Modify/Reject).
   - Scenario Lab: Chaos injection, fault controls, crisis rehearsal, pacer controls, and policy Gauntlet benchmarks.
   - System Health: Real-time telemetry, circuit breaker state, Prometheus metrics, and latency distribution.
   - Architecture & History: System topology, event logs, and historical decision records.

YOUR ROLE & TONE:
- Professional, concise, precise, operational operator tone.
- Format responses cleanly using GitHub-flavored Markdown (bullet points, bold highlights, `code` for IDs/ticks/numbers).
- When live facts are provided below, answer based on the real-time simulation state.
- If data is missing or if the simulator is paused/stale, proactively advise the operator on how to proceed.
"""


def build_live_context(
    snapshot: NetworkSnapshot | None,
    current_view: dict[str, Any] | None,
) -> str:
    """Constructs a structured live context summary from current system state."""
    if snapshot is None:
        return "LIVE SYSTEM STATE: No active snapshot available (simulator not connected or initializing)."

    lines = ["LIVE SYSTEM STATE:"]
    # 1. Simulator clock & status
    sim_st = snapshot.sim_status
    clock = snapshot.sim_time or "Unknown"
    lines.append(f"- Simulation Tick: {snapshot.tick} ({sim_st}, Sim Time: {clock})")

    # 2. Freshness & circuit breaker
    if snapshot.freshness:
        circuit = snapshot.freshness.circuit
        stale = "YES (Data out of date)" if snapshot.freshness.stale else "NO (Fresh)"
        lines.append(f"- Data Freshness: {stale} | Circuit Breaker: {circuit}")
        if snapshot.freshness.reasons:
            lines.append(f"  * Freshness notes: {'; '.join(snapshot.freshness.reasons[:2])}")

    # 3. Active disruptions and events
    events = [e for e in snapshot.events if getattr(e, "status", "") == "ACTIVE"]
    if events:
        lines.append(f"- Active Crises/Events ({len(events)}):")
        for ev in events[:4]:
            lines.append(f"  * Event #{ev.id}: {ev.type} (ticks {ev.start_tick}-{ev.end_tick})")
    else:
        lines.append("- Active Crises/Events: None (Normal operational conditions)")

    # 4. Route status
    disrupted_routes = [r for r in snapshot.routes if getattr(r, "status", "") == "DISRUPTED"]
    if disrupted_routes:
        lines.append(f"- Disrupted Routes ({len(disrupted_routes)}): {', '.join(r.id for r in disrupted_routes[:5])}")
    else:
        lines.append(f"- Routes: All {len(snapshot.routes)} routes available")

    # 5. Station inventories and stockout alerts
    critical_stations = []
    for s in snapshot.stations:
        if getattr(s, "status", "") == "OUTAGE":
            critical_stations.append(f"Station {s.id}: OUTAGE (closed)")
            continue
        for fuel, inv in s.inventory.items():
            cap = s.capacity.get(fuel, 1)
            ratio = (inv / cap) if cap > 0 else 0
            if ratio < 0.20:
                critical_stations.append(
                    f"Station {s.id} ({fuel}): {inv:.0f} L ({ratio*100:.0f}% capacity - Low Stock)"
                )
    if critical_stations:
        lines.append(f"- Stations Requiring Attention ({len(critical_stations)}):")
        for alert in critical_stations[:5]:
            lines.append(f"  * {alert}")
    else:
        lines.append("- Station Inventories: All stations maintain adequate fuel reserves")

    # 6. Current Recommendation & Autonomy
    if current_view:
        rec = current_view.get("recommendation")
        gate = current_view.get("gate")
        autonomy = current_view.get("autonomy")

        if rec:
            rec_id = rec.get("id", "N/A")
            pol = rec.get("policy", "N/A")
            legs_count = len(rec.get("legs", []))
            unmet_avoided = rec.get("projected_unmet_avoided", 0)
            lines.append(
                f"- Current Recommendation ({rec_id}): Policy `{pol}`, "
                f"{legs_count} proposed legs, ~{unmet_avoided:.0f} L unmet demand avoided"
            )
        if gate:
            human_req = gate.get("requires_human", False)
            conf = gate.get("confidence", 0)
            lines.append(
                f"- Confidence Gate: Confidence {conf*100:.1f}%, "
                f"Human Review Required: {'YES' if human_req else 'NO'}"
            )
        if autonomy:
            mode = autonomy.get("mode", "UNKNOWN")
            armed = autonomy.get("armed", False)
            lines.append(f"- Autonomy Mode: `{mode}` (Armed: {armed})")

    return "\n".join(lines)


STARTER_PROMPTS = [
    "What is the current network status and simulation tick?",
    "Explain the latest recommendation and proposed shipments",
    "Are any stations at risk of fuel stockout?",
    "How does the Autonomy Gate decide when to require human review?",
    "What crisis playbooks are available in the Scenario Lab?",
]
