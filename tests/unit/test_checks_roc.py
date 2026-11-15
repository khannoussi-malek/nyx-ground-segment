import random

import pytest

from gs.alarms.checks import Reading, Sample
from gs.alarms.checks.limit import Limit, LimitCheck
from gs.alarms.checks.roc import Roc, RocCheck
from gs.alarms.contract import Level
from tests.support.alarms import PERIOD_S, run, series, shipped

LEAK_PER_S, NOMINAL_KPA, SIGMA = -0.4, 101.3, 0.02


def leak_series() -> list[Sample]:  # ALM-04: 0.4 kPa/s from 101.3, sigma 0.02, seed 1
    rng = random.Random(1)
    return series(
        [NOMINAL_KPA + LEAK_PER_S * i * PERIOD_S + rng.gauss(0, SIGMA) for i in range(200)]
    )


def shipped_roc() -> RocCheck:
    cfg = shipped("cabin_pressure_kpa", "roc")
    assert isinstance(cfg, RocCheck)
    return cfg


def shipped_limit() -> LimitCheck:
    cfg = shipped("cabin_pressure_kpa", "limit")
    assert isinstance(cfg, LimitCheck)
    return cfg


def ramp(per_s: float, n: int = 40) -> list[Sample]:
    return series([NOMINAL_KPA + per_s * i * PERIOD_S for i in range(n)])


def roc_readings(points: list[tuple[float, float]], period_s: float) -> list[Reading | None]:
    samples = [Sample(i, t, v, period_s) for i, (t, v) in enumerate(points)]
    return run(Roc(shipped_roc()), samples)


def roc_levels(samples: list[Sample], cfg: RocCheck | None = None) -> list[Reading]:
    return [r for r in run(Roc(cfg or shipped_roc()), samples) if r is not None]


def judged(r: Reading | None) -> tuple[Level, str | None] | None:
    """(level, side) of a reading, its value aside."""
    return None if r is None else (r.level, r.side)


def first_seq(readings: list[Reading | None], level: Level) -> int | None:
    return next((i for i, r in enumerate(readings) if r is not None and r.level == level), None)


def test_leak_alarms_before_yellow() -> None:  # ALM-04, §3.4: ~1.3 s vs 8.25 s
    first_roc = first_seq(run(Roc(shipped_roc()), leak_series()), Level.YELLOW)
    first_limit = first_seq(run(Limit(shipped_limit()), leak_series()), Level.YELLOW)
    assert first_roc is not None and first_limit is not None
    assert (first_limit - first_roc) * PERIOD_S >= 5.0


def test_falling_only_ignores_rise() -> None:
    out = roc_levels(ramp(+0.5))
    assert out and all(r.level == Level.NOMINAL for r in out)


def test_too_few_samples_no_opinion() -> None:  # < 80 % of window_s / period_s in the window
    assert roc_readings([(0.0, 101.3), (0.5, 100.0)], period_s=0.1)[-1] is None


def test_eight_of_ten_samples_is_enough() -> None:  # 80 % exactly (float 8.000000000000002)
    pts = [(i * 0.1, 101.3 - 0.04 * i) for i in range(8)]
    assert judged(roc_readings(pts, period_s=0.1)[-1]) == (Level.YELLOW, "FALLING")
    assert roc_readings(pts[:7], period_s=0.1)[-1] is None


def test_window_drops_old_samples() -> None:  # a past fall leaves the 1 s window
    pts = [(i * 0.1, 101.3 - 0.04 * i) for i in range(10)] + [
        (i * 0.1, 100.94) for i in range(10, 25)
    ]
    out = roc_readings(pts, period_s=0.1)
    assert judged(out[9]) == (Level.YELLOW, "FALLING") and judged(out[-1]) == (Level.NOMINAL, None)


def test_rising_and_both_directions() -> None:
    def cfg(direction: str) -> RocCheck:
        return RocCheck.model_validate(
            {"type": "roc", "limit_per_s": 0.15, "window_s": 1.0, "direction": direction,
             "severity": "RED"}
        )  # fmt: skip

    up, down = ramp(+0.5), ramp(-0.5)
    assert set(map(judged, roc_levels(up, cfg("rising")))) == {(Level.RED, "RISING")}
    assert set(map(judged, roc_levels(down, cfg("rising")))) == {(Level.NOMINAL, None)}
    assert set(map(judged, roc_levels(up, cfg("both")))) == {(Level.RED, "RISING")}
    assert set(map(judged, roc_levels(down, cfg("both")))) == {(Level.RED, "FALLING")}
    assert set(map(judged, roc_levels(down, cfg("falling")))) == {(Level.RED, "FALLING")}


def test_duplicate_timestamps_no_opinion() -> None:  # no slope from one instant
    assert roc_readings([(1.0, 101.0)] * 12, period_s=0.1)[-1] is None


def test_reading_carries_slope() -> None:  # Task 8: a ROC reading's value is the slope per s
    rd = roc_levels(ramp(-0.4, n=20))[-1]  # no noise
    assert rd.value == pytest.approx(-0.4, abs=1e-9) and rd.side == "FALLING"
