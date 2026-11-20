import pytest
from hypothesis import find, given, settings

from gs.alarms.checks.limit import LimitCheck, deadband, floor, meets_floor
from gs.alarms.checks.roc import RocCheck
from tests.property.strategies import Tuning, mission_for, tunings


@settings(max_examples=100)
@given(tunings())
def test_tuning_draws_respect_floor(t: Tuning) -> None:
    """Every drawn tuning set loads through the mission-db validator (R14 E-9) and is applied."""
    db = mission_for(t)  # raises MissionDbError below the deadband floor
    checks = [c for e in db.limits.values() for c in e.checks]
    assert all(meets_floor(c) for c in checks if isinstance(c, LimitCheck))
    assert {c.persistence for c in checks} == {t.persistence}
    assert {c.window_s for c in checks if isinstance(c, RocCheck)} == {t.window_s}


def test_tuning_draws_reach_the_floor() -> None:
    """The floor itself is drawn (hysteresis_pct and hysteresis_abs checks): deadband == floor."""
    t = find(tunings(), lambda t: t.above_floor_pct == 0)
    lims = [
        c for e in mission_for(t).limits.values() for c in e.checks if isinstance(c, LimitCheck)
    ]
    assert {c.hysteresis_abs is not None for c in lims} == {True, False}
    for c in lims:
        assert deadband(c) == pytest.approx(floor(c), rel=1e-9)
