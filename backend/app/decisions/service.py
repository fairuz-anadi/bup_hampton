"""Decision lifecycle: recommendation -> human review -> submission -> outcome -> Twin check.

    projected/gated --approve--> approved --writer--> submitted --horizon ends--> verified
                    --reject---> rejected

The intelligence lane creates records with `create()` (in-process) or POST /api/decisions.
Approving posts the selected candidate's legs, or the operator's modified legs, through the
allocation writer. Every stage is saved, so the record is also the audit trail.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.contracts import AllocationLeg, DecisionRecord, Recommendation
from app.db.repo import DecisionRepo
from app.obs.logging import log_event
from app.obs.metrics import DECISIONS, TWIN_ERROR, TWIN_ERROR_HIST
from app.sim.allocations import AllocationWriter
from app.sim.errors import SimulatorError
from app.state.store import StateStore

REVIEWABLE = ("projected", "gated")


class DecisionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class DecisionService:
    def __init__(self, repo: DecisionRepo, writer: AllocationWriter, store: StateStore, deployment_version: str):
        self.repo = repo
        self.writer = writer
        self.store = store
        self.deployment_version = deployment_version

    async def create(self, rec: Recommendation, gate: dict | None = None, mode: str | None = None) -> DecisionRecord:
        if self.repo.get(rec.id):
            raise DecisionError(409, "DECISION_EXISTS", f"Decision {rec.id} already exists.")
        if rec.selected_candidate_id not in {c.id for c in rec.candidates}:
            raise DecisionError(422, "UNKNOWN_CANDIDATE", "selected_candidate_id is not one of the candidates.")
        record = DecisionRecord(
            decision_id=rec.id, sim_tick=rec.tick, created_at=datetime.now(UTC),
            stage="gated" if gate else "projected", mode=mode, recommendation=rec, gate=gate,
            versions={**rec.versions, "deployment": self.deployment_version})
        await self._save(record)
        return record

    async def approve(self, decision_id: str, by: str, reason: str | None,
                      legs: list[AllocationLeg] | None = None) -> DecisionRecord:
        record = self._reviewable(decision_id)
        if record.recommendation.built_on_stale_data:
            raise DecisionError(409, "STALE_RECOMMENDATION",
                                "Built on stale data. Refresh and re-run the recommendation before approving.")
        selected = next(c for c in record.recommendation.candidates
                        if c.id == record.recommendation.selected_candidate_id)
        modified = legs is not None
        final_legs = legs if modified else selected.legs
        record = record.model_copy(update={"stage": "approved", "approval": {
            "decision": "approved", "by": by, "reason": reason, "modified": modified,
            "candidate_id": None if modified else selected.id,
            "at_tick": self.store.snapshot.tick if self.store.snapshot else None}})
        await self._save(record)
        if not final_legs:  # approving "do nothing" is a valid decision
            return record
        response = await self.writer.submit(decision_id, final_legs)
        record = record.model_copy(update={"stage": "submitted", "submissions": response.submissions})
        await self._save(record)
        return record

    async def reject(self, decision_id: str, by: str, reason: str) -> DecisionRecord:
        record = self._reviewable(decision_id)
        record = record.model_copy(update={"stage": "rejected", "approval": {
            "decision": "rejected", "by": by, "reason": reason}})
        await self._save(record)
        return record

    # ------------------------------------------------------------------ outcome + Twin self-check

    async def check_outcomes(self) -> int:
        """Close decisions whose projection horizon has ended: record the actual network unmet demand
        from the simulator and compare it with what the Twin projected for the chosen option."""
        snap = self.store.snapshot
        if snap is None or snap.freshness.stale:
            return 0
        done = 0
        for record in self.repo.by_stage("submitted", "approved"):
            rec = record.recommendation
            horizon = max((f.horizon_ticks for f in rec.futures), default=24)
            if snap.tick < record.sim_tick + horizon:
                continue
            try:
                actual = await self._actual_unmet(record.sim_tick, horizon)
            except SimulatorError:
                return done
            if actual is None:
                record = record.model_copy(update={"stage": "outcome", "outcome": {"complete": False,
                    "reason": "demand history no longer covers the horizon"}})
                await self._save(record)
                continue
            statuses = await self._allocation_statuses(record)
            chosen = (record.approval or {}).get("candidate_id") or rec.selected_candidate_id
            future = next((f for f in rec.futures if f.candidate_id == chosen), None)
            update = {"stage": "outcome", "outcome": {
                "complete": True, "horizon_ticks": horizon, "actual_network_unmet_l": round(actual, 1),
                "allocation_statuses": statuses, "closed_at_tick": snap.tick}}
            if future is not None and not (record.approval or {}).get("modified"):
                err = abs(future.network_unmet_liters - actual)
                update["stage"] = "verified"
                update["twin_check"] = {"predicted_l": future.network_unmet_liters, "actual_l": round(actual, 1),
                                        "error_l": round(err, 1)}
                TWIN_ERROR.set(err)
                TWIN_ERROR_HIST.observe(err)
            record = record.model_copy(update=update)
            await self._save(record)
            done += 1
        return done

    async def _actual_unmet(self, start_tick: int, horizon: int) -> float | None:
        rows_needed = (horizon + 2) * 12 + 24  # 4 stations x 3 fuels per tick, plus slack
        history = (await self.store.client.demand_history(limit=min(2000, rows_needed * 4))).data
        window = [o for o in history if start_tick < o.tick <= start_tick + horizon]
        if not history or min(o.tick for o in history) > start_tick + 1:
            return None
        return sum(o.unmet_liters for o in window)

    async def _allocation_statuses(self, record: DecisionRecord) -> dict[str, str]:
        ids = {s.sim_allocation_id for s in record.submissions if s.sim_allocation_id}
        if not ids:
            return {}
        by_key = self.store.allocations_by_key()
        return {k: a.status for k, a in by_key.items() if a.id in ids}

    # ------------------------------------------------------------------ helpers

    def _reviewable(self, decision_id: str) -> DecisionRecord:
        record = self.repo.get(decision_id)
        if record is None:
            raise DecisionError(404, "DECISION_NOT_FOUND", f"No decision {decision_id}.")
        if record.stage not in REVIEWABLE:
            raise DecisionError(409, "NOT_REVIEWABLE", f"Decision is already {record.stage}.")
        return record

    async def _save(self, record: DecisionRecord) -> None:
        await self.repo.save(record)
        DECISIONS.labels(record.stage).inc()
        log_event(f"decision.{record.stage}", logging.INFO, decision_id=record.decision_id, sim_tick=record.sim_tick,
                  policy=record.versions.get("policy"), mode=record.mode,
                  twin_error_l=(record.twin_check or {}).get("error_l"))
