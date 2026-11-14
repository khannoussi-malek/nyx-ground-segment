import json
import os
import random
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, fields, make_dataclass, replace
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

import pytest

import gs.alarms.engine as engine_mod
import gs.alarms.state as state_mod
from gs.alarms.checks import CHECKS, CheckConfig, Reading, Sample
from gs.alarms.checks.roc import RocState
from gs.alarms.contract import AlarmState, Level, OpenAlarm, Transition
from gs.alarms.engine import (
    LIMITS_CHANGED,
    CompiledSet,
    EngineState,
    Slot,
    ack,
    compile_set,
    initial_state,
    step,
    switch_set,
)
from gs.alarms.state import dump, load, rebuild, shape_version
from gs.common.missiondb import load_dir
from gs.simulator.frames import NOMINALS
from tests.support.alarms import EPOCH, PERIOD_S, Frame, V, at, feed, leak_frames
from tests.support.mission import MISSION_DIR, files, ld

CSET = compile_set(load_dir(MISSION_DIR))
TEMP, PRESS = "cabin_temp_c", "cabin_pressure_kpa"
T = at(1000.0)
ID = UUID(int=7)
Ack = tuple[str, str]  # (param, ack key)
Event = Frame | Ack


def mixed_events(seed: int) -> tuple[list[Event], set[int]]:
    """Leak ramps, recoveries, spikes, NULLs, resets and ACKs at random frames."""
    rng = random.Random(seed)
    events: list[Event] = []
    resets: set[int] = set()
    temp, mode, p = 22.0, "flat", 101.3
    for i in range(400):
        if i % rng.randint(8, 40) == 0:
            temp, mode = rng.choice([22.0, 28.0, 31.0]), rng.choice(["flat", "leak", "rise"])
        p += {"flat": 0.0, "leak": -0.06, "rise": 0.06}[mode]
        p = min(max(p, 92.0), 106.5)
        t_val = temp + rng.gauss(0, 0.05) + (9.0 if rng.random() < 0.03 else 0.0)
        values: dict[str, float | None] = {
            TEMP: None if rng.random() < 0.05 else t_val,
            PRESS: None if rng.random() < 0.03 else p + rng.gauss(0, 0.02),
        }
        events.append((i, i * PERIOD_S, values))
        if rng.random() < 0.02:
            resets.add(i)
        if rng.random() < 0.05:
            events.append((rng.choice([TEMP, PRESS]), f"ack/{i}"))
    return events, resets


def apply(
    state: EngineState, events: list[Event], resets: set[int]
) -> tuple[EngineState, list[Transition]]:
    out: list[Transition] = []
    for e in events:
        if len(e) == 2:
            state, tr = ack(state, V, e[0], "fc1", "seen", e[1], T)
            out += [tr] if tr else []
        else:
            state, trs = feed(state, CSET, [e], resets)
            out += trs
    return state, out


def batches(events: list[Event], rng: random.Random) -> list[list[Event]]:
    out, i = [], 0
    while i < len(events):
        n = rng.randint(1, 40)
        out.append(events[i : i + n])
        i += n
    return out


@pytest.mark.parametrize("seed", range(50))
def test_snapshot_roundtrip_equals_uninterrupted(seed: int) -> None:  # D-ALM-10 property
    events, resets = mixed_events(seed)
    straight_state, straight = apply(initial_state(CSET), events, resets)
    assert straight  # the scenario does raise alarms
    state, got = initial_state(CSET), []
    for batch in batches(events, random.Random(seed)):
        state = load(json.loads(json.dumps(dump(state))), CSET)  # every batch boundary
        state, tr = apply(state, batch, resets)
        got += tr
    assert got == straight
    assert dump(state) == dump(straight_state)


def test_roundtrip_is_exact() -> None:  # tuples, aware datetimes, UUIDs, enums come back as such
    state, trs = feed(initial_state(CSET), CSET, leak_frames(seconds=3, seed=1))
    assert trs and state.alarms
    back = load(json.loads(json.dumps(dump(state))), CSET)
    assert back == state
    roc = back.slots[(PRESS, "roc")]
    assert isinstance(roc.state, RocState) and isinstance(roc.state.t, tuple) and roc.state.t
    assert back.alarms[PRESS].opened_at.tzinfo is not None
    assert isinstance(back.alarms[PRESS].severity, Level)


def test_dump_is_json_native_and_sorted() -> None:
    d = dump(initial_state(CSET))
    assert json.loads(json.dumps(d)) == d
    keys = [(s["param"], s["kind"]) for s in d["slots"]]
    assert keys == sorted(keys)


