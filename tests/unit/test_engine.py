import dataclasses
import random
from dataclasses import dataclass
from typing import ClassVar, Literal

import pytest

from gs.alarms.checks import CHECKS, CheckConfig, Reading, Sample, register
from gs.alarms.checks.limit import LimitCheck
from gs.alarms.contract import Level
from gs.alarms.engine import CompiledSet, compile_set, initial_state, step
from gs.common.missiondb import LimitEntry, load_dir
from gs.ingest.events import alarm_id_for, transition_key
from tests.support.alarms import (
    EPOCH,
    PERIOD_S,
    Frame,
    V,
    at,
    leak_frames,
    nominal_frames,
    run_engine,
)
from tests.support.mission import MISSION_DIR, files, ld

DB = load_dir(MISSION_DIR)
CSET = compile_set(DB)  # N = 3
TEMP = "cabin_temp_c"


def temp_series(values: list[float | None]) -> list[Frame]:  # seq from 0, 10 Hz
    return [(i, i * PERIOD_S, {TEMP: v}) for i, v in enumerate(values)]


def test_spike_no_alarm() -> None:  # ALM-03
    assert run_engine(CSET, temp_series([22, 31, 22, 22, 22, 22])) == []


def test_two_reds_no_alarm() -> None:  # N = 3 means three in a row
    assert run_engine(CSET, temp_series([22, 31, 31, 22, 31, 31, 22])) == []


def test_three_reds_raise_once_on_third() -> None:  # ALM-05 latency from the N-th frame
    tr = run_engine(CSET, temp_series([22, 31, 31, 31, 31]))
    assert [(t.kind, t.severity, t.trigger_seq, t.alert) for t in tr] == [
        ("RAISE", Level.RED, 3, True)
    ]


def test_mixed_ring_escalates_to_its_minimum() -> None:  # red needs no own N (D-ALM-03)
    tr = run_engine(CSET, temp_series([28, 31, 31, 31]))
    assert [(t.kind, t.severity, t.trigger_seq) for t in tr] == [
        ("RAISE", Level.YELLOW, 2),
        ("ESCALATE", Level.RED, 3),
    ]


def test_gap_resets_counters_not_alarm() -> None:  # D-ALM-04
    tr = run_engine(CSET, temp_series([31, 31, 31, 22, 22, 22, 22]), resets={4})
    assert [t.kind for t in tr] == ["RAISE"]  # ring cleared at the reset: 5, 6 only two fresh
    tr = run_engine(CSET, temp_series([31, 31, 31, 22, 22, 22, 22, 22]), resets={4})
    assert [(t.kind, t.trigger_seq) for t in tr] == [("RAISE", 2), ("RTN", 7)]


def test_reset_before_raise_restarts_count() -> None:
    assert run_engine(CSET, temp_series([31, 31, 31, 31]), resets={2}) == []


def test_null_sample_holds_level() -> None:  # EX-03
    tr = run_engine(CSET, temp_series([31, 31, 31, None, None, None, None]))
    assert [t.kind for t in tr] == ["RAISE"]


def test_missing_param_is_null() -> None:  # a param absent from the frame is a NULL sample
    frames: list[Frame] = [*temp_series([31, 31, 31]), (3, 0.3, {}), (4, 0.4, {}), (5, 0.5, {})]
    assert [t.kind for t in run_engine(CSET, frames)] == ["RAISE"]


def test_one_alarm_per_param_escalates() -> None:  # D-ALM-06, design 3.4 leak
    tr = run_engine(CSET, leak_frames(seconds=17, seed=1))
    kinds = [(t.kind, t.check_kind, t.severity) for t in tr]
    assert kinds[0] == ("RAISE", "roc", Level.YELLOW) and ("ESCALATE", "limit", Level.RED) in kinds
    assert sum(k[0] == "RAISE" for k in kinds) == 1
    assert {t.alarm_id for t in tr} == {tr[0].alarm_id}
    assert tr[0].level == "YELLOW_FALLING"
    esc = next(t for t in tr if t.kind == "ESCALATE")
    assert (esc.level, esc.trigger_check, esc.alert) == ("RED_LOW", "limit", True)


def n1_set() -> CompiledSet:
    lim = files()["limits"]
    old = b"defaults: {hysteresis_pct: 2.0, persistence: 3}"
    assert old in lim
    return compile_set(ld(files(limits=lim.replace(old, old.replace(b"3}", b"1}")))))


def _noise_cases() -> list[tuple[str, float, str]]:
    cases = []
    for param, entry in DB.limits.items():
        (lim,) = [c for c in entry.checks if isinstance(c, LimitCheck)]
        for bound in (lim.red_low, lim.yellow_low, lim.yellow_high, lim.red_high):
            if bound is not None:
                cases += [(param, bound, r) for r in ("pct_of_span", "pct_of_limit")]
    return cases


