"""LangGraph copilot (read-only: it never touches the write path).

    extract_facts -> match_playbook -> generate -> check_faithfulness -> synthesize

One graph serves all four jobs (explain / investigate / summarize / incident). The LLM only rewrites
structured facts into operator language; check_faithfulness discards any answer containing a number
that is not in the facts (or the word "saved"), and the deterministic template is used instead.

LangChain / LangGraph are imported lazily: without them, or without OPENAI_API_KEY, the copilot is the
template and the backend still starts. LangSmith tracing is on when LANGSMITH_TRACING=true and
LANGSMITH_API_KEY are set: every graph run is traced with its job, decision id and outcome.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any, TypedDict

from app.contracts import ExplainResponse

LLM_MODEL = os.getenv("COPILOT_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS = float(os.getenv("COPILOT_TIMEOUT_SECONDS", "8"))

PLAYBOOKS = {
    "demand_anomaly": "Demand spike: raise allocations to the affected region, keep reserves for the other region.",
    "demand_spike": "Demand spike: raise allocations to the affected region, keep reserves for the other region.",
    "route_disrupted": "Route disruption: reroute over a backup route; single-route stations go to containment.",
    "route_disruption": "Route disruption: reroute over a backup route; single-route stations go to containment.",
    "station_outage": "Station outage: send nothing to it; prepare a refill for when it reopens.",
    "depot_constrained": "Depot constraint: shift dispatch to the other depot where routes allow.",
    "depot_constraint": "Depot constraint: shift dispatch to the other depot where routes allow.",
    "supply_delayed": "Shipment delay: stretch depot stock, protect the stations closest to stockout.",
    "shipment_delay": "Shipment delay: stretch depot stock, protect the stations closest to stockout.",
    "supply_reduced": "Supply shortfall: ration by time to stockout, never draw a depot below reserve.",
    "supply_shortfall": "Supply shortfall: ration by time to stockout, never draw a depot below reserve.",
    "stale_data": "Stale data: recommend only; nothing executes until data is fresh.",
}

TASKS = {
    "decision": "Explain this recommendation in 4-7 short sentences: what we recommend, why, the binding "
                "constraint, projected impact, the best alternative, and whether a human must approve.",
    "station": "Answer the operator's question about this station in 3-5 short sentences.",
    "network": "Summarize the network situation in 2-4 short sentences: are we OK and what is going wrong.",
    "incident": "Write a short incident report: summary, what we decided and why, what happened, what is open. "
                "Use short paragraphs with the headings Summary, Decisions, Outcome, Open.",
}

SYSTEM_PROMPT = (
    "You are FuelGuard Copilot. You explain a SIMULATED fuel network to its operators.\nRules:\n"
    "1. Use only the FACTS. Never introduce a number, place or quantity that is not in the FACTS.\n"
    "2. Projections are not outcomes: write 'X L projected unmet demand avoided vs the no-action "
    "counterfactual', never 'saved X L'.\n"
    "3. Plain English, no marketing words. If the FACTS do not answer the question, say so."
)


class CopilotState(TypedDict, total=False):
    facts: dict
    question: str
    playbook: str
    draft: str
    rejected_reason: str | None
    response: ExplainResponse


def _kinds(facts: dict) -> list[str]:
    kinds = [s["kind"] for s in facts.get("signals", [])]
    kinds += [e["type"] if isinstance(e, dict) else e for e in facts.get("events", [])]
    kinds += [e["type"] for e in facts.get("active_events", [])]
    if facts.get("stale"):
        kinds.append("stale_data")
    return kinds


def node_extract_facts(state: CopilotState) -> dict:
    return {"facts": state["facts"]}


def node_match_playbook(state: CopilotState) -> dict:
    picked = list(dict.fromkeys(PLAYBOOKS[k] for k in _kinds(state["facts"]) if k in PLAYBOOKS))
    try:
        from app.rag.pipeline import get_rag_pipeline
        rag = get_rag_pipeline()
        kinds = _kinds(state["facts"])
        query = f"policy constraints {' '.join(kinds[:2])}" if kinds else "depot reserve policy"
        rag_hits = rag.search(query=query, category="rules_policies", top_k=1)
        if rag_hits:
            picked.append(f"Grounded Policy [{rag_hits[0].source}]: {rag_hits[0].content[:150].strip()}...")
    except Exception:
        pass
    return {"playbook": " ".join(picked) or "Normal operations: keep headroom, prefer fast routes."}


def _llm():
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None
    return ChatOpenAI(model=LLM_MODEL, temperature=0.1, timeout=LLM_TIMEOUT_SECONDS, max_retries=1,
                      tags=["fuelguard", "copilot"])


def node_generate(state: CopilotState) -> dict:
    llm = _llm()
    if llm is None:
        return {"draft": "", "rejected_reason": "no LLM configured"}
    from langchain_core.messages import HumanMessage, SystemMessage
    facts = state["facts"]
    human = (f"TASK: {TASKS.get(facts.get('kind', 'decision'), TASKS['decision'])}\n"
             f"QUESTION: {state.get('question') or '(none)'}\nPLAYBOOK: {state['playbook']}\n"
             f"FACTS (JSON):\n{json.dumps(facts, default=str)}")
    try:
        out = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=human)])
        return {"draft": str(out.content).strip(), "rejected_reason": None}
    except Exception as exc:  # timeouts, rate limits, network: fall back, never fail the request
        return {"draft": "", "rejected_reason": f"LLM error: {type(exc).__name__}"}


_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[float]:
    out = set()
    for m in _NUM.findall(text):
        try:
            out.add(float(m.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def _close(n: float, a: float) -> bool:
    # Rounded restatements are fine: 1,496.4 -> "1,496", 3.24 h -> "3.2 h".
    return abs(n - a) <= (0.5 if abs(a) >= 10 else 0.051)


def unsupported_numbers(draft: str, facts: dict) -> set[float]:
    """Numbers in the draft that do not appear in the facts (allowing rounding, % and thousands)."""
    base = _numbers(json.dumps(facts, default=str))
    allowed = base | {a * 100 for a in base if a <= 1} | {a / 1000 for a in base if a >= 1000}
    allowed |= {float(i) for i in range(0, 11)}  # counting words like "two routes"
    return {n for n in _numbers(draft) if not any(_close(n, a) for a in allowed)}


def faithfulness_problem(draft: str, facts: dict) -> str | None:
    bad = unsupported_numbers(draft, facts)
    if bad:
        return "unsupported numbers: " + ", ".join(f"{b:g}" for b in sorted(bad)[:5])
    if re.search(r"\bsav(ed|es|ing)\b", draft, re.I):
        return "broke the projection wording rule"
    return None


def node_check_faithfulness(state: CopilotState) -> dict:
    draft = state.get("draft") or ""
    if not draft:
        return {}
    problem = faithfulness_problem(draft, state["facts"])
    return {"draft": "", "rejected_reason": problem} if problem else {}


def make_synthesize(template: Callable[[dict], ExplainResponse]):
    def node_synthesize(state: CopilotState) -> dict:
        facts = state["facts"]
        base = template(facts)
        if state.get("draft"):
            return {"response": ExplainResponse(text=state["draft"], cited_facts=base.cited_facts, source="llm",
                                                confidence=facts.get("confidence", 1.0), llm_model=LLM_MODEL,
                                                is_fallback=False)}
        if state.get("rejected_reason") and state["rejected_reason"] != "no LLM configured":
            base.cited_facts.append(f"copilot fallback: {state['rejected_reason']}")
        return {"response": base}
    return node_synthesize


NODES = ("extract_facts", "match_playbook", "generate", "check_faithfulness", "synthesize")


def build_graph(template: Callable[[dict], ExplainResponse]) -> Any | None:
    """Compiled LangGraph, or None when langgraph is not installed (then run_sequential is used)."""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None
    g = StateGraph(CopilotState)
    fns = (node_extract_facts, node_match_playbook, node_generate, node_check_faithfulness, make_synthesize(template))
    for n, fn in zip(NODES, fns, strict=True):
        g.add_node(n, fn)
    g.add_edge(START, NODES[0])
    for a, b in zip(NODES, NODES[1:], strict=False):
        g.add_edge(a, b)
    g.add_edge(NODES[-1], END)
    return g.compile()


def run_sequential(state: CopilotState, template: Callable[[dict], ExplainResponse]) -> CopilotState:
    for fn in (node_extract_facts, node_match_playbook, node_generate, node_check_faithfulness,
               make_synthesize(template)):
        state = {**state, **fn(state)}
    return state