def valid_dump() -> dict[str, Any]:
    state, _ = feed(initial_state(CSET), CSET, leak_frames(seconds=2, seed=1))
    d = dump(state)
    assert d["alarms"]
    return d


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        load({"slots": [{"kind": "nope"}], "alarms": []}, CSET)
    d = valid_dump()
    d["slots"][0]["state"]["kind"] = "nope"
    with pytest.raises(ValueError, match="unknown check kind 'nope'"):
        load(d, CSET)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda d: d.pop("alarms"),
        lambda d: d["slots"].pop(),  # no longer matches the set
        lambda d: d["slots"][0].pop("ring"),
        lambda d: d["slots"][0]["state"].update(extra=1),
        lambda d: d["slots"][0].update(kind="roc" if d["slots"][0]["kind"] == "limit" else "limit"),
        lambda d: d["alarms"][0].update(state="CLEARED"),
        lambda d: d["alarms"][0].update(opened_at=5),
        lambda d: d["slots"][0].update(level=9),
        lambda d: d.update(slots=None),
    ],
)
def test_bad_shape_rejected(corrupt: Callable[[dict[str, Any]], object]) -> None:
    d = valid_dump()
    corrupt(d)
    with pytest.raises(ValueError):
        load(d, CSET)


def test_roc_window_must_be_a_list() -> None:
    d = valid_dump()
    roc = next(s for s in d["slots"] if s["kind"] == "roc")
    roc["state"]["t"] = 5
    with pytest.raises(ValueError, match="expected a list"):
        load(d, CSET)


def test_shape_version_tracks_state_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass(frozen=True)
    class A:
        x: int

    @dataclass(frozen=True)
    class B:
        x: int
        y: int

    assert shape_version(A) != shape_version(B) and shape_version(A) == shape_version(A)
    assert 0 <= shape_version() < 2**31 and shape_version() != shape_version(A)

    @dataclass(frozen=True)
    class NewState:
        n: int

    class NewCheck:
        kind: ClassVar[str] = "zz"
        Config: ClassVar[type[CheckConfig]] = CheckConfig
        State: ClassVar[type] = NewState

        def __init__(self, cfg: CheckConfig) -> None: ...
        def initial(self) -> object:
            return NewState(0)

        def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
            return state, None

    before = shape_version()
    monkeypatch.setitem(CHECKS, "zz", NewCheck)
    assert shape_version() != before  # every registered check State counts


def nominal_values(**over: float) -> dict[str, float | None]:
    return {**{p: nom for p, (nom, _) in NOMINALS.items()}, **over}


def open_alarm(state: AlarmState = "UNACK", acked: Level | None = None) -> OpenAlarm:
    return OpenAlarm(ID, TEMP, state, Level.RED, acked, "limit", T)


def temp_frames(value: float, start: int, n: int = 3) -> list[Frame]:
    return [(s, s * PERIOD_S, nominal_values(**{TEMP: value})) for s in range(start, start + n)]


def test_rebuild_seeds_trigger_check() -> None:  # ruling G2 item 6
    st = rebuild([open_alarm()], CSET)
    st, tr = step(st, CSET, V, EPOCH, 10, T, 0.1, nominal_values(**{TEMP: 31.0}), False)
    assert tr == ()  # still red: no RTN, no second alert
    st, trs = feed(st, CSET, temp_frames(22.0, 11))
    assert [(t.kind, t.alarm_id) for t in trs] == [("RTN", ID)]


def test_rebuild_keeps_rows() -> None:
    st = rebuild([open_alarm("ACKED", Level.RED)], CSET)
    a = st.alarms[TEMP]
    assert (a.alarm_id, a.state, a.severity, a.acked_sev, a.opened_at) == (
        ID, "ACKED", Level.RED, Level.RED, T
    )  # fmt: skip
    assert st.slots[(TEMP, "limit")].level == Level.RED
    assert all(s.level == Level.NOMINAL for k, s in st.slots.items() if k != (TEMP, "limit"))
    _, trs = feed(st, CSET, temp_frames(22.0, 0))
    assert [t.kind for t in trs] == ["CLEAR"]


def test_rebuild_does_not_seed_rtn_unack() -> None:  # it is at NOMINAL: seeding would re-RAISE
    st = rebuild([open_alarm("RTN_UNACK")], CSET)
    assert st.slots[(TEMP, "limit")].level == Level.NOMINAL
    assert feed(st, CSET, temp_frames(22.0, 0))[1] == []


