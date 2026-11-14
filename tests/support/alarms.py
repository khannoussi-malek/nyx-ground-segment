"""Helpers for the alarm check and engine tests."""

from collections.abc import Iterable, Sequence

from gs.alarms.checks import Check, CheckConfig, Reading, Sample
from gs.alarms.contract import Level
from gs.common.missiondb import load_dir
from tests.support.mission import MISSION_DIR

PERIOD_S = 0.1


def shipped(param: str, kind: str) -> CheckConfig:
    """The resolved check of `kind` for `param` in the shipped mission/ set."""
    (cfg,) = [c for c in load_dir(MISSION_DIR).limits[param].checks if c.type == kind]
    return cfg


def series(values: Sequence[float], period_s: float = PERIOD_S) -> list[Sample]:
    return [Sample(i, i * period_s, v, period_s) for i, v in enumerate(values)]


def run(
    chk: Check, samples: Iterable[Sample], start: Level = Level.NOMINAL
) -> list[Reading | None]:
    """N = 1: each reading becomes the current level of the next step (None keeps it)."""
    cur, st, out = start, chk.initial(), []
    for s in samples:
        st, r = chk.step(st, s, cur)
        cur = cur if r is None else r.level
        out.append(r)
    return out
