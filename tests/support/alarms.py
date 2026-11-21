"""Helpers for the alarm check and engine tests."""

import random
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from gs.alarms.checks import Check, CheckConfig, Reading, Sample
from gs.alarms.contract import Level, Transition
from gs.alarms.engine import CompiledSet, EngineState, initial_state, step
from gs.common.missiondb import load_dir
from gs.simulator.frames import NOMINALS
from tests.support.mission import MISSION_DIR

PERIOD_S = 0.1
V = "NYX-TA1"
EPOCH = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
LEAK_KPA_PER_S = -0.4  # simulator `leak` fault (design 3.3)
CABIN_P = "cabin_pressure_kpa"

Frame = tuple[int, float, dict[str, float | None]]  # (seq, t_onboard seconds after EPOCH, values)


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


def at(t: float) -> datetime:
    return EPOCH + timedelta(seconds=t)


def feed(
    state: EngineState,
    cset: CompiledSet,
    frames: Iterable[Frame],
    resets: Iterable[int] = (),
    mode: str | None = None,
) -> tuple[EngineState, list[Transition]]:
    out: list[Transition] = []
    reset_seqs = set(resets)
    for seq, t, values in frames:
        state, trs = step(
            state, cset, V, EPOCH, seq, at(t), PERIOD_S, values, seq in reset_seqs, mode=mode
        )
        out.extend(trs)
    return state, out


def run_engine(
    cset: CompiledSet,
    frames: Iterable[Frame],
    resets: Iterable[int] = frozenset(),
    mode: str | None = None,
) -> list[Transition]:
    return feed(initial_state(cset), cset, frames, resets, mode)[1]


def nominal_frames(seconds: float, seed: int, leak_per_s: float = 0.0) -> list[Frame]:
    """Every simulator parameter at its design 3.3 nominal with Gaussian sigma per frame; cabin
    pressure falls at `leak_per_s` from t = 0 when given."""
    rng = random.Random(seed)
    frames: list[Frame] = []
    for i in range(round(seconds / PERIOD_S)):
        t = i * PERIOD_S
        vals: dict[str, float | None] = {
            p: rng.gauss(nom, sd) + (leak_per_s * t if p == CABIN_P else 0.0)
            for p, (nom, sd) in NOMINALS.items()
        }
        frames.append((i, t, vals))
    return frames


def leak_frames(seconds: float, seed: int) -> list[Frame]:
    """ATP-02 leak (design 3.4): cabin pressure -0.4 kPa/s from 101.3 at t = 0."""
    return nominal_frames(seconds, seed, LEAK_KPA_PER_S)