# ---- set switch (D-ALM-12 / D-EXT-06 slot rule)
def edited_set(drop: Callable[[bytes], bool]) -> CompiledSet:
    lines = files()["limits"].splitlines(keepends=True)
    kept = [ln for ln in lines if not drop(ln)]
    assert len(kept) < len(lines)
    return compile_set(ld(files(limits=b"".join(kept))))


NO_ROC = edited_set(lambda ln: b"type: roc" in ln)
NO_PRESSURE_LIMIT = edited_set(lambda ln: b"red_low: 95.0, yellow_low: 98.0" in ln)
NO_TEMP = edited_set(lambda ln: ln.startswith(b"  cabin_temp_c:"))
SWITCH_SEQ = 9999


def switch(state: EngineState, old: CompiledSet, new: CompiledSet) -> tuple[EngineState, Any]:
    return switch_set(state, old, new, V, EPOCH, SWITCH_SEQ, T)


def leaked(seconds: float) -> EngineState:
    state, trs = feed(initial_state(CSET), CSET, leak_frames(seconds=seconds, seed=1))
    assert trs
    return state


def test_switch_keeps_shared_slot_and_alarm() -> None:
    new = edited_set(lambda ln: ln.startswith(b"  rate_z_dps"))  # pressure checks unchanged
    old_state = leaked(2)  # ROC YELLOW raised
    state, trs = switch(old_state, CSET, new)
    assert trs == () and state.alarms == old_state.alarms
    for kind in ("limit", "roc"):
        before, after = old_state.slots[(PRESS, kind)], state.slots[(PRESS, kind)]
        assert after == replace(before, ring=())  # level, side, state (ROC window) kept
    assert old_state.slots[(PRESS, "roc")].ring and (("rate_z_dps", "limit") not in state.slots)


def test_switch_removed_check_returns_to_normal() -> None:  # roc removed from cabin pressure
    state, trs = switch(leaked(2), CSET, NO_ROC)
    (t,) = trs
    assert (t.kind, t.alarm_state, t.note, t.trigger_seq, t.trigger_t) == (
        "RTN", "RTN_UNACK", LIMITS_CHANGED, SWITCH_SEQ, T
    )  # fmt: skip
    assert (PRESS, "roc") not in state.slots


def test_switch_removed_check_deescalates() -> None:  # RED limit + YELLOW roc; limit removed
    old = leaked(17)
    assert old.alarms[PRESS].severity == Level.RED
    _, (t,) = switch(old, CSET, NO_PRESSURE_LIMIT)
    assert (t.kind, t.severity, t.check_kind, t.note) == (
        "DEESCALATE", Level.YELLOW, "roc", LIMITS_CHANGED
    )  # fmt: skip


def test_switch_removed_param_clears_acked_alarm() -> None:
    state, _ = feed(initial_state(CSET), CSET, temp_frames(31.0, 0))
    state, _ = ack(state, V, TEMP, "fc1", "seen", "k", T)
    state, (t,) = switch(state, CSET, NO_TEMP)
    assert (t.kind, t.alarm_state, t.closed_at, t.note) == ("CLEAR", "CLEARED", T, LIMITS_CHANGED)
    assert TEMP not in state.alarms and not any(p == TEMP for p, _ in state.slots)


def test_switch_added_check_starts_initial() -> None:
    old_state, _ = feed(initial_state(NO_ROC), NO_ROC, leak_frames(seconds=2, seed=1))
    state, trs = switch(old_state, NO_ROC, CSET)
    assert trs == ()
    roc = state.slots[(PRESS, "roc")]
    assert (roc.level, roc.ring, roc.state) == (Level.NOMINAL, (), RocState((), ()))


@dataclass(frozen=True)
class TimedState:
    since: datetime
    marks: tuple[datetime, ...]
    count: int


class Timed:  # plug-in kind whose State holds aware datetimes
    kind: ClassVar[str] = "timed"
    Config: ClassVar[type[CheckConfig]] = CheckConfig
    State: ClassVar[type] = TimedState

    def __init__(self, cfg: CheckConfig) -> None: ...
    def initial(self) -> object:
        return TimedState(EPOCH, (), 0)

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        return state, None


def test_check_state_datetimes_roundtrip_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CHECKS, "timed", Timed)
    chk = Timed(CheckConfig(type="timed"))
    cset = CompiledSet("s", "l", {"p": ((chk, 1),)})
    st = TimedState(at(0.123456), (at(1.5), at(2.000001)), 3)
    state = EngineState({("p", "timed"): Slot("timed", Level.YELLOW, "HIGH", (Level.RED,), st)}, {})
    back = load(json.loads(json.dumps(dump(state))), cset)
    assert back == state
    since = back.slots[("p", "timed")].state
    assert isinstance(since, TimedState) and since.since.tzinfo is not None
    bad = dump(state)
    bad["slots"][0]["state"]["since"] = 5
    with pytest.raises(ValueError, match="expected an ISO time"):
        load(bad, cset)


