from app.sim.breaker import CircuitBreaker


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_opens_after_threshold_within_window():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, window_seconds=10, cooldown_seconds=5, clock=clock)
    for _ in range(2):
        b.record_failure()
    assert b.state == "CLOSED"
    b.record_failure()
    assert b.state == "OPEN"
    assert not b.allow()


def test_failures_outside_window_do_not_count():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, window_seconds=10, cooldown_seconds=5, clock=clock)
    b.record_failure()
    b.record_failure()
    clock.t = 11
    b.record_failure()
    assert b.state == "CLOSED"


def test_half_open_allows_one_probe_then_closes_on_success():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=1, window_seconds=10, cooldown_seconds=5, clock=clock)
    b.record_failure()
    clock.t = 5
    assert b.state == "HALF_OPEN"
    assert b.allow()
    assert not b.allow()  # only one probe in flight
    b.record_success()
    assert b.state == "CLOSED"
    assert b.allow()


def test_failed_probe_reopens():
    clock = Clock()
    b = CircuitBreaker(failure_threshold=1, window_seconds=10, cooldown_seconds=5, clock=clock)
    b.record_failure()
    clock.t = 5
    assert b.allow()
    b.record_failure()
    assert b.state == "OPEN"
    clock.t = 9
    assert b.state == "OPEN"
