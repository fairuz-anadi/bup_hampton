"""
FuelGuard Copilot LangGraph Workflow (backend/app/copilot/graph.py)
Orchestrates explanation generation using LangGraph, LangChain ChatOpenAI,
and full observability via LangSmith tracing.
"""

import os
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.contracts import (
    ExplainResponse,
    NetworkSnapshot,
    Recommendation,
    RouteStatus,
    StationStatus,
)
from app.copilot.fallback import DeterministicCopilot


class CopilotState(TypedDict):
    snapshot: NetworkSnapshot
    recommendation: Recommendation
    query: str | None
    facts: list[str]
    playbook: str
    explanation_text: str
    response: ExplainResponse | None


# Crisis playbooks reference (§7)
PLAYBOOKS = {
    "demand_spike": (
        "Demand spike detected: Prioritize high-throughput urban routes. "
        "Avoid drawing depot below reserve."
    ),
    "route_disruption": (
        "Route disruption detected: Reroute shipments to secondary corridors if available. "
        "If single-route station, engage containment."
    ),
    "station_outage": (
        "Station outage active: Halt dispatches to closed station; "
        "conserve fuel for post-recovery surge."
    ),
    "depot_constraint": "Depot constraint active: Throttle dispatches, balance allocations with peer depot.",
    "shipment_delay": "Supply arrival delayed: Extend rationing horizon; preserve station safety buffers.",
    "normal": "Standard operations: Maintain optimal tank headroom and minimize long-transit routing costs.",
}


def node_extract_facts(state: CopilotState) -> dict:
    """Extracts strictly verified ground-truth facts from the snapshot & recommendation."""
    rec = state["recommendation"]
    snap = state["snapshot"]
    facts: list[str] = []

    # 1. State facts
    sim_st = getattr(snap, "sim_status", getattr(snap, "status", "RUNNING"))
    facts.append(f"Simulation Tick: {snap.tick}, Status: {sim_st}")
    for r_id, r in snap.route_map.items():
        if r.status != RouteStatus.AVAILABLE:
            facts.append(f"Disrupted route: {r_id} connecting {r.depot_id} to {r.station_id}")
    for s_id, s in snap.station_map.items():
        if s.status != StationStatus.OPEN:
            facts.append(f"Station {s_id} is in {s.status} status")

    # 2. Urgent risks
    for risk in rec.risks[:3]:
        facts.append(
            f"Risk: {risk.station_id} {risk.fuel.value} has {risk.current_inventory:.0f} L "
            f"({risk.time_to_stockout_hours} h until stockout, P={risk.p_stockout:.2f})"
        )

    # 3. Proposed legs
    for leg in rec.legs:
        facts.append(
            f"Dispatch: {leg.quantity_liters:.0f} L {leg.fuel.value} via {leg.route_id} "
            f"({leg.depot_id} -> {leg.station_id}) taking {leg.transit_ticks} ticks"
        )

    # 4. Decision Twin metrics
    facts.append(
        f"Decision Twin Impact: {rec.projected_unmet_avoided:.0f} L unmet demand avoided "
        f"vs No-Op counterfactual ({rec.before_projected_unmet:.0f} L -> {rec.after_projected_unmet:.0f} L)"
    )

    return {"facts": facts}


def node_match_playbook(state: CopilotState) -> dict:
    """Identifies matching crisis doctrine based on active signals and network events."""
    rec = state["recommendation"]
    active_types = [s.type for s in rec.signals]

    selected_playbooks = []
    for sig_type in active_types:
        if sig_type in PLAYBOOKS:
            selected_playbooks.append(PLAYBOOKS[sig_type])

    playbook_summary = (
        " | ".join(selected_playbooks) if selected_playbooks else PLAYBOOKS["normal"]
    )
    return {"playbook": playbook_summary}


def node_generate_explanation(state: CopilotState) -> dict:
    """Invokes OpenAI LLM with LangChain & LangSmith tracing to create operator explanation."""
    facts = state["facts"]
    playbook = state["playbook"]
    rec = state["recommendation"]
    user_query = state.get("query") or "Explain the operational rationale for this recommendation."

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("<"):
        # Fallback to template if no valid key
        fallback = DeterministicCopilot().explain(rec, state["snapshot"], user_query)
        return {
            "explanation_text": fallback.text,
            "response": fallback,
        }

    system_prompt = (
        "You are FuelGuard Copilot, the AI operational assistant for the national fuel supply chain.\n"
        "Your task is to provide clear, concise, inspectable explanations to human grid dispatchers.\n"
        "RULES:\n"
        "1. Strictly adhere to the provided CITED FACTS. Never invent numbers, capacities, or gallons.\n"
        "2. Wording rule: write 'X L projected unmet demand avoided vs the no-action counterfactual' "
        "and never 'FuelGuard saved X L'.\n"
        "3. Include structured sections: [Operational Rationale], [Cited Facts], "
        "[Crisis Playbook Alignment], [Counterfactual Comparison].\n"
        "4. Keep the tone professional, direct, and actionable."
    )

    human_prompt = (
        f"User Query: {user_query}\n\n"
        f"Policy Chosen: {rec.policy}\n"
        f"Confidence Score: {rec.confidence:.2f}\n"
        f"Human Review Required: {rec.human_review_required}\n"
        f"Active Playbook Guidance: {playbook}\n\n"
        "GROUND-TRUTH FACTS:\n" + "\n".join(f"- {f}" for f in facts)
    )

    try:
        # LangChain ChatOpenAI automatically reads LANGSMITH_* env vars for tracing!
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.1,
            api_key=api_key,
            tags=["FuelGuard", "Intel-Copilot", rec.policy],
            metadata={
                "project": "FuelGuard",
                "policy": rec.policy,
                "tick": rec.tick,
                "confidence": rec.confidence,
            },
        )
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt),
        ])
        explanation = response.content
        return {"explanation_text": explanation}
    except Exception as exc:
        # Fallback gracefully
        fallback = DeterministicCopilot().explain(rec, state["snapshot"], user_query)
        fallback.text += f"\n\n*(Note: LLM generation error: {str(exc)}. Displaying deterministic template.)*"
        return {
            "explanation_text": fallback.text,
            "response": fallback,
        }


def node_synthesize(state: CopilotState) -> dict:
    """Formats final ExplainResponse."""
    if state.get("response"):
        return {"response": state["response"]}

    rec = state["recommendation"]
    final = ExplainResponse(
        text=state["explanation_text"],
        cited_facts=state["facts"],
        confidence=rec.confidence,
        llm_model="gpt-4o-mini",
        is_fallback=False,
    )
    return {"response": final}


def build_copilot_graph():
    """Builds and compiles the LangGraph StateGraph."""
    graph = StateGraph(CopilotState)
    graph.add_node("extract_facts", node_extract_facts)
    graph.add_node("match_playbook", node_match_playbook)
    graph.add_node("generate_explanation", node_generate_explanation)
    graph.add_node("synthesize", node_synthesize)

    graph.add_edge(START, "extract_facts")
    graph.add_edge("extract_facts", "match_playbook")
    graph.add_edge("match_playbook", "generate_explanation")
    graph.add_edge("generate_explanation", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()
