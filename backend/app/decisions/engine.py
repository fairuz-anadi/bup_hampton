"""Decision engine: once per simulator tick, recommendation -> confidence -> mode -> gate -> record.

  recommender      Turjo's IntelligenceService when it can be imported (RECOMMENDER=auto), the example
                   fixture (RECOMMENDER=fixture, for UI work), or nothing (RECOMMENDER=off).
  policy switch    every Twin future carries its own legs, so the engine offers each as a candidate and
                   selects the backend's active policy (`PUT /api/policy`), falling back to the
                   recommender's own choice.
  gate             app/decisions/gate.py: confidence factors, autonomy mode, guardrails, human review.
  records          recommendations with shipments become DecisionRecords through DecisionService.create(),
                   so approval, submission, outcome and the Twin self-check all use the backend's lifecycle.
  autopilot        in AUTONOMOUS mode, a decision the gate clears for auto-execution is approved as
                   "autopilot" through the same DecisionService.approve() a human would use.

The loop runs in the backend (not in a request), so decisions are made even when nobody has the UI open.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from app.contracts import Candidate, ComponentHealth, NetworkSnapshot, Recommendation
from app.decisions.gate import (
    AutonomyController,
    Guardrails,
    compute_factors,
    confidence,
    evaluate,
    futures_of,
    selected_legs,
    twin_accuracy,
)
from app.decisions.service import DecisionError, DecisionService
from app.obs.logging import log_event
from app.obs.metrics import FALLBACKS

Recommender = Callable[[NetworkSnapshot, list | None], Recommendation]
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "recommendation.json"


def load_recommender(kind: str | None = None) -> tuple[Recommender | None, str]:
    kind = (kind or os.getenv("RECOMMENDER", "auto")).lower()
    if kind == "off":
        return None, "off"
    if kind == "fixture":
        return _fixture_recommender, "fixture"
    errors = []
    # The intelligence lane imports via backend.app.* and forecaster.*; that resolves when the repo root is on
    # sys.path (scripts/dev_backend.py, the root test suite). Try both spellings.
    for mod in ("app.intel.service", "backend.app.intel.service"):
        try:
            module = __import__(mod, fromlist=["IntelligenceService"])
            intel = module.IntelligenceService()
        except Exception as exc:  # ImportError, or the intelligence lane failing to start
            errors.append(f"{mod}: {type(exc).__name__}: {exc}"[:160])
            continue

        def recommend(snap: NetworkSnapshot, history: list | None, _intel=intel) -> Recommendation:
            out = _intel.evaluate_and_recommend(snap, history)
            return Recommendation.model_validate(out.model_dump(mode="json"))
        return recommend, "intel"
    log_event("decisions.recommender_unavailable", errors=errors)
    return None, "unavailable"


def _fixture_recommender(snap: NetworkSnapshot, history: list | None) -> Recommendation:
    data = json.loads(FIXTURE.read_text())
    data.update(id=f"fx-{snap.tick}", tick=snap.tick, created_at=datetime.now(UTC).isoformat())
    data.setdefault("versions", {})["source"] = "fixture"
    return Recommendation.model_validate(data)


def apply_policy(rec: Recommendation, active: str | None) -> Recommendation:
    """Offer every Twin future as a candidate and select the active policy when the Twin has it."""
    futures = futures_of(rec)
    cands = [Candidate(id=f.candidate_id, policy=f.candidate_id, legs=f.legs) for f in futures
             if f.legs or f.candidate_id == "noop"]
    if not cands:
        return rec
    ids = {c.id for c in cands}
    chosen = active if active in ids else (rec.policy if rec.policy in ids else rec.selected_candidate_id)
    if chosen not in ids:
        return rec
    legs = next(c.legs for c in cands if c.id == chosen)
    return rec.model_copy(update={"candidates": cands, "selected_candidate_id": chosen, "legs": legs,
                                  "policy": chosen, "futures": futures,
                                  "versions": {**rec.versions, "policy": chosen}})


class DecisionEngine:
    def __init__(self, decisions: DecisionService, recommender: Recommender | None = None, source: str | None = None,
                 rails: Guardrails | None = None, autopilot: bool | None = None):
        if recommender is None and source is None:
            recommender, source = load_recommender()
        self.decisions = decisions
        self.recommender, self.source = recommender, source or "custom"
        self.rails = rails or Guardrails()
        self.autonomy = AutonomyController()
        self.autopilot = autopilot if autopilot is not None else os.getenv("AUTOPILOT", "true").lower() == "true"
        self.current: dict | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    # ---- the per-tick cycle ------------------------------------------------------------------

    async def cycle(self, snap: NetworkSnapshot, components: list[ComponentHealth], history: list | None = None,
                    active_policy: str | None = None) -> dict:
        async with self._lock:
            if self.current and self.current["tick"] == snap.tick and not self.current.get("error"):
                return self._refresh_view()
            rec, err = None, None
            if self.recommender is not None:
                try:
                    rec = await run_in_threadpool(self.recommender, snap, history)
                    rec = apply_policy(rec, active_policy)
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"[:300]
                    FALLBACKS.labels("decision_engine").inc()
                    log_event("fallback.activated", component="decision_engine", error=err)
            else:
                err = f"recommender {self.source}"
            self.last_error = err
            factors = compute_factors(snap, rec, components, twin_accuracy(self.decisions.repo.list(50, "verified")))
            if rec is None:
                factors["health"] = min(factors["health"], 0.5)
            mode = self.autonomy.observe(snap.tick, factors)
            conf = confidence(factors)
            view: dict = {"tick": snap.tick, "source": self.source, "error": err, "recommendation": None,
                          "gate": None, "record_stage": None, "autonomy": None}
            if rec is not None:
                model_conf = rec.confidence
                rec = rec.model_copy(update={"confidence": conf})
                gate = evaluate(rec, snap, mode, conf, self.rails)
                rec = rec.model_copy(update={"human_review_required": gate.requires_human,
                                             "status": "PENDING_REVIEW" if gate.requires_human else "AUTO_READY"})
                view.update(recommendation=rec.model_dump(mode="json"), gate=gate.as_dict(),
                            model_confidence=model_conf)
                if selected_legs(rec) and self.decisions.repo.get(rec.id) is None:
                    try:
                        await self.decisions.create(rec, gate.as_dict(), mode)
                    except DecisionError as exc:
                        log_event("decision.create_failed", decision_id=rec.id, code=exc.code)
                    if gate.auto_execute and mode == "AUTONOMOUS" and self.autopilot:
                        await self._autoexecute(rec.id, conf)
                log_event("decision.evaluated", decision_id=rec.id, sim_tick=snap.tick, mode=mode,
                          confidence=conf, requires_human=gate.requires_human, executable=gate.executable)
            self.current = view
            return self._refresh_view()

    async def _autoexecute(self, decision_id: str, conf: float) -> None:
        try:
            await self.decisions.approve(decision_id, by="autopilot",
                                         reason=f"auto-executed inside guardrails (AUTONOMOUS, confidence {conf:.2f})")
            self.autonomy._note(self.autonomy.last_tick, f"Autopilot executed {decision_id}.")
        except Exception as exc:
            log_event("decision.autopilot_failed", decision_id=decision_id, error=repr(exc)[:200])

    def _refresh_view(self) -> dict:
        view = self.current
        rid = (view.get("recommendation") or {}).get("id")
        record = self.decisions.repo.get(rid) if rid else None
        view["record_stage"] = record.stage if record else None
        view["autonomy"] = self.autonomy.view() | {"autopilot": self.autopilot}
        return view

    async def run(self, snapshot: Callable[[], NetworkSnapshot | None],
                  components: Callable[[], list[ComponentHealth]],
                  history: Callable[[], list | None], policy: Callable[[], str | None],
                  interval: float = 1.0, on_error: Callable[[Exception], Awaitable[None]] | None = None) -> None:
        """Background loop: evaluate every new tick."""
        while True:
            snap = snapshot()
            if snap is not None and (self.current is None or self.current["tick"] != snap.tick
                                     or self.current.get("error")):
                try:
                    await self.cycle(snap, components(), history(), policy())
                except Exception as exc:  # never let the loop die
                    log_event("decision.engine_error", error=repr(exc)[:200])
            await asyncio.sleep(interval)

    # ---- health ------------------------------------------------------------------------------

    def health(self) -> ComponentHealth:
        if self.recommender is None:
            return ComponentHealth(name="Decision engine", status="down", detail=f"recommender {self.source}")
        if self.last_error:
            return ComponentHealth(name="Decision engine", status="degraded", detail=self.last_error[:120])
        return ComponentHealth(name="Decision engine", status="healthy",
                               detail=None if self.source == "intel" else f"source: {self.source}")


def scoreboard(records: list, noop_id: str = "noop", baseline_id: str = "greedy-v1") -> dict:
    """Counterfactual scoreboard over executed decisions. Every number here is a Twin projection except
    the verified Twin error, which compares a projection with what the simulator actually did."""
    executed = [r for r in records if r.stage in ("submitted", "outcome", "verified") and r.recommendation]
    avoided_noop = avoided_base = 0.0
    rows = []
    for r in executed:
        fs = {f.candidate_id: f.network_unmet_liters for f in futures_of(r.recommendation)}
        chosen = r.recommendation.selected_candidate_id
        if chosen not in fs:
            continue
        a_noop = max(0.0, fs[noop_id] - fs[chosen]) if noop_id in fs else None
        a_base = fs[baseline_id] - fs[chosen] if baseline_id in fs and chosen != baseline_id else None
        avoided_noop += a_noop or 0.0
        avoided_base += a_base or 0.0
        rows.append({"decision_id": r.decision_id, "tick": r.sim_tick, "policy": chosen,
                     "by": (r.approval or {}).get("by"),
                     "projected_unmet_l": fs[chosen], "avoided_vs_noop_l": a_noop, "vs_baseline_l": a_base,
                     "twin_check": r.twin_check})
    checks = [r.twin_check for r in records if r.twin_check]
    return {"executed": len(rows), "projected_avoided_vs_noop_l": round(avoided_noop, 1),
            "projected_vs_baseline_l": round(avoided_base, 1),
            "verified": len(checks),
            "mean_twin_error_l": round(sum(c["error_l"] for c in checks) / len(checks), 1) if checks else None,
            "rows": rows[:50],
            "note": "Projected by the Decision Twin, not outcomes. Only mean_twin_error_l compares with the simulator."}
