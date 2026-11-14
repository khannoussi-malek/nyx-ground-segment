import pytest

from gs.alarms.checks import Reading
from gs.alarms.checks.limit import Limit, LimitCheck
from gs.alarms.contract import Level
from tests.support.alarms import run, series, shipped

N, YELLOW, RED = Level.NOMINAL, Level.YELLOW, Level.RED
# R8's table assumes 2 % (d = 0.22); built directly, not via load
CP2 = LimitCheck(
    type="limit", red_low=95.0, yellow_low=98.0, yellow_high=104.0, red_high=106.0,
    hysteresis_pct=2.0,
)  # fmt: skip
BOUNDARY = [  # R8 §1, 16 rows: (value, start level, expected)
    (94.99, N, RED), (95.00, N, RED), (95.01, N, YELLOW), (97.99, N, YELLOW), (98.00, N, YELLOW),
    (98.01, N, N), (98.21, YELLOW, YELLOW), (98.23, YELLOW, N), (103.99, N, N),
    (104.00, N, YELLOW), (104.01, N, YELLOW), (105.99, N, YELLOW), (106.00, N, RED),
    (106.01, N, RED), (95.21, RED, RED), (95.23, RED, YELLOW),
]  # fmt: skip


def levels(cfg: LimitCheck, values: list[float], start: Level = N) -> list[Reading]:
    out = run(Limit(cfg), series(values), start)
    assert all(r is not None for r in out)  # a limit check always has an opinion
    return [r for r in out if r is not None]


@pytest.mark.parametrize(("v", "start", "want"), BOUNDARY)
def test_boundaries(v: float, start: Level, want: Level) -> None:  # ALM-01
    assert levels(CP2, [v], start)[0].level == want


def test_shipped_deadband_rows() -> None:  # 15 % of 11 = 1.65 (§3.4)
    cfg = shipped("cabin_pressure_kpa", "limit")
    assert isinstance(cfg, LimitCheck)
    assert levels(cfg, [96.64], RED)[0].level == RED
    assert levels(cfg, [96.66], RED)[0].level == YELLOW
    assert levels(cfg, [99.64], YELLOW)[0].level == YELLOW
    assert levels(cfg, [99.66], YELLOW)[0].level == N


def test_side() -> None:
    assert [r.side for r in levels(CP2, [94.0, 107.0, 100.0])] == ["LOW", "HIGH", None]


def test_held_side_is_the_deciding_bound() -> None:
    assert levels(CP2, [98.1], YELLOW)[0] == Reading(YELLOW, "LOW")
    assert levels(CP2, [105.9], RED)[0] == Reading(RED, "HIGH")


def test_one_sided_abs() -> None:
    cfg = shipped("bus_current_a", "limit")
    assert isinstance(cfg, LimitCheck)
    assert levels(cfg, [8.0])[0] == Reading(YELLOW, "HIGH")
    assert levels(cfg, [7.81], YELLOW)[0] == Reading(YELLOW, "HIGH")
    assert levels(cfg, [7.79], YELLOW)[0] == Reading(N, None)
    assert levels(cfg, [-100.0])[0] == Reading(N, None)  # no low bound


def test_red_steps_down_through_yellow() -> None:  # deadband applies band by band
    out = levels(CP2, [94.0, 95.1, 95.3, 98.1, 98.3])
    assert [r.level for r in out] == [RED, RED, YELLOW, YELLOW, N]
