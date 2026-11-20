"""P5 (design 11.5, D-EXT-14): every alarm's transitions form a valid D-ALM-07/08 path.

P3 and P4 land with the derived-value and mode work they depend on (EXT-01 after the 47).
"""

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from gs.alarms.contract import AlarmState, Level, Transition
from gs.alarms.engine import ack, compile_set, initial_state, step
from tests.property.strategies import Run, Tuning, mission_for, runs, tunings
from tests.support.alarms import EPOCH, PERIOD_S, V, at

# (kind, state before) -> states allowed after; None = no open alarm. D-ALM-07/08.
_PATHS: dict[tuple[str, AlarmState | None], tuple[AlarmState, ...]] = {
    ("RAISE", None): ("UNACK",),
    ("RAISE", "RTN_UNACK"): ("UNACK",),  # the same alarm again before the operator saw it clear
    ("ESCALATE", "UNACK"): ("UNACK",),
    ("ESCALATE", "ACKED"): ("UNACK", "ACKED"),
    ("DEESCALATE", "UNACK"): ("UNACK",),
    ("DEESCALATE", "ACKED"): ("ACKED",),
    ("RTN", "UNACK"): ("RTN_UNACK",),
    ("CLEAR", "ACKED"): ("CLEARED",),
    ("ACK", "UNACK"): ("ACKED",),
    ("ACK", "RTN_UNACK"): ("CLEARED",),
}


def _check_path(open_: dict[str, tuple[AlarmState, Level, object]], tr: Transition) -> None:
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
    if tr.kind == "DEESCALATE":
        assert before and Level.NOMINAL < tr.severity < before[1]
    if tr.alarm_state == "CLEARED":
        open_.pop(tr.param, None)
        assert tr.closed_at is not None
    else:
        open_[tr.param] = (
            tr.alarm_state,
            tr.severity if tr.kind != "RTN" else Level.NOMINAL,
            tr.alarm_id,
        )


def _replay(tuning: Tuning, run: Run) -> list[Transition]:
    cset = compile_set(mission_for(tuning))
    state = initial_state(cset)
    model: dict[str, tuple[AlarmState, Level, object]] = {}
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


@given(st.data())
def test_p5_transition_paths_valid(data: st.DataObject) -> None:
    tuning = data.draw(tunings())
    run = data.draw(runs(tuning.faults))
    trs = _replay(tuning, run)
    event(f"transitions>0={bool(trs)}")
    event("kinds=" + ",".join(sorted({t.kind for t in trs})))


@pytest.mark.skip(reason="P3 needs derived values and mission-db timelines (D-EXT-05/06, EXT-01)")
def test_p3_same_input_same_keys() -> None: ...


@pytest.mark.skip(reason="P4 needs modes and derived state in the snapshot (D-EXT-06, EXT-01)")
def test_p4_snapshot_any_split() -> None: ...