@pytest.mark.parametrize(("param", "bound", "reading"), _noise_cases())
def test_noise_one_alarm(param: str, bound: float, reading: str) -> None:  # ALM-02, R2-17
    (lim,) = [c for c in DB.limits[param].checks if isinstance(c, LimitCheck)]
    bounds = [
        b for b in (lim.red_low, lim.yellow_low, lim.yellow_high, lim.red_high) if b is not None
    ]
    amp = (max(bounds) - min(bounds) if reading == "pct_of_span" else abs(bound)) / 100
    rng = random.Random(f"{param}/{bound}/{reading}")
    frames: list[Frame] = [
        (i, i * PERIOD_S, {param: bound + rng.uniform(-amp, amp)}) for i in range(2000)
    ]
    kinds = [t.kind for t in run_engine(n1_set(), frames)]
    assert kinds.count("RAISE") == 1 and "RTN" not in kinds and "CLEAR" not in kinds


def test_nominal_noise_no_alarm() -> None:  # Review Focus 5: 10 min at design 3.3 sigma
    assert run_engine(CSET, nominal_frames(seconds=600, seed=7)) == []


def test_keys_and_ids() -> None:
    (t,) = run_engine(CSET, temp_series([31, 31, 31]))
    assert t.trigger_seq == 2 and t.trigger_t == at(0.2) and t.opened_at == at(0.2)
    assert t.key == transition_key(V, TEMP, EPOCH, 2, "RAISE")
    assert t.alarm_id == alarm_id_for(t.key)
    assert t.level == "RED_HIGH" and t.trigger_check == "limit" and t.check_kind == "limit"
    assert (t.vehicle, t.param, t.by_user, t.note, t.closed_at) == (V, TEMP, "system", None, None)


def test_step_is_pure() -> None:
    s0 = initial_state(CSET)
    s1, _ = step(s0, CSET, V, EPOCH, 0, at(0), PERIOD_S, {TEMP: 31.0}, False)
    assert s0 == initial_state(CSET) and s1 != s0


def test_unresolved_persistence_rejected() -> None:
    raw = LimitCheck(type="limit", red_low=1.0, red_high=2.0, hysteresis_pct=50.0)
    db = dataclasses.replace(DB, limits={TEMP: LimitEntry(checks=[raw])})
    with pytest.raises(ValueError, match="persistence not resolved"):
        compile_set(db)


class StuckCheck(CheckConfig):
    type: Literal["stuck"]


@dataclass(frozen=True)
class StuckState:
    pass


class Stuck:  # plug-in kind: RED whenever the value is exactly 0
    kind: ClassVar[str] = "stuck"
    Config: ClassVar[type[CheckConfig]] = StuckCheck
    State: ClassVar[type] = StuckState

    def __init__(self, cfg: StuckCheck) -> None:
        self.cfg = cfg

    def initial(self) -> object:
        return StuckState()

    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        return state, Reading(Level.RED if s.value == 0 else Level.NOMINAL, None)


def test_plugin_kind_dispatched_through_registry() -> None:  # ALM-07: no engine edit
    register(Stuck)
    try:
        lim = files()["limits"] + b"  bus_current_a_extra: {checks: [{type: stuck}]}\n"
        par = files()["parameters"] + b"  bus_current_a_extra: {unit: A, type: float}\n"
        cset = compile_set(ld(files(limits=lim, parameters=par)))
        frames: list[Frame] = [(i, i * PERIOD_S, {"bus_current_a_extra": 0.0}) for i in range(3)]
        (t,) = run_engine(cset, frames)
        assert (t.kind, t.check_kind, t.severity) == ("RAISE", "stuck", Level.RED)
    finally:
        CHECKS.pop("stuck")


def test_trigger_is_first_check_in_file_order() -> None:  # limit and roc reach YELLOW together
    p = "cabin_pressure_kpa"
    flat: list[Frame] = [(i, i * PERIOD_S, {p: 99.0}) for i in range(10)]
    drop: list[Frame] = [(i, i * PERIOD_S, {p: 97.9 - 0.05 * (i - 10)}) for i in range(10, 13)]
    (t,) = run_engine(CSET, [*flat, *drop])
    assert (t.kind, t.trigger_seq, t.check_kind, t.level) == ("RAISE", 12, "limit", "YELLOW_LOW")


def test_deescalate_keeps_the_last_side() -> None:  # ring (Y, N, N): YELLOW, current reading N
    tr = run_engine(CSET, temp_series([31, 31, 31, 28, 22, 22, 22]))
    assert [(t.kind, t.level) for t in tr] == [
        ("RAISE", "RED_HIGH"),
        ("DEESCALATE", "YELLOW_HIGH"),
        ("RTN", None),
    ]
