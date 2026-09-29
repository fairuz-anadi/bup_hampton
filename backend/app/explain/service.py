"""Copilot entry point used by the API. Read-only; always returns an answer (LLM or template)."""
from __future__ import annotations

import os

from app.contracts import DecisionRecord, ExplainResponse, NetworkSnapshot, Recommendation
from app.explain import templates as T
from app.explain.graph import LLM_MODEL, build_graph, run_sequential

TEMPLATES = {"decision": T.explain_decision, "station": T.investigate_station, "network": T.network_summary_from_facts,
             "incident": T.incident_report}


class Explainer:
    def __init__(self) -> None:
        self.graphs = {kind: build_graph(fn) for kind, fn in TEMPLATES.items()}

    @property
    def engine(self) -> str:
        return "langgraph" if all(self.graphs.values()) else "sequential"

    @property
    def llm_enabled(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def describe(self) -> dict:
        return {"engine": self.engine, "llm": LLM_MODEL if self.llm_enabled else None,
                "tracing": os.getenv("LANGSMITH_TRACING", "").lower() == "true"
                and bool(os.getenv("LANGSMITH_API_KEY")),
                "project": os.getenv("LANGSMITH_PROJECT") or None}

    def run(self, facts: dict, question: str | None = None) -> ExplainResponse:
        kind = facts.get("kind", "decision")
        state = {"facts": facts, "question": question or ""}
        graph = self.graphs.get(kind)
        config = {"run_name": f"fuelguard-{kind}", "tags": ["fuelguard", kind],
                  "metadata": {"kind": kind, "decision_id": facts.get("decision_id"), "tick": facts.get("tick")}}
        out = graph.invoke(state, config=config) if graph is not None else run_sequential(state, TEMPLATES[kind])
        return out["response"]

    # convenience wrappers ----------------------------------------------------------------

    def explain(self, rec: Recommendation, snap: NetworkSnapshot | None, question: str | None = None,
                gate: dict | None = None) -> ExplainResponse:
        return self.run(T.decision_facts(rec, snap, gate), question)

    def investigate(self, snap: NetworkSnapshot, station_id: str, rec: Recommendation | None,
                    question: str | None = None) -> ExplainResponse | None:
        facts = T.station_facts(snap, station_id, rec)
        return None if facts is None else self.run(facts, question)

    def summarize(self, snap: NetworkSnapshot, question: str | None = None) -> ExplainResponse:
        return self.run(T.network_facts(snap), question)

    def incident(self, snap: NetworkSnapshot, records: list[DecisionRecord], from_tick: int, to_tick: int,
                 mode_log: list[dict] | None = None) -> ExplainResponse:
        return self.run(T.incident_facts(snap, records, from_tick, to_tick, mode_log))
