"""ING-07 alarm half: across a kill or crash of the owner, every alarm transition is written
exactly once, at the frame an uninterrupted run would give it (D-ING-17, D-ALM-11). The kill
always lands in a long red run (alarm open, ring full of RED); short red steps follow it."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import psycopg
import pytest

from tests.support.compose import recreate
from tests.support.env import now, wait_until
from tests.support.ingest import FAST_ENV, KILL_CASES, LIVE, kill_or_crash, wait_drained
from tests.support.pg import alarm_events_for, app_dsn
from tests.support.sim import delete_fault, post_fault

V, PARAM, RED = "NYX-TA1", "cabin_temp_c", 31.0  # 31 °C > red_high 30; nominal 22 ± 0.05
N = 3  # persistence (mission/limits.yaml defaults)
LONG_S = 300.0  # the red run the kill lands in; DELETEd once the takeover is done
STEPS, EVERY_S, STEP_S = 4, 5.0, 1.0  # then four 1 s red steps, 5 s apart, after the takeover


def red_steps() -> None:
    """STEPS short sim `step` faults on cabin_temp_c, each after EVERY_S nominal."""
    for _ in range(STEPS):
        time.sleep(EVERY_S)  # nominal first: the long run (or the previous step) returns
        assert post_fault(V, "step", param=PARAM, value=RED, duration_s=STEP_S) == 204


@contextmanager
def long_red() -> Iterator[None]:
    """One sim `step` fault held red until exit: the takeover meets an open alarm and a ring
    full of RED. Yields once the RAISE is stored."""
    t0 = now()
    assert post_fault(V, "step", param=PARAM, value=RED, duration_s=LONG_S) == 204
    try:
        wait_until(lambda: ("RAISE", PARAM) in kinds(t0), 30)
        yield
        time.sleep(2)  # red frames stamped after the takeover returned
    finally:
        assert delete_fault(V, "step") == 204


def kinds(since: datetime) -> set[tuple[str, str]]:
    return {(r["kind"], r["param"]) for r in alarm_events_for(V, since)}


def red_windows(since: datetime) -> list[list[tuple[int, datetime]]]:
    """Runs of consecutive stored (seq, t_onboard) whose value is exactly the step value."""
    with psycopg.connect(app_dsn("gs")) as c:
        rows = c.execute(
            f"select seq, t_onboard, {PARAM} = %s from telemetry"  # noqa: S608
            " where vehicle=%s and t_onboard >= %s order by t_onboard",
            (RED, V, since),
        ).fetchall()
    out: list[list[tuple[int, datetime]]] = []
    prev = False
    for seq, t, red in rows:
        if red and prev:
            out[-1].append((seq, t))
        elif red:
            out.append([(seq, t)])
        prev = red
    return out


@pytest.mark.usefixtures("fast_ingest")
@pytest.mark.parametrize("case", KILL_CASES)
def test_kill_owner_keeps_alarm_keys(case: str) -> None:
    t0 = now()
    with long_red():
        kill_from = now()
        kill_or_crash(case)
        kill_to = now()
    red_steps()
    time.sleep(STEP_S + 1)  # the last step is over and N nominal frames are in
    recreate(["ingest-1"], FAST_ENV)  # restore, no hook
    wait_drained(LIVE, 30)
    windows = red_windows(t0)
    assert len(windows) == 1 + STEPS and all(len(w) >= N for w in windows), windows
    long = windows[0]
    assert long[0][1] < kill_from and kill_to < long[-1][1]  # the takeover is inside the red run
    rows = [r for r in alarm_events_for(V, t0) if r["param"] == PARAM]
    want = sorted(
        [("RAISE", w[N - 1][0]) for w in windows] + [("RTN", w[-1][0] + N) for w in windows],
        key=lambda x: x[1],
    )  # what one uninterrupted engine gives: N-th red frame, N-th nominal frame after
    assert [(r["kind"], r["trigger_seq"]) for r in rows] == want
    assert all(r["key"].endswith(f"/{r['trigger_seq']}/{r['kind']}") for r in rows)
    assert len({r["alarm_id"] for r in rows}) == 1  # each RAISE re-opens the RTN_UNACK alarm
