"""Copilot evaluation dataset and checks (blueprint: "LangSmith tracing + faithfulness eval").

Each case is structured facts plus what a correct answer must contain. The same checks run
  - in pytest against the templates (always, no keys needed),
  - against the LLM path with scripts/copilot_eval.py when OPENAI_API_KEY is set,
  - as a LangSmith dataset + experiment with scripts/copilot_eval.py --langsmith.

Checks per answer:
  faithful        no number that is not in the facts, and no "saved" (projection wording rule)
  key_numbers     the numbers an operator needs are present (rounded forms accepted)
  forbidden       phrases that must never appear
"""
from __future__ import annotations

import json
from pathlib import Path

from app.contracts import DecisionRecord, NetworkSnapshot, Recommendation
from app.explain import templates as T
from app.explain.graph import _close, _numbers, faithfulness_problem

FIX = Path(__file__).resolve().parents[3] / "fixtures"
GATE = {"requires_human": True, "executable": True, "reasons": ["confidence 0.74 < 0.80"]}


def _fixtures() -> tuple[NetworkSnapshot, Recommendation]:
    snap = NetworkSnapshot.model_validate(json.loads((FIX / "snapshot.json").read_text()))
    rec = Recommendation.model_validate(json.loads((FIX / "recommendation.json").read_text()))
    return snap, rec


def load_cases() -> list[dict]:
    snap, rec = _fixtures()
    no_legs = rec.model_copy(update={"candidates": [c.model_copy(update={"legs": []}) for c in rec.candidates]})
    stale = rec.model_copy(update={"built_on_stale_data": True})
    contain = rec.model_copy(update={"mode": "containment"})
    fallback = rec.model_copy(update={"fallback_used": ["forecaster"]})
    record = DecisionRecord(decision_id="rec-0042", sim_tick=97, stage="verified", recommendation=rec,
                            approval={"decision": "approved", "by": "operator", "reason": "spike playbook"},
                            twin_check={"predicted_l": 200.0, "actual_l": 240.0, "error_l": 40.0})
    return [
        {"id": "explain-baseline", "facts": T.decision_facts(rec, snap, GATE), "question": None,
         "key_numbers": [3000, 1296, 0.74], "forbidden": ["saved"]},
        {"id": "explain-why-not-5000", "facts": T.decision_facts(rec, snap, GATE),
         "question": "Why not send 5,000 L instead?", "key_numbers": [550], "forbidden": ["saved"]},
        {"id": "explain-no-shipment", "facts": T.decision_facts(no_legs, snap, None), "question": None,
         "key_numbers": [], "forbidden": ["saved", "Send 3,000"]},
        {"id": "explain-stale", "facts": T.decision_facts(stale, snap, {**GATE, "executable": False}),
         "question": None, "key_numbers": [], "must_mention": ["stale"], "forbidden": ["saved"]},
        {"id": "explain-containment", "facts": T.decision_facts(contain, snap, GATE), "question": None,
         "key_numbers": [], "must_mention": ["ontainment"], "forbidden": ["saved"]},
        {"id": "explain-fallback", "facts": T.decision_facts(fallback, snap, GATE), "question": None,
         "key_numbers": [], "must_mention": ["fallback"], "forbidden": ["saved"]},
        {"id": "investigate-tongi", "facts": T.station_facts(snap, "station-tongi", rec),
         "question": "Can Tongi be resupplied?", "key_numbers": [], "must_mention": ["Tongi"], "forbidden": []},
        {"id": "summary-now", "facts": T.network_facts(snap), "question": None, "key_numbers": [0.867],
         "forbidden": []},
        {"id": "incident-window", "facts": T.incident_facts(snap, [record], 90, 98, []), "question": None,
         "key_numbers": [200, 240], "forbidden": ["saved"]},
    ]


def check(text: str, case: dict) -> dict:
    problems = []
    fp = faithfulness_problem(text, case["facts"])
    if fp:
        problems.append(fp)
    nums = _numbers(text) | {n / 100 for n in _numbers(text)}  # "86.7%" -> 0.867
    for k in case.get("key_numbers", []):
        if not any(_close(n, k) for n in nums):
            problems.append(f"missing {k:g}")
    for phrase in case.get("must_mention", []):
        if phrase.lower() not in text.lower():
            problems.append(f"does not mention '{phrase}'")
    for phrase in case.get("forbidden", []):
        if phrase.lower() in text.lower():
            problems.append(f"contains '{phrase}'")
    return {"passed": not problems, "problems": problems}


def run_case(case: dict, explainer) -> dict:
    out = explainer.run(case["facts"], case.get("question"))
    return {"id": case["id"], "source": out.source, **check(out.text, case), "text": out.text}
