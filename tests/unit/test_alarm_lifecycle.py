"""D-ALM-07/08 life cycle (R8 section 5 state table)."""

import pytest

from gs.alarms.contract import Level, Transition
from gs.alarms.engine import EngineState, ack, compile_set, initial_state
from gs.common.missiondb import load_dir
from tests.support.alarms import PERIOD_S, Frame, V, at, feed
from tests.support.mission import MISSION_DIR

CSET = compile_set(load_dir(MISSION_DIR))
P = "cabin_temp_c"
RED_T, YELLOW_T, OK_T = 31.0, 28.0, 22.0
T = at(100.0)


class Run:
    """Drives one parameter frame by frame; seq and t continue across calls."""

    def __init__(self) -> None:
        self.state: EngineState = initial_state(CSET)
        self.seq = 0

    def frames(self, value: float, n: int = 3) -> list[Transition]:
        fr: list[Frame] = [(s, s * PERIOD_S, {P: value}) for s in range(self.seq, self.seq + n)]
        self.seq += n
        self.state, out = feed(self.state, CSET, fr)
        return out

    def ack(self, user: str = "fc1", note: str = "seen") -> Transition | None:
        self.state, tr = ack(self.state, V, P, user, note, f"ack-{self.seq}", T)
        return tr


def test_rtn_before_ack_is_rtn_unack() -> None:
    r = Run()
    (raise_,) = r.frames(RED_T)
    (rtn,) = r.frames(OK_T)
    assert (rtn.kind, rtn.alarm_state, rtn.severity) == ("RTN", "RTN_UNACK", Level.NOMINAL)
    assert rtn.alarm_id == raise_.alarm_id and rtn.closed_at is None and rtn.level is None
    assert r.state.alarms[P].state == "RTN_UNACK"  # stays visible until acked


def test_ack_then_rtn_clears() -> None:
    r = Run()
    r.frames(RED_T)
    a = r.ack()
    assert a is not None
    assert (a.kind, a.alarm_state, a.acked_sev, a.by_user, a.note) == (
        "ACK", "ACKED", Level.RED, "fc1", "seen"
    )  # fmt: skip
    assert a.key == "ack-3" and a.trigger_seq is None and not a.alert
    (clear,) = r.frames(OK_T)
    assert (clear.kind, clear.alarm_state, clear.by_user) == ("CLEAR", "CLEARED", "system")
    assert clear.closed_at == at(5 * PERIOD_S)
    assert P not in r.state.alarms


def test_ack_on_rtn_unack_clears_by_user() -> None:
    r = Run()
    r.frames(RED_T)
    r.frames(OK_T)
    a = r.ack()
    assert a is not None
    assert (a.kind, a.alarm_state, a.by_user, a.closed_at) == ("ACK", "CLEARED", "fc1", T)
    assert P not in r.state.alarms


def test_deescalate_keeps_unack() -> None:
    r = Run()
    r.frames(RED_T)
    (d,) = r.frames(YELLOW_T)
    assert (d.kind, d.alarm_state, d.severity, d.alert) == (
        "DEESCALATE", "UNACK", Level.YELLOW, False
    )  # fmt: skip


def test_acked_yellow_escalating_alerts_again() -> None:  # D-ALM-08 / Q2
    r = Run()
    (y,) = r.frames(YELLOW_T)
    assert (y.kind, y.severity, y.alert) == ("RAISE", Level.YELLOW, False)  # yellow is visual
    r.ack()
    (e,) = r.frames(RED_T)
    assert (e.kind, e.alarm_state, e.alert, e.acked_sev) == (
        "ESCALATE",
        "UNACK",
        True,
        Level.YELLOW,
    )


def test_red_yellow_red_after_ack_no_alert() -> None:  # acked_sev RED kept
    r = Run()
    r.frames(RED_T)
    r.ack()
    (d,) = r.frames(YELLOW_T)
    assert (d.kind, d.alarm_state) == ("DEESCALATE", "ACKED")
    (e,) = r.frames(RED_T)
    assert (e.kind, e.alarm_state, e.alert, e.acked_sev) == ("ESCALATE", "ACKED", False, Level.RED)


def test_rtn_unack_reraise_same_alarm() -> None:
    r = Run()
    (first,) = r.frames(RED_T)
    r.frames(OK_T)
    (again,) = r.frames(RED_T)
    assert (again.kind, again.alarm_state, again.alert) == ("RAISE", "UNACK", True)
    assert again.alarm_id == first.alarm_id and again.opened_at == first.opened_at
    assert again.key != first.key


def test_ack_requires_note() -> None:
    r = Run()
    r.frames(RED_T)
    with pytest.raises(ValueError, match="note"):
        ack(r.state, V, P, "fc1", "  ", "k", T)


def test_second_ack_is_noop() -> None:
    r = Run()
    r.frames(RED_T)
    r.ack()
    before = r.state
    assert r.ack(user="fc2") is None and r.state is before


def test_ack_without_open_alarm_is_noop() -> None:
    r = Run()
    assert r.ack() is None and r.state.alarms == {}
