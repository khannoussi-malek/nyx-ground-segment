"""P5 (design 11.5, D-EXT-14): every alarm's transitions form a valid D-ALM-07/08 path.

P3 and P4 (D-EXT-14) run the real `fold` over batches, each in a raw-only form and in a form with
derived parameters, calibration and a mode per frame (D-EXT-05, D-EXT-06).
"""

from hypothesis import event, given
from hypothesis import strategies as st

from gs.alarms.contract import AlarmState, Level, Transition
from gs.alarms.engine import MODE_CHANGED, ack, compile_set, initial_state, step
from gs.common.missiondb import MissionDb
from gs.ingest.fold import dump_vehicle
from tests.property import profiles
from tests.property.folding import Played, headers, play
from tests.property.strategies import (
    LEAK,
    Run,
    Tuning,
    mission_for,
    mode_schedule,
    runs,
    tunings,
)
from tests.support.alarms import EPOCH, PERIOD_S, V, at

# (kind, state before) -> states allowed after; None = no open alarm. D-ALM-07/08.
_PATHS: dict[tuple[str, AlarmState | None], tuple[AlarmState, ...]] = {
    ("RAISE", None): ("UNACK",),
    ("RAISE", "RTN_UNACK"): (
        "UNACK",
    ),  # D-ALM-07 RTN_UNACK: the same alarm again before the operator saw it clear
    ("ESCALATE", "UNACK"): ("UNACK",),
    ("ESCALATE", "ACKED"): ("UNACK", "ACKED"),  # which one: D-ALM-08, checked on acked_sev below
    ("DEESCALATE", "UNACK"): ("UNACK",),
    ("DEESCALATE", "ACKED"): ("ACKED",),
    ("RTN", "UNACK"): ("RTN_UNACK",),
    ("CLEAR", "ACKED"): ("CLEARED",),
    ("ACK", "UNACK"): ("ACKED",),
    ("ACK", "RTN_UNACK"): ("CLEARED",),
}


# state, severity, alarm id, severity at the operator's ACK (None before one)
_Open = tuple[AlarmState, Level, object, Level | None]


def _check_path(open_: dict[str, _Open], tr: Transition) -> None:
    """Advance the per-param model by one transition, asserting it is a legal step."""
    before = open_.get(tr.param)
    prev = before[0] if before else None
    allowed = _PATHS.get((tr.kind, prev))
    assert allowed is not None, f"{tr.kind} from {prev} on {tr.param}"
    assert tr.alarm_state in allowed, (tr.kind, prev, tr.alarm_state)
    if before:
        assert tr.alarm_id == before[2], "one alarm id per open alarm"
    if tr.kind == "ESCALATE":
        assert before and tr.severity > before[1]
        if prev == "ACKED":  # D-ALM-08: only an acked RED keeps its ack; a worse alarm alerts once
            red_acked = before[3] == Level.RED
            assert tr.alarm_state == ("ACKED" if red_acked else "UNACK"), (tr.alarm_state, before)
            assert tr.alert == (tr.severity == Level.RED and not red_acked)
        else:
            assert tr.alert == (tr.severity == Level.RED)
    if tr.kind == "RAISE":
        assert tr.alert == (tr.severity == Level.RED)
    if tr.kind in ("DEESCALATE", "RTN", "CLEAR", "ACK"):
        assert not tr.alert
    if tr.kind == "DEESCALATE":
        assert before and Level.NOMINAL < tr.severity < before[1]
    if tr.alarm_state == "CLEARED":
        open_.pop(tr.param, None)
        assert tr.closed_at is not None
    else:
        acked = before[3] if before else None
        if tr.kind == "ACK":
            acked = before[1] if before else None  # severity when the operator acked
        elif tr.kind in ("RAISE", "ESCALATE") and tr.alarm_state == "UNACK":
            acked = None
        open_[tr.param] = (
            tr.alarm_state,
            tr.severity if tr.kind != "RTN" else Level.NOMINAL,
            tr.alarm_id,
            acked,
        )


def _replay(tuning: Tuning, run: Run) -> list[Transition]:
    cset = compile_set(mission_for(tuning))
    state = initial_state(cset)
    model: dict[str, _Open] = {}
    seen: set[str] = set()
    out: list[Transition] = []
    for seq, t, values, reset in run.frames:
        state, trs = step(state, cset, V, EPOCH, seq, at(t), PERIOD_S, values, reset)
        if seq in run.acks:
            for param in sorted(state.alarms):
                state, a = ack(state, V, param, "op", "note", f"ack-{seq}-{param}", at(t))
                trs = (*trs, *([a] if a else []))
        for tr in trs:
            assert tr.key not in seen, "duplicate transition key"
            seen.add(tr.key)
            _check_path(model, tr)
            out.append(tr)
        # at most one open alarm per param, and the engine agrees with the model
        assert {p: a.state for p, a in state.alarms.items()} == {p: m[0] for p, m in model.items()}
    return out


@profiles.PROFILE
@given(st.data())
def test_p5_transition_paths_valid(data: st.DataObject) -> None:
    tuning = data.draw(tunings())
    run = data.draw(runs(tuning.faults))
    trs = _replay(tuning, run)
    event(f"transitions>0={bool(trs)}")
    event("kinds=" + ",".join(sorted({t.kind for t in trs})))


@st.composite
def _timelines(
    draw: st.DrawFn, derived: bool = False
) -> tuple[Tuning, Run, tuple[MissionDb, MissionDb | None], int]:
    """Frames, a mission-db timeline (a second tuning from some real frame on, or none)."""
    tuning = draw(tunings())
    run = draw(runs(tuning.faults))
    other = draw(st.none() | tunings())
    n = len(run.frames)
    switch = draw(st.integers(1, max(1, n - 1)))
    second = None if other is None else mission_for(other, derived)
    return tuning, run, (mission_for(tuning, derived), second), switch


