"""Alarm engine inside the frame transaction (Task 16; ALM-01/06/08, ING-07, D-ALM-11, G17, G18)."""

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import psycopg
import pytest
from psycopg.rows import dict_row

from gs.alarms.contract import Level, Transition
from gs.alarms.engine import ack, compile_set, initial_state, step
from gs.common.hooks import Hooks
from gs.common.missiondb import load_dir
from gs.ingest.fold import state_version
from gs.ingest.store import write_transitions
from gs.simulator.frames import build_frame
from gs.simulator.nominals import NOMINALS
from tests.support.alarms import Frame, run_engine
from tests.support.env import now, until, vid
from tests.support.ingest import ensure_set, worker
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy
from tests.support.pg import admin_dsn, alarm_events_for, app_dsn, events_for, stored
from tests.support.redisx import dlq_entries, drained, pending, xadd_raw

TEMP = "cabin_temp_c"
MISSION = load_dir(MISSION_DIR)
LIMITS_SHA = hashlib.sha256((MISSION_DIR / "limits.yaml").read_bytes()).hexdigest()


class Crash(BaseException):
    pass


def crash(_: int) -> NoReturn:
    raise Crash


def frames_of(temps: Sequence[float], start_seq: int = 0) -> list[Frame]:
    """(seq, t, values): every parameter at its nominal, cabin_temp_c from `temps`, 100 ms apart."""
    nominal: dict[str, float | None] = {p: nom for p, (nom, _) in NOMINALS.items()}
    return [(start_seq + i, (start_seq + i) / 10, nominal | {TEMP: x}) for i, x in enumerate(temps)]


def put(v: str, base: datetime, frames: Sequence[Frame]) -> None:
    for seq, t, values in frames:
        vals = {p: x for p, x in values.items() if x is not None}
        xadd_raw(v, build_frame(v, seq, base + timedelta(seconds=t), vals))


def produce_values(v: str, base: datetime, temps: Sequence[float], start_seq: int = 0) -> None:
    put(v, base, frames_of(temps, start_seq))


def alarm_row(v: str) -> dict[str, Any]:
    with psycopg.connect(app_dsn("gs"), row_factory=dict_row) as c:
        (row,) = c.execute("select * from alarms where vehicle=%s", (v,)).fetchall()
    return row


def admin(sql: str, *args: object) -> None:
    with psycopg.connect(admin_dsn("gs")) as c:
        c.execute(sql, args)


def kinds(v: str) -> list[str]:
    return [r["kind"] for r in alarm_events_for(v)]


async def drain(v: str, consumer: str = "ingest-test", mission_dir: Path = MISSION_DIR) -> None:
    async with worker([v], consumer=consumer, mission_dir=mission_dir):
        await until(lambda: drained(v), 5)


async def test_red_temp_raises_with_file_hash() -> None:  # ALM-01/06/08 row content
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    async with worker([v]):
        await until(lambda: len(alarm_events_for(v)) == 1, 5)
    (row,) = alarm_events_for(v)
    got = (row["kind"], row["severity"], row["alert"], row["trigger_seq"])
    assert got == ("RAISE", "RED", True, 7)
    assert (row["param"], row["check_kind"], row["level"]) == (TEMP, "limit", "RED_HIGH")
    assert row["trigger_t_onboard"] == base + timedelta(seconds=0.7)
    assert row["limits_sha256"] == LIMITS_SHA and row["set_sha256"] == MISSION.set_sha256
    a = alarm_row(v)
    assert (a["alarm_id"], a["state"], a["severity"], a["trigger_check"]) == (
        row["alarm_id"], "UNACK", "RED", "limit",
    )  # fmt: skip
    assert (a["limits_sha256"], a["set_sha256"]) == (LIMITS_SHA, MISSION.set_sha256)


async def test_redelivery_raises_nothing_twice(tmp_path: Path) -> None:  # D-ING-17, ING-07
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    hooks = Hooks(crash_at="after_commit_before_publish", marker_dir=tmp_path, exit=crash)
    with pytest.raises(Crash):  # committed, never acked
        async with worker([v], hooks=hooks) as w:
            await asyncio.wait_for(w.tasks[0], 5)
    assert stored(v) == 10 and pending(v) == 10 and kinds(v) == ["RAISE"]
    await drain(v)  # the same entries again
    assert kinds(v) == ["RAISE"] and alarm_row(v)["state"] == "UNACK"


