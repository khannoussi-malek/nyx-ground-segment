"""ING-07: ownership and failover of the ingest workers (D-ING-10..12, D-ING-24)."""

import time

import pytest

from gs.ingest.worker import BLOCK_MS, Timing
from tests.support.compose import compose, recreate, restart_count, wait_healthy
from tests.support.env import now, wait_until
from tests.support.ingest import (
    FAST_ENV,
    LIVE,
    lock_holder_pid,
    restarts,
    terminate_backends,
    wait_drained,
)
from tests.support.pg import events_for, gapless, lock_holder, out_of_order, seqs_since
from tests.support.redisx import client, dlq_since


def test_each_worker_holds_its_home_vehicle() -> None:  # D-ING-10, G1/G5
    wait_until(lambda: [lock_holder(v) for v in LIVE] == ["ingest-1", "ingest-2"], 30)


def test_db_error_exits_without_ack() -> None:  # connection-class error → exit, restart, 0 lost
    t0, before = now(), restarts()
    owner = lock_holder_pid("NYX-TA1")
    assert owner is not None
    pid, svc = owner
    terminate_backends([pid])  # the lock session: 57P01 on its next statement (the fence)
    wait_until(lambda: restarts()[svc] > before[svc], 20)
    wait_healthy(svc, 60)
    wait_drained(LIVE, 30)
    time.sleep(3)
    for v in LIVE:
        seqs = seqs_since(v, t0)
        assert len(seqs) > 30 and gapless(seqs), v
        assert dlq_since(f"tm:{v}", t0) == [], v
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)  # handed back home


KILL_CASES = [
    "before_commit",
    "after_commit_before_publish",
    "after_publish_before_ack",
    "docker_kill",
    "crash_at_self_restart",
]


@pytest.mark.usefixtures("fast_ingest")
@pytest.mark.parametrize("case", KILL_CASES)
def test_kill_owner_mid_stream(case: str) -> None:  # ING-07: 0 lost by seq count
    t0 = now()
    if case == "docker_kill":
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)
        compose("kill", "ingest-1")
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 15)  # the peer, not a restart
    else:
        point = "after_xack" if case == "crash_at_self_restart" else case
        recreate(["ingest-1"], {**FAST_ENV, "CRASH_AT": point}, wait=False)
        # the hook fires once the new ingest-1 owns NYX-TA1 again (after a hand-back), exits 99
        # and comes back through `restart: unless-stopped`; meanwhile ingest-2 holds NYX-TA1
        wait_until(lambda: restart_count("ingest-1") >= 1, 40)
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 15)
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 30)  # hand-back, no 2nd crash
        assert restart_count("ingest-1") == 1  # the marker kept the hook from firing again
    time.sleep(3)
    recreate(["ingest-1"], FAST_ENV)  # restore, no hook
    wait_drained(LIVE, 30)
    seqs = seqs_since("NYX-TA1", t0)
    assert len(seqs) > 50 and gapless(seqs) and dlq_since("tm:NYX-TA1", t0) == []


# Bounds from the design values the fast_ingest workers run with (no magic numbers):
STEAL_S = int(FAST_ENV["STEAL_IDLE_MS"]) / 1000
T = Timing()  # the compose workers run with the default periods
SLACK_S = 5.0  # container restart + Python startup + polling
# G24: the holder must be over STEAL_IDLE_MS on two consecutive ticks; the first such tick comes
# at most one tick after the threshold is crossed
STEAL_BOUND_S = STEAL_S + 2 * T.tick_s + SLACK_S
FENCED_EXIT_S = BLOCK_MS / 1000 + SLACK_S  # a woken owner fails at its next DB statement
# D-ING-26 re-arms while every owned loop passed in the last watchdog_s, so the last re-arm can
# come watchdog_s after the hung loop's last pass and the dump another watchdog_s later
WATCHDOG_BOUND_S = 2 * T.watchdog_s + T.tick_s + SLACK_S
HANDBACK_S = T.start_delay_s + T.handback_s + 2 * SLACK_S  # restart, wait on the lock, hand-back
HANG_ENV = {
    "sigstop_after_xack": {"STOP_AT": "after_xack"},
    "await_forever_in_loop": {"HANG_AT": "in_loop"},
    "stop_after_fence": {"STOP_AT": "after_fence_before_read"},
}
PAUSED = ("sigstop_in_read", "sigcont_after_steal")  # cgroup freeze: SIGSTOP can't pass tini


def hb_missing(v: str) -> bool:
    return not client("admin").exists(f"hb:veh:{v}")


def hang(case: str) -> int:
    """Hang ingest-1 while it owns NYX-TA1; returns the restart count to compare against."""
    if case in PAUSED:
        wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", HANDBACK_S)
        rc = restart_count("ingest-1")
        compose("pause", "ingest-1")
        return rc
    recreate(["ingest-1"], {**FAST_ENV, **HANG_ENV[case]}, wait=False)
    # a recreate hands NYX-TA1 to ingest-2; the new ingest-1 gets it back by hand-back and hangs
    # in its first pass (HANG_AT, stop_after_fence) or first acked batch (after_xack)
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", HANDBACK_S)
    return restart_count("ingest-1")


def wake(case: str) -> None:
    if case in PAUSED:
        compose("unpause", "ingest-1")
    else:
        # not `docker compose kill -s SIGCONT`: Docker counts any `kill` as a manual stop and
        # then ignores `restart: unless-stopped`, so the fenced worker's exit 1 is never restarted
        compose("exec", "-T", "ingest-1", "sh", "-c", "kill -CONT -1")


@pytest.mark.usefixtures("fast_ingest")
@pytest.mark.parametrize(
    "case",
    [
        "sigstop_in_read",
        "sigstop_after_xack",
        "await_forever_in_loop",
        "sigcont_after_steal",
        "stop_after_fence",
    ],
)
def test_hung_owner_is_stolen(case: str) -> None:  # ING-06/08, D-ING-12/26, R14 E-5
    t0 = now()
    rc = hang(case)
    if case == "await_forever_in_loop":  # the heartbeat stops with the loop
        wait_until(lambda: hb_missing("NYX-TA1"), T.hb_ttl_s + SLACK_S)
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", STEAL_BOUND_S)
    if case == "await_forever_in_loop":
        wait_until(lambda: restart_count("ingest-1") > rc, WATCHDOG_BOUND_S)  # watchdog exit
        assert "most recent call first" in compose("logs", "ingest-1").stdout
    else:
        wake(case)
        wait_until(lambda: restart_count("ingest-1") > rc, FENCED_EXIT_S)  # fenced, exits
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", HANDBACK_S)
    recreate(["ingest-1"], FAST_ENV)
    time.sleep(3)
    wait_drained(LIVE, 30)
    seqs = seqs_since("NYX-TA1", t0)
    assert gapless(seqs) and len(seqs) == len(set(seqs)) and len(seqs) > 100
    assert dlq_since("tm:NYX-TA1", t0) == []
    gaps = events_for("NYX-TA1", "GAP", since=t0)
    ooo = out_of_order("NYX-TA1", t0)
    print(f"{case}: out-of-order {ooo}, GAP events {len(gaps)}")
    if case != "stop_after_fence":
        assert gaps == []  # no false GAP
    # stop_after_fence is D-ING-12's residual window (R14 E-5): the woken owner reads once into
    # its own PEL; those entries return through the reclaim, late, possibly out of order and
    # behind a false GAP (R2-06) — never lost or doubled, which the asserts above check
    if case == "sigcont_after_steal":
        assert ooo == 0  # stopped before the fence: 0 out of order
