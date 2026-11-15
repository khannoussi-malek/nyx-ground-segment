"""Cabin effects of isolate_valve and vent_cabin (D-SIM-10)."""

from gs.simulator.commands import Cabin, cabin_tick, execute
from tests.support.sim import tc


def test_isolate_during_leak_recovers_at_half_kpa_per_s() -> None:
    c = execute(tc("isolate_valve", valve="A"), Cabin(), leak_offset=-4.0, now=0.0)
    assert (c.offset_kpa, c.rate) == (-4.0, 0.5)
    offsets = []
    for k in range(1, 81):
        c = cabin_tick(c, 0.1, k * 0.1)
        offsets.append(c.offset_kpa)
    assert abs(offsets[39] - -2.0) < 1e-9  # 4 s x 0.5
    assert c.offset_kpa == 0.0 and c.rate >= 0  # -4.0 + 8 s x 0.5, clamped, no overshoot
    assert cabin_tick(c, 0.1, 8.1).offset_kpa == 0.0  # stays at nominal


def test_isolate_never_overshoots_nominal() -> None:
    c = execute(tc("isolate_valve", valve="B"), Cabin(), leak_offset=-0.01, now=0.0)
    assert cabin_tick(c, 0.1, 0.1) == Cabin()


def test_isolate_adds_the_leak_to_an_existing_offset() -> None:
    c = execute(tc("isolate_valve", valve="A"), Cabin(-1.0, -2.0, 5.0), leak_offset=-0.5, now=1.0)
    assert c == Cabin(-1.5, 0.5, None)


def test_isolate_without_leak_keeps_the_offset() -> None:
    c = execute(tc("isolate_valve", valve="A"), Cabin(-1.0), leak_offset=None, now=0.0)
    assert c == Cabin(-1.0, 0.5, None)


def test_vent_three_seconds_drops_six() -> None:
    c = execute(tc("vent_cabin", duration_s=3), Cabin(), leak_offset=None, now=0.0)
    assert (c.rate, c.until) == (-2.0, 3.0)
    for k in range(1, 31):
        c = cabin_tick(c, 0.1, k * 0.1)
    assert abs(c.offset_kpa - -6.0) < 1e-9
    for k in range(31, 60):
        c = cabin_tick(c, 0.1, k * 0.1)
    assert abs(c.offset_kpa - -6.0) < 1e-9 and c.rate == 0.0  # then constant


def test_vent_stops_inside_a_tick() -> None:
    c = execute(tc("vent_cabin", duration_s=1), Cabin(), leak_offset=None, now=0.0)
    c = cabin_tick(c, 0.7, 0.7)
    c = cabin_tick(c, 0.7, 1.4)  # a stalled tick: only 0.3 s of it is venting
    assert abs(c.offset_kpa - -2.0) < 1e-9


def test_idle_cabin_is_unchanged() -> None:
    assert cabin_tick(Cabin(-3.0), 0.1, 5.0) == Cabin(-3.0)