# ---- Task 5 fix round 1
def test_shape_version_sees_types_and_names() -> None:
    @dataclass(frozen=True)
    class A:
        x: int

    @dataclass(frozen=True)
    class AStr:
        x: str

    @dataclass(frozen=True)
    class A2:
        x: int

    assert shape_version(A) != shape_version(AStr)
    int_a, str_a = make_dataclass("A", [("x", int)]), make_dataclass("A", [("x", str)])
    assert shape_version(int_a) != shape_version(str_a)  # same name and field: the type counts
    assert shape_version(A) != shape_version(A2)  # same fields, other class: qualname counts


@pytest.mark.parametrize("cls", [engine_mod.Slot, engine_mod.Alarm])
def test_shape_version_tracks_slot_and_alarm(cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    before = shape_version()
    grown = make_dataclass(cls.__name__, [(f.name, f.type) for f in fields(cls)] + [("x", int)])
    monkeypatch.setattr(state_mod, cls.__name__, grown)
    assert shape_version() != before


def test_shape_version_is_stable_across_processes() -> None:  # crc32, not hash()
    code = "from gs.alarms.state import shape_version; print(shape_version())"
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert outs == {f"{shape_version()}\n"}


def test_shape_version_counts_fieldless_check_states(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass(frozen=True)
    class Empty:
        pass

    class Fieldless(Timed):
        kind: ClassVar[str] = "fieldless"
        State: ClassVar[type] = Empty

    before = shape_version()
    monkeypatch.setitem(CHECKS, "fieldless", Fieldless)
    assert shape_version() != before


def test_every_registered_check_state_round_trips() -> (
    None
):  # a plug-in fails the build, not ingest
    bad = [
        (k, f.name, f.type)
        for k, c in CHECKS.items()
        for f in fields(c.State)
        if not state_mod.round_trips(f.type)
    ]
    assert bad == []
    assert not state_mod.round_trips(dict[str, int]) and not state_mod.round_trips(Level)
    assert state_mod.round_trips(tuple[datetime, ...]) and state_mod.round_trips(float)
    assert not state_mod.round_trips(tuple[int, int]) and not state_mod.round_trips(
        tuple[Level, ...]
    )


def test_rebuild_seeds_yellow_at_yellow() -> None:  # G2 item 6 with a non-RED alarm
    yellow = OpenAlarm(ID, TEMP, "UNACK", Level.YELLOW, None, "limit", T)
    st = rebuild([yellow], CSET)
    assert st.slots[(TEMP, "limit")].level == Level.YELLOW
    _, trs = feed(st, CSET, temp_frames(28.0, 0))
    assert trs == []  # still yellow: no ESCALATE, no RTN


def test_rebuild_seeds_only_the_trigger_check() -> None:
    a = OpenAlarm(ID, PRESS, "UNACK", Level.RED, None, "limit", T)
    st = rebuild([a], CSET)
    assert st.slots[(PRESS, "limit")].level == Level.RED
    assert st.slots[(PRESS, "roc")].level == Level.NOMINAL


def test_rebuild_rejects_a_cleared_row() -> None:
    with pytest.raises(ValueError, match="CLEARED"):
        rebuild([open_alarm("CLEARED")], CSET)


@pytest.mark.parametrize(("row_state", "kind"), [("UNACK", "RTN"), ("ACKED", "CLEAR")])
def test_switch_closes_orphaned_alarm(row_state: AlarmState, kind: str) -> None:
    orphan = OpenAlarm(ID, "gone_param", row_state, Level.RED, None, "limit", T)
    st = rebuild([orphan], CSET)
    st, trs = switch(st, CSET, CSET)
    assert [(t.param, t.kind, t.note) for t in trs] == [("gone_param", kind, LIMITS_CHANGED)]


def test_shape_version_tracks_check_state_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class V1(Timed):
        State: ClassVar[type] = make_dataclass("S", [("n", int)])

    class V2(Timed):
        State: ClassVar[type] = make_dataclass("S", [("n", str)])

    monkeypatch.setitem(CHECKS, "zz", V1)
    v1 = shape_version()
    monkeypatch.setitem(CHECKS, "zz", V2)
    assert shape_version() != v1  # same kind and State name: the field type counts
