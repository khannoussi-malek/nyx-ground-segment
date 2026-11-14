import pytest

from gs.ingest.liveness import should_steal, watchdog_ok


@pytest.mark.parametrize(
    ("idle", "since", "want"),
    [
        (3001, None, True),
        (3000, None, False),
        (10_000, 500, False),  # R2-23: idle kept counting through our own Redis outage
        (10_000, 2999, False),
        (10_000, 3000, True),
    ],
)
def test_should_steal(idle: int, since: int | None, want: bool) -> None:
    assert should_steal(idle, 3000, since) is want


def test_watchdog_ok_only_when_every_owned_loop_passed() -> None:
    seen = {"NYX-A": 90.0, "NYX-B": 50.0}
    assert watchdog_ok(seen, [], 100.0, 30.0)  # standby only: nothing to hang
    assert watchdog_ok(seen, ["NYX-A"], 100.0, 30.0)
    assert not watchdog_ok(seen, ["NYX-A", "NYX-B"], 100.0, 30.0)  # B passed 50 s ago
    assert not watchdog_ok(seen, ["NYX-C"], 100.0, 30.0)  # owned but never passed
    assert not watchdog_ok({"NYX-A": 70.0}, ["NYX-A"], 100.0, 30.0)  # exactly the limit