def _cuts(n: int) -> st.SearchStrategy[frozenset[int]]:
    return st.sets(st.integers(1, max(1, n - 1)), max_size=30).map(frozenset)


@profiles.PROFILE
@given(st.data())
def test_p3_same_input_same_keys(data: st.DataObject) -> None:
    """Same frames + same ACKs + same mission-db timeline => the same transitions, whatever the
    batch split; a late frame (re-delivery with other values) changes nothing: it is not
    classified and leaves every ROC window and the state as it was (EX-13). The "one batch" run
    is itself cut at ACK frames and at the mission-db switch (D-ALM-14), so the split only moves
    the other boundaries. Raw values only; derived values and modes are in the next test."""
    _, run, mdbs, switch = data.draw(_timelines())
    n = len(run.frames)  # an empty run has nothing to re-deliver
    late = data.draw(
        st.lists(
            st.tuples(st.integers(0, max(0, n - 1)), st.integers(0, 20)), max_size=8 if n else 0
        )
    )
    base = headers(run)
    whole = play(base, mdbs, switch, run.acks, frozenset())
    stream = headers(run, late)
    split = play(stream, mdbs, switch, run.acks, data.draw(_cuts(len(stream))))
    assert split.transitions == whole.transitions
    assert dump_vehicle(split.end) == dump_vehicle(whole.end)
    event(f"transitions>0={bool(whole.transitions)}")
    event(f"late={bool(late)}")
    event(f"switched={mdbs[1] is not None and mdbs[0].set_sha256 != mdbs[1].set_sha256}")


@profiles.PROFILE
@given(st.data())
def test_p4_snapshot_any_split(data: st.DataObject) -> None:
    """Dump to JSON and load at random batch boundaries (with a mission-db change between) =>
    the same transitions and end state as the uninterrupted run. Raw values only; derived values
    and modes are in the next test."""
    _, run, mdbs, switch = data.draw(_timelines())
    stream = headers(run)
    cuts = data.draw(_cuts(len(stream)))
    whole = play(stream, mdbs, switch, run.acks, cuts)
    again = play(stream, mdbs, switch, run.acks, cuts, restore=True)
    assert again.transitions == whole.transitions
    assert dump_vehicle(again.end) == dump_vehicle(whole.end)
    event(f"transitions>0={bool(whole.transitions)}")


def _coverage(p: Played, modes: tuple[str, ...], mdbs: tuple[MissionDb, MissionDb | None]) -> None:
    """Hypothesis events: each interesting case of the derived/mode draws must occur (a property
    that never meets its cases proves nothing)."""
    for reason in sorted({why for _, c in p.computed for _, why in c.nulls}):
        event(f"null={reason}")
    event(f"mode_switches>0={len(set(modes)) > 1}")
    event(f"mode_changed_transition={any(t.note == MODE_CHANGED for t in p.transitions)}")
    event(f"transitions>0={bool(p.transitions)}")
    event(f"db_change={mdbs[1] is not None and mdbs[0].set_sha256 != mdbs[1].set_sha256}")


@profiles.PROFILE
@given(st.data())
def test_p3_same_input_same_keys_with_derived_and_modes(data: st.DataObject) -> None:
    """P3 with derived parameters, a calibration table and a mode per frame: the batch split moves
    neither the transition keys nor any derived value; a late frame gets `rate_of` NULL and
    leaves the window (and so the next value) as it was (EX-13)."""
    _, run, mdbs, switch = data.draw(_timelines(derived=True))
    n = len(run.frames)
    modes = data.draw(mode_schedule(n))
    late = data.draw(
        st.lists(
            st.tuples(st.integers(0, max(0, n - 1)), st.integers(0, 20)), max_size=8 if n else 0
        )
    )
    whole = play(headers(run, modes=modes), mdbs, switch, run.acks, frozenset())
    stream = headers(run, late, modes)
    split = play(stream, mdbs, switch, run.acks, data.draw(_cuts(len(stream))))
    assert split.transitions == whole.transitions
    assert dump_vehicle(split.end) == dump_vehicle(whole.end)  # incl. the rate_of windows
    assert [c.values for late_, c in split.computed if not late_] == [
        c.values for _, c in whole.computed
    ]
    for late_, c in split.computed:
        if late_:
            assert c.values[LEAK] is None
            assert (LEAK, "late") in c.nulls
    _coverage(split, modes, mdbs)
    event(f"late={bool(late)}")


@profiles.PROFILE
@given(st.data())
def test_p4_snapshot_any_split_with_set_change_and_mode_switch(data: st.DataObject) -> None:
    """P4 with derived parameters and modes: JSON snapshot / restore at random batch boundaries,
    with a mission-db change and mode switches between them, equals the uninterrupted run."""
    _, run, mdbs, switch = data.draw(_timelines(derived=True))
    modes = data.draw(mode_schedule(len(run.frames)))
    stream = headers(run, modes=modes)
    cuts = data.draw(_cuts(len(stream)))
    whole = play(stream, mdbs, switch, run.acks, cuts)
    again = play(stream, mdbs, switch, run.acks, cuts, restore=True)
    assert again.transitions == whole.transitions
    assert dump_vehicle(again.end) == dump_vehicle(whole.end)
    assert [c.values for _, c in again.computed] == [c.values for _, c in whole.computed]
    _coverage(whole, modes, mdbs)
