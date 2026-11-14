import pytest

from gs.ingest.liveness import HOLDOFF_MIN_MS, should_steal, watchdog_ok, watchdog_timeout
from gs.ingest.worker import BACKOFF_MAX_S, BLOCK_MS, SOCKET_TIMEOUT_S


@pytest.mark.parametrize(
    ("idle", "since", "want"),
    [
        (3001, None, True),
        (3000, None, False),
        # G23: the hold-off after our own reconnect is max(STEAL_IDLE_MS, a holder's worst
        # reconnect = (BACKOFF_MAX_S + SOCKET_TIMEOUT_S) s + 2 x BLOCK = 12 s), so CI's 3 s
        # threshold does not shorten it
        (10_000, 500, False),
        (10_000, 3000, False),
        (10_000, HOLDOFF_MIN_MS - 1, False),
        (10_000, HOLDOFF_MIN_MS, True),
    ],
)
def test_should_steal(idle: int, since: int | None, want: bool) -> None:
    assert should_steal(idle, 3000, since) is want


def test_hold_off_follows_a_longer_steal_threshold() -> None:  # default 30 s > 12 s
    assert not should_steal(40_000, 30_000, 29_999)
    assert should_steal(40_000, 30_000, 30_000)


def test_hold_off_floor_is_the_owner_worst_reconnect() -> None:
    assert HOLDOFF_MIN_MS == (BACKOFF_MAX_S + SOCKET_TIMEOUT_S) * 1000 + 2 * BLOCK_MS == 12_000


def test_watchdog_ok_only_when_every_owned_loop_passed() -> None:
    seen = {"NYX-A": 90.0, "NYX-B": 50.0}
    assert watchdog_ok(seen, [], 100.0, 30.0)  # standby only: nothing to hang
    assert watchdog_ok(seen, ["NYX-A"], 100.0, 30.0)
    assert not watchdog_ok(seen, ["NYX-A", "NYX-B"], 100.0, 30.0)  # B passed 50 s ago
    assert not watchdog_ok(seen, ["NYX-C"], 100.0, 30.0)  # owned but never passed
    assert not watchdog_ok({"NYX-A": 70.0}, ["NYX-A"], 100.0, 30.0)  # exactly the limit


def test_watchdog_timeout_is_the_remaining_budget() -> None:  # G27
    assert watchdog_timeout({"NYX-A": 100.0}, ["NYX-A"], 100.0, 30.0) == 30.0  # fresh loop
    assert watchdog_timeout({"NYX-A": 80.0}, ["NYX-A"], 100.0, 30.0) == 10.0  # 20 s old
    seen = {"NYX-A": 95.0, "NYX-B": 75.0}
    assert watchdog_timeout(seen, ["NYX-A", "NYX-B"], 100.0, 30.0) == 5.0  # oldest owned
    assert watchdog_timeout(seen, ["NYX-A"], 100.0, 30.0) == 25.0  # B not owned
    assert watchdog_timeout({}, [], 100.0, 30.0) == 30.0  # standby only
    assert watchdog_timeout({"NYX-A": 70.0}, ["NYX-A"], 100.0, 30.0) == 0.1  # floor
