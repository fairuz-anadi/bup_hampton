import httpx
import pytest

from app.sim.breaker import CircuitBreaker
from app.sim.client import SimulatorClient
from app.sim.errors import (
    CircuitOpenError,
    InvalidResponseError,
    SimulatorAPIError,
    SimulatorUnavailable,
    parse_error_body,
)

pytestmark = pytest.mark.anyio


def make_client(transport, **kw):
    kw.setdefault("backoff_base", 0)
    return SimulatorClient("http://sim", transport=transport, **kw)


def test_parse_all_three_error_shapes():
    domain = {"detail": {"code": "ROUTE_DISRUPTED", "message": "m"}}
    assert parse_error_body(409, domain) == ("ROUTE_DISRUPTED", "m", False)
    assert parse_error_body(503, {"error": {"code": "FAULT_INJECTED", "message": "m"}}) == ("FAULT_INJECTED", "m", True)
    assert parse_error_body(503, {"detail": {"code": "FAULT_INJECTED"}})[2] is True
    assert parse_error_body(422, {"detail": [{"loc": ["body"]}]})[0] == "VALIDATION_ERROR"
    assert parse_error_body(500, None)[0] == "HTTP_500"


async def test_reads_validate_and_report_stale(fake_sim, transport):
    c = make_client(transport)
    fetched = await c.depots()
    assert {d.id for d in fetched.data} == {"depot-gazipur", "depot-patiya"}
    assert fetched.stale is False
    fake_sim.stale = True
    assert (await c.stations()).stale is True


async def test_transient_503_is_retried(fake_sim, transport):
    fake_sim.fail["/v1/depots"], fake_sim.fail_times["/v1/depots"] = 503, 2
    c = make_client(transport, retries=2)
    assert len((await c.depots()).data) == 2
    assert fake_sim.calls.count(("GET", "/v1/depots")) == 3


async def test_persistent_503_raises_and_opens_breaker(fake_sim, transport):
    fake_sim.fail["/v1/depots"] = 503
    c = make_client(transport, retries=1, breaker=CircuitBreaker(failure_threshold=2))
    for _ in range(2):
        with pytest.raises(SimulatorUnavailable) as exc:
            await c.depots()
        assert exc.value.injected
    with pytest.raises(CircuitOpenError):
        await c.stations()
    # health bypasses the breaker: it is how we tell "faulted" from "down"
    assert (await c.health())["status"] == "ok"


async def test_domain_409_is_not_retried_and_keeps_breaker_closed(fake_sim, transport):
    c = make_client(transport, retries=3, breaker=CircuitBreaker(failure_threshold=1))
    body = dict(source_depot_id="depot-gazipur", destination_station_id="station-mirpur",
                route_id="route-gazipur-mirpur", fuel_type="DIESEL")
    await c.create_allocation(idempotency_key="k1", quantity=100, **body)
    with pytest.raises(SimulatorAPIError) as exc:
        await c.create_allocation(idempotency_key="k1", quantity=200, **body)
    assert exc.value.code == "IDEMPOTENCY_KEY_MISMATCH"
    assert fake_sim.calls.count(("POST", "/v1/allocations")) == 2
    assert c.breaker.state == "CLOSED"


async def test_invalid_body_is_rejected(fake_sim, transport):
    fake_sim.world["depots"][0]["status"] = "ON_FIRE"
    c = make_client(transport)
    with pytest.raises(InvalidResponseError):
        await c.depots()


async def test_timeouts_become_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("slow", request=request)
    c = make_client(httpx.MockTransport(boom), retries=1)
    with pytest.raises(SimulatorUnavailable):
        await c.instance()


async def test_admin_only_allows_admin_paths(transport):
    c = make_client(transport)
    with pytest.raises(ValueError):
        await c.admin("POST", "/v1/allocations")
