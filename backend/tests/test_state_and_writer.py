import pytest

from app.contracts import AllocationLeg, FuelType
from app.sim.allocations import AllocationWriter, precheck, split_leg
from app.sim.breaker import CircuitBreaker
from app.sim.client import SimulatorClient
from app.state.store import StateStore

pytestmark = pytest.mark.anyio


def leg(route, depot, station, fuel, qty):
    return AllocationLeg(route_id=route, source_depot_id=depot, station_id=station, fuel_type=FuelType(fuel),
                         quantity=qty)


MIRPUR = ("route-gazipur-mirpur", "depot-gazipur", "station-mirpur")


@pytest.fixture
def store(transport):
    client = SimulatorClient("http://sim", transport=transport, backoff_base=0, retries=0,
                             breaker=CircuitBreaker(failure_threshold=3))
    return StateStore(client)


async def test_snapshot_builds_and_keeps_last_good_on_failure(fake_sim, store):
    snap = await store.refresh()
    assert snap.tick == 0 and not snap.freshness.stale
    fake_sim.fail["/v1/stations"] = 503
    snap = await store.refresh()
    assert len(snap.stations) == 4  # last good copy kept
    assert snap.freshness.stale
    assert snap.freshness.resources["stations"].stale
    assert not snap.freshness.resources["depots"].stale


async def test_in_transit_ledger_and_dispatch(fake_sim, store):
    base = {"source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur",
            "route_id": "route-gazipur-mirpur", "fuel_type": "PETROL", "created_tick": 0}
    fake_sim.world["allocations"] = [
        {**base, "id": 1, "idempotency_key": "a", "quantity": 3000, "status": "PENDING"},
        {**base, "id": 2, "idempotency_key": "b", "quantity": 999, "status": "CANCELLED"},
    ]
    snap = await store.refresh()
    assert [leg.allocation_id for leg in snap.in_transit] == [1]
    assert snap.in_transit_totals["station-mirpur"][FuelType.PETROL] == 3000
    assert snap.dispatched_this_tick == {"depot-gazipur": 3000}


def test_split_leg():
    parts = split_leg(leg(*MIRPUR, "DIESEL", 16000), 7000)
    assert [p.quantity for p in parts] == [7000, 7000, 2000]


async def test_precheck_counts_in_transit_and_batch(fake_sim, store):
    snap = await store.refresh()
    # Mirpur petrol: 9000 of 14000. 3000 + 3000 fits by itself, a further 3000 would overflow.
    codes = precheck(snap, [leg(*MIRPUR, "PETROL", 3000), leg(*MIRPUR, "PETROL", 2000),
                            leg(*MIRPUR, "PETROL", 3000)])
    assert codes == [None, None, "PRECHECK_TANK_HEADROOM"]


async def test_precheck_dispatch_route_and_scheduled_disruption(fake_sim, store):
    fake_sim.world["events"] = [{"id": 1, "type": "route_disruption", "start_tick": 1, "end_tick": 5,
                                 "status": "SCHEDULED", "parameters": {"route_ids": ["route-patiya-mirpur"]}}]
    snap = await store.refresh()
    codes = precheck(snap, [
        leg("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 6500),
        leg("route-gazipur-karnaphuli", "depot-gazipur", "station-karnaphuli", "PETROL", 5000),
        leg("route-gazipur-karnaphuli", "depot-gazipur", "station-karnaphuli", "OCTANE", 1000),  # > 12000 dispatch
        leg("route-patiya-mirpur", "depot-patiya", "station-mirpur", "PETROL", 500),             # disruption next tick
        leg("route-gazipur-mirpur", "depot-patiya", "station-mirpur", "PETROL", 500),            # wrong depot
    ])
    assert codes == [None, None, "PRECHECK_DISPATCH_CAPACITY", "PRECHECK_ROUTE_DISRUPTED",
                     "PRECHECK_ROUTE_MISMATCH"]


async def test_writer_posts_splits_and_replays_idempotently(fake_sim, store):
    writer = AllocationWriter(store.client, store)
    r1 = await writer.submit("d1", [leg("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 7000)])
    assert [(s.leg.quantity, s.result, s.idempotency_key) for s in r1.submissions] == [
        (6500, "accepted", "fg-d1-0"), (500, "accepted", "fg-d1-1")]
    r2 = await writer.submit("d1", [leg("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 7000)])
    assert [s.result for s in r2.submissions] == ["accepted", "accepted"]
    assert [s.sim_allocation_id for s in r2.submissions] == [s.sim_allocation_id for s in r1.submissions]
    assert len(fake_sim.posted) == 2


async def test_writer_holds_when_simulator_is_down(fake_sim, store):
    await store.refresh()
    for path in ("/v1/instance", "/v1/depots", "/v1/stations", "/v1/allocations"):
        fake_sim.fail[path] = 503
    await store.refresh()  # 3+ failures open the breaker
    writer = AllocationWriter(store.client, store)
    r = await writer.submit("d2", [leg(*MIRPUR, "DIESEL", 100)])
    assert r.submissions[0].result == "held"
    assert fake_sim.posted == {}
