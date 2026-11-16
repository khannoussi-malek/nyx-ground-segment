"""The replay's alarm-state reducer and the merge position (T4-g, D-ALM-14)."""

from datetime import timedelta

from gs.alarms.contract import AlarmState, Transition
from gs.alarms.engine import EngineState, ack, compile_set, initial_state
from gs.common.missiondb import load_dir
from gs.replay.rows import next_state, position
from tests.support.alarms import PERIOD_S, Frame, V, at, feed
from tests.support.mission import MISSION_DIR

CSET = compile_set(load_dir(MISSION_DIR))
P = "cabin_temp_c"
RED_T, YELLOW_T, OK_T = 31.0, 28.0, 22.0


def test_next_state_tracks_engine() -> None:
    """Every transition kind (and D-ALM-08's kept ACK) through engine.step / engine.ack: the
    reducer, folded over the rows alone, equals the engine's alarm state after each one."""
    state: EngineState = initial_state(CSET)
    seq = 0
    folded: AlarmState | None = None
    kinds: list[str] = []

    def check(tr: Transition) -> None:
        nonlocal folded
        a = state.alarms.get(P)
        closes = a is None  # the row after which the alarm is gone (what the SQL rule finds)
        folded = next_state(folded, tr.kind, closes, tr.alert)
        assert folded == (a.state if a else "CLEARED"), tr.kind
        assert folded == tr.alarm_state
        kinds.append(tr.kind)
        if closes:
            folded = None

    def frames(value: float) -> None:
        nonlocal state, seq
        fr: list[Frame] = [(s, s * PERIOD_S, {P: value}) for s in range(seq, seq + 3)]
        seq += 3
        state, out = feed(state, CSET, fr)
        (tr,) = out
        check(tr)

    def acked() -> None:
        nonlocal state
        state, tr = ack(state, V, P, "fc1", "seen", f"ack-{seq}", at(seq * PERIOD_S))
        assert tr is not None
        check(tr)

    frames(YELLOW_T)  # RAISE
    acked()  # ACKED at YELLOW
    frames(RED_T)  # ESCALATE: the re-alarm, UNACK
    acked()  # ACKED at RED
    frames(YELLOW_T)  # DEESCALATE keeps ACKED
    frames(RED_T)  # ESCALATE with RED already acked: stays ACKED, no alert (D-ALM-08)
    frames(OK_T)  # CLEAR
    frames(RED_T)  # RAISE (a new alarm)
    frames(YELLOW_T)  # DEESCALATE keeps UNACK
    frames(OK_T)  # RTN
    frames(RED_T)  # RAISE again on the RTN_UNACK alarm
    frames(OK_T)  # RTN
    acked()  # ACK of an RTN_UNACK alarm closes it
    assert kinds == [
        "RAISE", "ACK", "ESCALATE", "ACK", "DEESCALATE", "ESCALATE", "CLEAR",
        "RAISE", "DEESCALATE", "RTN", "RAISE", "RTN", "ACK",
    ]  # fmt: skip


def test_position_puts_ack_after_named_frame() -> None:
    t5, t6 = at(0.5), at(0.6)
    f5 = {"t_onboard": t5, "seq": 5}
    f6 = {"t_onboard": t6, "seq": 6}
    ack_row = {"kind": "ACK", "id": 99, "after_t_onboard": t5, "after_seq": 5}
    raise6 = {"kind": "RAISE", "id": 10, "trigger_t_onboard": t6, "trigger_seq": 6}
    ev5 = {"id": 3, "t_onboard": t5}
    later = {"t_onboard": t5 + timedelta(microseconds=1), "seq": 0}  # a new epoch's frame
    items = [
        ("event", ev5), ("frame", f6), ("alarm", raise6), ("ack", ack_row), ("frame", f5),
        ("frame", later),
    ]  # fmt: skip
    got = [row for _, row in sorted(items, key=lambda kr: position(kr[0], kr[1]))]  # type: ignore[arg-type]
    assert got == [f5, ack_row, ev5, later, f6, raise6]
    assert position("ack", ack_row) == (t5, 5, 1, 99)
    assert position("event", ev5) == (t5, 2**32, 2, 3)
