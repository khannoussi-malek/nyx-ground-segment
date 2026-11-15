from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from gs.alarms.contract import TransitionKind
from gs.alarms.quality import FAULT_PARAMS, FaultWindow, QEvent, quality

T0 = datetime(2026, 1, 1, tzinfo=UTC)
V, P = "NYX-TA1", "cabin_temp_c"


def ev(kind: TransitionKind, s: float, vehicle: str = V, param: str = P) -> QEvent:
    return QEvent(vehicle, param, kind, T0 + timedelta(seconds=s), uuid4())


def raises(times: list[float], vehicle: str = V) -> list[QEvent]:
    return [ev("RAISE", t, vehicle) for t in times]


def test_psi_zero_below_two_alarms() -> None:
    q = quality(raises([0]), None)
    assert q.psi[f"{V}/{P}"] == 0 and q.raises == 1 and q.chattering == ()
    assert quality([], None).peak_10min == {"all": 0}


def test_wang_chen_example_low_psi_but_rule_3a() -> None:
    evs = [e for i in range(8) for e in (ev("RAISE", 116 * i), ev("RTN", 116 * i + 5))]
    q = quality(evs, None)
    assert q.psi[f"{V}/{P}"] == pytest.approx(1 / 116) and q.psi[f"{V}/{P}"] < 0.05
    assert q.short_t1 == 8 and q.chattering == (f"{V}/{P}",)


def test_psi_flags_fast_chatter() -> None:
    q = quality(raises([10 * i for i in range(6)]), None)
    assert q.psi[f"{V}/{P}"] == pytest.approx(0.1) and q.chattering == (f"{V}/{P}",)


def test_short_t0_and_long_alarm_not_chattering() -> None:
    q = quality([ev("RAISE", 0), ev("CLEAR", 60), ev("RAISE", 70)], None)
    assert (q.short_t1, q.short_t0) == (0, 1) and q.chattering == (f"{V}/{P}",)
    q = quality([ev("RAISE", 0), ev("RTN", 60), ev("RAISE", 200), ev("ESCALATE", 210)], None)
    assert (q.short_t1, q.short_t0, q.chattering) == (0, 0, ())


def test_flood_starts_at_ten_ends_below_five() -> None:
    # 10 RAISEs one minute apart: flood from the 10th (t=540); trailing count falls to 4
    # when the 6th..1st have expired, i.e. at raise[5] + 10 min = 300 + 600
    q = quality(raises([60 * i for i in range(10)]), None)
    assert q.time_in_flood_s == pytest.approx(900 - 540)
    assert quality(raises([60 * i for i in range(9)]), None).time_in_flood_s == 0


def test_peak_per_vehicle_and_combined() -> None:
    evs = raises([0, 1, 2], "A") + raises([3, 4], "B") + raises([5000], "A")
    q = quality(evs, None)
    assert q.peak_10min == {"A": 3, "B": 2, "all": 5}


def test_step_alarms_excluded() -> None:
    w = FaultWindow(V, "step", T0, T0 + timedelta(seconds=100), frozenset({P}))
    evs = [ev("RAISE", 1), ev("RTN", 2), ev("RAISE", 3), ev("RAISE", 500)]
    q = quality(evs, [w])
    assert q.raises == 1 and q.short_t1 == 0 and q.psi[f"{V}/{P}"] == 0
    assert q.false_raises == 1


def test_false_raises_only_with_a_fault_log() -> None:
    leak = FaultWindow(V, "leak", T0, T0 + timedelta(seconds=100), FAULT_PARAMS["leak"])
    inside = ev("RAISE", 100, param="cabin_pressure_kpa")  # inclusive end
    outside = ev("RAISE", 101, param="cabin_pressure_kpa")
    wrong_param = ev("RAISE", 5)
    ack = ev("ACK", 500, param="x")
    evs = [inside, outside, wrong_param, ack]
    assert quality(evs, None).false_raises is None
    assert quality(evs, []).false_raises == 3
    assert quality(evs, [leak]).false_raises == 2