async def test_alarm_state_survives_restart() -> None:  # snapshot: no second RAISE, then RTN
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    await drain(v)
    produce_values(v, base, [31.0, 22.0, 22.0, 22.0], start_seq=10)
    await drain(v)
    rows = alarm_events_for(v)
    assert [(r["kind"], r["trigger_seq"], r["severity"]) for r in rows] == [
        ("RAISE", 7, "RED"), ("RTN", 13, "NOMINAL"),
    ]  # fmt: skip
    a = alarm_row(v)  # G17: the RTN keeps the alarm's severity (column is YELLOW | RED)
    assert (a["state"], a["severity"], a["closed_at"]) == ("RTN_UNACK", "RED", None)


async def test_rtn_and_clear_rows_keep_severity() -> None:  # G17 incl. CLEAR (ACK path: day 3)
    v, t0 = vid(), now()
    cset = compile_set(MISSION)
    await ensure_set()
    es, trs = initial_state(cset), list[Transition]()
    for seq, t, values in frames_of([31.0] * 3):
        es, out = step(es, cset, v, t0, seq, t0 + timedelta(seconds=t), 0.1, values, False)
        trs += out
    es, acked = ack(es, v, TEMP, "op", "seen", f"ack/{v}", t0)
    assert acked is not None
    trs.append(acked)  # RAISE, ACK, then the value returns: an ACKED alarm CLEARs
    for seq, t, values in frames_of([22.0] * 3, start_seq=3):
        es, out = step(es, cset, v, t0, seq, t0 + timedelta(seconds=t), 0.1, values, False)
        trs += out
    assert [t.kind for t in trs] == ["RAISE", "ACK", "CLEAR"] and trs[2].severity == Level.NOMINAL
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        async with c.transaction():
            await write_transitions(c, cset.set_sha256, trs)
    a = alarm_row(v)
    assert (a["state"], a["severity"], a["acked_sev"]) == ("CLEARED", "RED", "RED")
    assert a["closed_at"] is not None
    assert [(r["kind"], r["severity"]) for r in alarm_events_for(v)] == [
        ("RAISE", "RED"), ("ACK", "RED"), ("CLEAR", "NOMINAL"),
    ]  # fmt: skip


async def test_takeover_continues_engine_exactly() -> None:  # D-ALM-10/11 end to end
    v, base = vid(), now() - timedelta(minutes=1)
    ramp = [22.0 + i * 0.1 for i in range(110)]
    frames = frames_of(ramp + ramp[::-1])
    split = 51  # w1 stops two frames into the YELLOW persistence ring
    put(v, base, frames[:split])
    await drain(v, "w1")
    put(v, base, frames[split:])
    await drain(v, "w2")
    ref = run_engine(compile_set(MISSION), frames)  # one uninterrupted in-memory run
    want = [(t.kind, t.trigger_seq, t.severity.name, t.level) for t in ref]
    got = [(r["kind"], r["trigger_seq"], r["severity"], r["level"]) for r in alarm_events_for(v)]
    assert len(want) >= 4 and got == want


async def test_version_mismatch_rebuilds_from_alarms() -> None:  # D-ALM-11, A-E1-5
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    await drain(v)
    admin("update engine_state set schema_version = -1 where vehicle=%s", v)
    produce_values(v, base, [31.0] * 5, start_seq=10)
    await drain(v)
    (reset,) = events_for(v, "ENGINE_STATE_RESET")
    assert reset["details"]["found_version"] == -1
    assert kinds(v) == ["RAISE"]  # still red: no RTN, no second alert
    produce_values(v, base, [22.0] * 3, start_seq=15)
    await drain(v)
    assert kinds(v) == ["RAISE", "RTN"]


async def test_late_frame_not_alarmed() -> None:  # LATE: stored, never classified
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 10)
    await drain(v)
    late = [(seq, seq / 10 + 0.05, vals) for seq, _, vals in frames_of([31.0] * 3, start_seq=3)]
    put(v, base, late)
    await drain(v)
    assert stored(v) == 13 and alarm_events_for(v) == []


