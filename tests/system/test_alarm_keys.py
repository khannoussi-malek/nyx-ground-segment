"""ING-07 alarm half: across a kill or crash of the owner, every alarm transition is written
exactly once, at the frame an uninterrupted run would give it (D-ING-17, D-ALM-11)."""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import psycopg
import pytest

from tests.support.compose import recreate
from tests.support.env import now
from tests.support.ingest import FAST_ENV, KILL_CASES, LIVE, kill_or_crash, wait_drained
from tests.support.pg import alarm_events_for, app_dsn
from tests.support.sim import post_fault

V, PARAM, RED = "NYX-TA1", "cabin_temp_c", 31.0  # 31 °C > red_high 30; nominal 22 ± 0.05
N = 3  # persistence (mission/limits.yaml defaults)
STEPS, EVERY_S, STEP_S = 4, 5.0, 1.0  # four 1 s red steps, 5 s apart: they span the kill


@contextmanager
def red_steps() -> Iterator[None]:
    """Sim `step` faults on cabin_temp_c in the background; joined on exit."""

    def run() -> None:
        for _ in range(STEPS):
            assert post_fault(V, "step", param=PARAM, value=RED, duration_s=STEP_S) == 204
            time.sleep(EVERY_S)

    t = threading.Thread(target=run)
    t.start()
    try:
        yield
    finally:
        t.join()


def red_windows(since: datetime) -> list[list[int]]:
    """Runs of consecutive stored seqs whose value is exactly the step value, in seq order."""
    with psycopg.connect(app_dsn("gs")) as c:
        rows = c.execute(
            f"select seq, {PARAM} = %s from telemetry where vehicle=%s and t_onboard >= %s"  # noqa: S608
            " order by t_onboard",
            (RED, V, since),
        ).fetchall()
    out: list[list[int]] = []
    prev = False
    for seq, red in rows:
        if red and prev:
            out[-1].append(seq)
        elif red:
            out.append([seq])
        prev = red
    return out


@pytest.mark.usefixtures("fast_ingest")
@pytest.mark.parametrize("case", KILL_CASES)
def test_kill_owner_keeps_alarm_keys(case: str) -> None:
    t0 = now()
    with red_steps():
        kill_or_crash(case)
    time.sleep(STEP_S + 1)  # the last step is over and N nominal frames are in
    recreate(["ingest-1"], FAST_ENV)  # restore, no hook
    wait_drained(LIVE, 30)
    windows = red_windows(t0)
    assert len(windows) == STEPS and all(len(w) >= N for w in windows), windows
    rows = [r for r in alarm_events_for(V, t0) if r["param"] == PARAM]
    want = sorted(
        [("RAISE", w[N - 1]) for w in windows] + [("RTN", w[-1] + N) for w in windows],
        key=lambda x: x[1],
    )  # what one uninterrupted engine gives: N-th red frame, N-th nominal frame after
    assert [(r["kind"], r["trigger_seq"]) for r in rows] == want
    assert all(r["key"].endswith(f"/{r['trigger_seq']}/{r['kind']}") for r in rows)
    assert len({r["alarm_id"] for r in rows}) == 1  # each RAISE re-opens the RTN_UNACK alarm
