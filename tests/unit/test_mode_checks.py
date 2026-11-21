"""checks_by_mode, load part (D-EXT-06); the engine part comes with its own task."""

import pytest

from gs.alarms.contract import Level, Transition
from gs.alarms.engine import (
    CompiledSet,
    EngineState,
    active,
    compile_set,
    initial_state,
    step,
    switch_set,
)
from gs.alarms.state import dump, load
from gs.common.missiondb import MissionDbError
from tests.support.alarms import EPOCH, PERIOD_S, V, at
from tests.support.mission import files, ld


def test_absent_mode_has_no_entry_and_empty_list_is_kept() -> None:
    lim = files()["limits"].replace(
        b"  cabin_temp_c: {checks: [", b"  cabin_temp_c: {checks_by_mode: {SAFE: []}, checks: ["
    )
    e = ld(files(limits=lim)).limits["cabin_temp_c"]
    assert tuple(e.checks_by_mode["SAFE"]) == () and "TEST" not in e.checks_by_mode
    assert len(e.checks) == 1


# --- engine part (Task 8) ---------------------------------------------------------------------
T = "cabin_temp_c"
LIM = "{type: limit, red_high: %s, yellow_high: %s, hysteresis_abs: 0.5}"
ROC = "{type: roc, limit_per_s: 0.15, window_s: 1.0, direction: rising, severity: YELLOW}"


def cs(by_mode: str, n: int = 3) -> CompiledSet:
    """cabin_temp_c: default [limit red 30, roc]; `by_mode` is the checks_by_mode body."""
    lim = files()["limits"]
    old = b"defaults: {hysteresis_pct: 2.0, persistence: 3}"
    lim = lim.replace(old, old.replace(b"3}", b"%d}" % n))
    line = next(x for x in lim.splitlines(keepends=True) if x.startswith(b"  cabin_temp_c:"))
    new = f"  cabin_temp_c: {{checks_by_mode: {{{by_mode}}}, checks: [{LIM % (30, 27)}, {ROC}]}}\n"
    return compile_set(ld(files(limits=lim.replace(line, new.encode(), 1))))


SAFE = cs(f"SAFE: [{LIM % (25, 20)}]")


def tslots(s: EngineState) -> set[str]:
    return {k for p, k in s.slots if p == T}


def go(
    s: EngineState, c: CompiledSet, seq: int, v: float, mode: str
) -> tuple[EngineState, tuple[Transition, ...]]:
    return step(s, c, V, EPOCH, seq, at(seq * PERIOD_S), PERIOD_S, {T: v}, False, mode=mode)


def run(
    s: EngineState, c: CompiledSet, start: int, vals: list[float], mode: str
) -> tuple[EngineState, list[Transition]]:
    out: list[Transition] = []
    for i, v in enumerate(vals):
        s, trs = go(s, c, start + i, v, mode)
        out += trs
    return s, out


def test_set_selection_and_fallback() -> None:
    assert active(SAFE, T, "TEST") == SAFE.checks[T]  # absent mode: the default checks
    (only,) = active(SAFE, T, "SAFE")
    assert only[0].kind == "limit" and only[0].cfg.red_high == 25  # type: ignore[attr-defined]
    _, tr = run(initial_state(SAFE), SAFE, 0, [26.0] * 3, "TEST")
    assert not tr  # 26 is nominal under the default
    _, tr = run(initial_state(SAFE), SAFE, 0, [26.0] * 3, "SAFE")
    assert [t.severity for t in tr] == [Level.RED]  # but RED under SAFE


def test_mode_switch_slot_rules() -> None:
    s, trs = run(initial_state(SAFE), SAFE, 0, [31.0] * 3, "TEST")
    (raise_,) = trs
    assert tslots(s) == {"limit", "roc"} and s.mode == "TEST"
    s2, tr = go(s, SAFE, 3, 22.0, "SAFE")  # both: limit keeps RED + its alarm, ring reset
    assert not tr and s2.alarms[T].alarm_id == raise_.alarm_id
    assert s2.slots[(T, "limit")].level == Level.RED and len(s2.slots[(T, "limit")].ring) == 1
    assert tslots(s2) == {"limit"} and s2.mode == "SAFE"  # roc left
    s3, tr = go(s2, SAFE, 4, 22.0, "TEST")  # roc joins from initial()
    assert not tr and s3.slots[(T, "roc")].level == Level.NOMINAL
    assert len(s3.slots[(T, "roc")].state.t) == 1  # type: ignore[attr-defined]  # fresh window
    gone = cs(f"FAULT: [{ROC}]")  # the red limit leaves: forced NOMINAL, MODE_CHANGED
    s, _ = run(initial_state(gone), gone, 0, [31.0] * 3, "TEST")
    _, (rtn,) = go(s, gone, 3, 31.0, "FAULT")
    assert (rtn.kind, rtn.note, rtn.severity, rtn.check_set) == (
        "RTN",
        "MODE_CHANGED",
        Level.NOMINAL,
        "FAULT",
    )


def test_empty_mode_list_is_not_monitored() -> None:  # R23 M3
    c = cs("SAFE: []")
    assert active(c, T, "SAFE") == () and active(c, T, "TEST") == c.checks[T]
    s, _ = run(initial_state(c), c, 0, [31.0] * 3, "TEST")
    s, (rtn,) = go(s, c, 3, 31.0, "SAFE")
    assert (rtn.kind, rtn.note) == ("RTN", "MODE_CHANGED")
    assert not tslots(s) and s.alarms[T].state == "RTN_UNACK"
    s, tr = run(s, c, 4, [31.0] * 5, "SAFE")  # no new alarm while unmonitored
    assert not tr and not tslots(s)


def test_check_set_on_rows_and_text() -> None:
    _, (a,) = run(initial_state(SAFE), SAFE, 0, [31.0] * 3, "TEST")
    assert (a.check_set, a.level) == ("default", "RED_HIGH")
    _, (b,) = run(initial_state(SAFE), SAFE, 0, [26.0] * 3, "SAFE")
    assert (b.check_set, b.level) == ("SAFE", "RED_HIGH · SAFE limits")


def test_mode_change_reload_and_restart_agree() -> None:  # Review Focus 4, unit level
    c = cs(f"SAFE: [{LIM % (25, 20)}]")
    pre, _ = run(initial_state(c), c, 0, [31.0] * 3, "TEST")
    straight, _ = run(pre, c, 3, [26.0] * 5, "SAFE")
    # the same batch, but after the mode change: reload the same set, then restart from the snapshot
    mid, _ = run(pre, c, 3, [26.0] * 3, "SAFE")
    reloaded, _ = switch_set(mid, c, c, V, EPOCH, 6, at(6 * PERIOD_S))
    restarted = load(dump(reloaded), c)
    end, _ = run(restarted, c, 6, [26.0] * 2, "SAFE")

    def view(s: EngineState) -> object:  # the ring is reset by a reload, so it is not compared
        return s.mode, {k: (x.level, x.side) for k, x in s.slots.items()}, s.alarms

    assert straight.mode == "SAFE" and straight.slots[(T, "limit")].level == Level.RED
    assert T in straight.alarms
    assert view(end) == view(straight)


def test_deadband_floor_every_set() -> None:
    with pytest.raises(MissionDbError, match="below floor"):
        cs("SAFE: [{type: limit, red_high: 25, yellow_high: 20, hysteresis_abs: 0.0001}]")