async def test_snapshot_of_older_set_switches_to_current(tmp_path: Path) -> None:  # G18
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    await drain(v)
    m = mission_copy(tmp_path)
    shipped = next(ln for ln in (m / "limits.yaml").read_text().splitlines() if TEMP in ln)
    edit(m / "limits.yaml", shipped, f"  {TEMP}: {{checks: []}}")  # the alarm's param unchecked
    new = load_dir(m)
    produce_values(v, base, [31.0], start_seq=10)
    async with worker([v], mission_dir=m):  # loaded with the old set, switched at frame 10
        await until(lambda: len(alarm_events_for(v)) == 2, 5)
    raise_, rtn = alarm_events_for(v)
    assert (rtn["kind"], rtn["note"], rtn["trigger_seq"]) == ("RTN", "LIMITS_CHANGED", 10)
    (ev,) = [e for e in events_for(v, "LIMITS_APPLIED") if e["details"]["first_seq"] == 10]
    assert ev["details"]["set_sha256"] == new.set_sha256
    assert (rtn["set_sha256"], rtn["limits_sha256"]) == (new.set_sha256, new.limits_sha256)
    assert raise_["set_sha256"] == MISSION.set_sha256 and events_for(v, "ENGINE_STATE_RESET") == []
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select set_sha256 from engine_state where vehicle=%s", (v,)).fetchone()
    assert row == (new.set_sha256,)


BOGUS = "b" * 64


@pytest.mark.parametrize("corrupt", ["engine", "link", "set"])
async def test_unloadable_snapshot_never_crash_loops(corrupt: str) -> None:  # G18
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5 + [31.0] * 5)
    await drain(v)
    if corrupt in ("engine", "link"):  # current version, a snapshot that doesn't load → rebuild
        bad = "{}" if corrupt == "engine" else '"x"'  # ValueError / TypeError
        admin(f"update engine_state set state = jsonb_set(state, '{{{corrupt}}}', '{bad}')"
              " where vehicle=%s", v)  # fmt: skip
    else:  # a stored set that no longer validates → loaded with the current set
        admin(
            "insert into limits_versions values (%s, 'x', 'x', 'x', 'version: 2', '', '')"
            " on conflict do nothing",
            BOGUS,
        )
        admin("update engine_state set set_sha256 = %s where vehicle=%s", BOGUS, v)
    produce_values(v, base, [31.0] * 5, start_seq=10)
    async with worker([v]) as w:
        await until(lambda: drained(v), 5)
        assert not w.done()
    assert stored(v) == 15 and kinds(v) == ["RAISE"]  # the open alarm carries on, no re-alert
    (reset,) = events_for(v, "ENGINE_STATE_RESET")  # the replay timeline marks every case
    if corrupt == "set":  # the state itself loaded with the current set; only the set moved
        assert (
            BOGUS in reset["details"]["reason"] and MISSION.set_sha256 in reset["details"]["reason"]
        )
    with psycopg.connect(app_dsn("gs")) as c:
        row = c.execute("select set_sha256 from engine_state where vehicle=%s", (v,)).fetchone()
    assert row == (MISSION.set_sha256,)


async def test_reset_while_silent_commits_once() -> None:  # D-ALM-11: no frame after the reset
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 10)
    await drain(v)
    admin("update engine_state set schema_version = -1 where vehicle=%s", v)
    for _ in range(2):  # the second start finds the rewritten snapshot: no second reset
        async with worker([v]):
            await until(lambda: bool(events_for(v, "ENGINE_STATE_RESET")), 5)
            await asyncio.sleep(0.5)
        assert len(events_for(v, "ENGINE_STATE_RESET")) == 1
        with psycopg.connect(app_dsn("gs")) as c:
            row = c.execute("select schema_version from engine_state where vehicle=%s", (v,))
            assert row.fetchone() == (state_version(),)


async def test_stray_open_alarm_without_snapshot_is_kept(tmp_path: Path) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # an open alarm row, no engine_state row
    cset = compile_set(MISSION)
    await ensure_set()
    es, trs = initial_state(cset), list[Transition]()
    for seq, t, values in frames_of([31.0] * 3):
        es, out = step(es, cset, v, base, seq, base + timedelta(seconds=t), 0.1, values, False)
        trs += out
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        await write_transitions(c, cset.set_sha256, trs)
    produce_values(v, base, [31.0] * 5, start_seq=10)
    await drain(v)  # rebuilt from the row: still RED, so no second alarm (alarms_one_open)
    assert stored(v) == 5 and dlq_entries(v) == [] and kinds(v) == ["RAISE"]


async def test_seq_state_without_snapshot_survives_silence() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, base, [22.0] * 5)
    await drain(v)
    admin("delete from engine_state where vehicle=%s", v)  # e.g. a row from before Gate 3a
    async with worker([v]) as w:  # no frame: the silence path writes LOS
        await until(lambda: bool(events_for(v, "LOS")), 5)
        assert not w.done()
    produce_values(v, base, [22.0] * 3, start_seq=5)
    await drain(v)
    applied = [e["details"]["first_seq"] for e in events_for(v, "LIMITS_APPLIED")]
    assert applied == [0, 5]  # the set is applied again at the first frame after
