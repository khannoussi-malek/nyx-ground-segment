from datetime import UTC, datetime, timedelta

from gs.alarms.quality import Quality
from perf.soak_count import VehicleCount, lost_by_epoch, passed, render

E1 = datetime(2026, 11, 16, 8, tzinfo=UTC)
E2 = E1 + timedelta(hours=1)


def test_lost_by_epoch() -> None:
    assert lost_by_epoch([]) == 0
    assert lost_by_epoch([(E1, 0, 99, 100)]) == 0  # gapless
    assert lost_by_epoch([(E1, 10, 19, 7)]) == 3  # one epoch missing 3
    assert lost_by_epoch([(E1, 0, 9, 8), (E2, 0, 4, 4)]) == 3  # two epochs add up


def _q(false_raises: int | None = 0, chattering: tuple[str, ...] = ()) -> Quality:
    return Quality(5, false_raises, {}, chattering, 0, 0, {"all": 1}, 0.0)


def _c(lost: int = 0, gaps: int = 0) -> VehicleCount:
    return VehicleCount("NYX-TA1", 1, 72000, lost, gaps)


def test_summary_pass_rule() -> None:
    assert passed([_c(), _c()], _q())
    assert not passed([_c(), _c(lost=1)], _q())  # one lost frame
    assert not passed([_c()], _q(false_raises=1))
    assert not passed([_c()], _q(false_raises=None))  # unjudged is not a pass
    assert not passed([_c()], _q(chattering=("NYX-TA1/bus_current_a",)))


def test_render_states_the_verdict_and_the_numbers() -> None:
    ok = render(E1, 2.0, [_c()], 0, _q())
    assert "**PASS**" in ok and "72000" in ok and "overflow: 0" in ok
    bad = render(E1, 2.0, [_c(lost=3, gaps=1)], 4, _q(chattering=("NYX-TA1/x",)))
    assert "**FAIL**" in bad and "NYX-TA1/x" in bad and "overflow: 4" in bad
