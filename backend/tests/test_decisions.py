import json
from pathlib import Path

import pytest

from app.contracts import AllocationLeg, Candidate, FuelType, Recommendation, TwinFuture
from app.db.repo import DecisionRepo
from app.decisions.service import DecisionError, DecisionService
from app.sim.allocations import AllocationWriter
from app.sim.client import SimulatorClient
from app.state.store import StateStore

pytestmark = pytest.mark.anyio

LEG = AllocationLeg(route_id="route-gazipur-mirpur", source_depot_id="depot-gazipur", station_id="station-mirpur",
                    fuel_type=FuelType.PETROL, quantity=3000)


def recommendation(rid="rec-1", tick=0, stale=False, predicted=200.0):
    return Recommendation(
        id=rid, tick=tick, created_at="2026-01-01T00:00:00Z", mode="prevention",
        candidates=[Candidate(id="noop", policy="noop", legs=[]), Candidate(id="lp-v2", policy="lp-v2", legs=[LEG])],
        selected_candidate_id="lp-v2",
        futures=[TwinFuture(candidate_id="lp-v2", label="Send 3,000 L", horizon_ticks=4,
                            network_unmet_liters=predicted, unmet_by_station={}, first_stockout_tick=None,
                            service_level=0.99)],
        risks=[], signals=[], confidence=0.74, versions={"policy": "lp-v2"}, built_on_stale_data=stale)


@pytest.fixture
async def svc(transport):
    client = SimulatorClient("http://sim", transport=transport, backoff_base=0, retries=0)
    store = StateStore(client)
    await store.refresh()
    return DecisionService(DecisionRepo(None), AllocationWriter(client, store), store, "test-sha")


async def test_create_approve_submits_selected_legs(svc, fake_sim):
    rec = await svc.create(recommendation())
    assert rec.stage == "projected" and rec.versions["deployment"] == "test-sha"
    done = await svc.approve("rec-1", by="anadi", reason="looks right")
    assert done.stage == "submitted"
    assert done.approval["candidate_id"] == "lp-v2" and not done.approval["modified"]
    assert [s.result for s in done.submissions] == ["accepted"]
    assert "fg-rec-1-0" in fake_sim.posted
    with pytest.raises(DecisionError) as exc:
        await svc.approve("rec-1", by="anadi", reason=None)
    assert exc.value.code == "NOT_REVIEWABLE"


async def test_modify_replaces_legs(svc, fake_sim):
    await svc.create(recommendation())
    modified = LEG.model_copy(update={"quantity": 2000})
    done = await svc.approve("rec-1", by="anadi", reason="less", legs=[modified])
    assert done.approval["modified"] is True
    assert fake_sim.posted["fg-rec-1-0"]["body"]["quantity"] == 2000


async def test_reject_and_stale_guard(svc, fake_sim):
    await svc.create(recommendation("rec-a"))
    rejected = await svc.reject("rec-a", by="anadi", reason="Tongi needs it more")
    assert rejected.stage == "rejected" and rejected.approval["reason"] == "Tongi needs it more"
    await svc.create(recommendation("rec-b", stale=True))
    with pytest.raises(DecisionError) as exc:
        await svc.approve("rec-b", by="anadi", reason=None)
    assert exc.value.code == "STALE_RECOMMENDATION"
    assert fake_sim.posted == {}


async def test_outcome_check_scores_the_twin(svc, fake_sim):
    await svc.create(recommendation(predicted=200.0))
    await svc.approve("rec-1", by="anadi", reason=None)
    fake_sim.world["instance"]["tick"] = 5
    fake_sim.world["demand_history"] = [
        {"id": i, "station_id": "station-mirpur", "fuel_type": "PETROL", "tick": t,
         "sim_time": "2026-01-01T00:00:00", "demand_liters": 100, "served_liters": 40, "unmet_liters": 60}
        for i, t in enumerate(range(5, 0, -1))]
    await svc.store.refresh()
    assert await svc.check_outcomes() == 1
    rec = svc.repo.get("rec-1")
    assert rec.stage == "verified"
    # ticks 1..4 are inside the 4-tick horizon: 4 x 60 L
    assert rec.twin_check == {"predicted_l": 200.0, "actual_l": 240.0, "error_l": 40.0}


async def test_repo_buffers_when_database_is_down(tmp_path: Path):
    repo = DecisionRepo("postgresql://nobody@127.0.0.1:1/none", tmp_path / "buf.jsonl")
    rec_model = recommendation()
    from app.contracts import DecisionRecord
    record = DecisionRecord(decision_id="d1", sim_tick=0, created_at="2026-01-01T00:00:00Z", stage="projected",
                            recommendation=rec_model)
    await repo.save(record)  # no pool yet -> buffered, never raises
    assert repo.get("d1") is not None
    assert repo.health().status == "down"
    lines = (tmp_path / "buf.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["kind"] == "decision"
    # a restarted backend recovers the buffer from the file
    again = DecisionRepo("postgresql://nobody@127.0.0.1:1/none", tmp_path / "buf.jsonl")
    again._load_buffer_file()
    assert again.get("d1") is not None and len(again._pending) == 1
