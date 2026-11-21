"""rate_of derived parameters: window state in compute(), the snapshot, late frames (D-EXT-05)."""

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta

import pytest

from gs.alarms.checks.roc import RocState
from gs.alarms.engine import compile_set
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import MissionDb
from gs.ingest.compute import Computed, compute
from gs.ingest.fold import dump_vehicle, fold, load_vehicle
from tests.support.recompute import empty_state
from tests.unit.test_derived_values import T0, frame, with_derived

L = "leak"
MDB: MissionDb = with_derived(
    f"  {L}: {{unit: kPa/s, rate_of: cabin_pressure_kpa, window_s: 1.0}}\n"
)
CSET = compile_set(MDB)
P = "cabin_pressure_kpa"


def fr(i: int, v: float | None) -> FrameHeader:
    f = frame(MDB, **{P: v})
    return f.model_copy(update={"seq": i, "t_onboard": T0 + timedelta(seconds=i / 10)})


def feed(
    vals: Sequence[float | None], w: Mapping[str, RocState] | None = None, start: int = 0
) -> tuple[list[Computed], Mapping[str, RocState]]:
    out, w = [], w or {}
    for i, v in enumerate(vals, start):
        c, w = compute(MDB, fr(i, v), w, False)
        out.append(c)
    return out, w


def test_equals_roc_slope_on_same_window() -> None:
    vals = [101.3 - 0.4 * i / 10 + (0.01 if i % 3 else -0.02) for i in range(30)]
    cs, _ = feed(vals)
    ts = [i / 10 for i in range(30)]
    got = cs[-1].values[L]
    want = statistics.linear_regression(ts[-11:], vals[-11:]).slope  # t >= last - 1.0: 11 points
    assert got is not None and abs(got - want) < 1e-9
    assert abs(got - -0.4) < 0.1


def test_under_80_percent_samples_is_null() -> None:
    cs, _ = feed([101.3] * 5)  # 0.4 s of a 1 s window
    assert cs[-1].values[L] is None and (L, "samples") in cs[-1].nulls


def test_window_resets_on_null_input() -> None:  # R23 M4
    _, w = feed([101.0 - i for i in range(15)])
    cs, w = feed([None], w, 15)
    assert cs[0].values[L] is None and (L, "missing") in cs[0].nulls and w[L] == RocState((), ())
    cs, _ = feed([50.0] * 5, w, 16)  # not enough samples after the reset: no stale slope
    assert cs[-1].values[L] is None


def test_late_frame_null_window_unchanged() -> None:
    _, w = feed([101.0 - i for i in range(15)])
    c, w2 = compute(MDB, fr(3, 90.0), w, True)
    assert c.values[L] is None and c.nulls == ((L, "late"),) and w2 == w


def test_reset_flag_restarts_window() -> None:
    _, w = feed([101.0 - i for i in range(15)])
    c, w2 = compute(MDB, fr(15, 80.0), w, False, reset=True)
    assert c.values[L] is None and len(w2[L].t) == 1


def test_slope_error_is_null_and_window_advances(monkeypatch: pytest.MonkeyPatch) -> None:
    vals = [101.0 - 0.4 * i / 10 for i in range(20)]
    _, ref_w = feed(vals[:15])
    ref_cs, _ = feed(vals)

    def boom(*_a: object) -> float | None:
        raise ValueError("forced")

    monkeypatch.setattr("gs.ingest.compute.slope", boom)
    cs, w = feed(vals[:15])  # EX-11: every frame NULL "error", the window still moves on
    assert all(c.values[L] is None and c.nulls == ((L, "error"),) for c in cs)
    assert w == ref_w
    monkeypatch.undo()
    cs2, _ = feed(vals[15:], w, 15)  # the next good sample is handled normally again
    got, want = cs2[-1].values[L], ref_cs[-1].values[L]
    assert got is not None and want is not None and abs(got - want) < 1e-9


def test_nonfinite_slope_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gs.ingest.compute.slope", lambda *_a: float("inf"))
    cs, _ = feed([1.0, 2.0, 3.0])
    assert all(c.values[L] is None and c.nulls == ((L, "nonfinite"),) for c in cs)


def test_input_window_untouched() -> None:
    _, w = feed([1.0, 2.0])
    before = dict(w)
    compute(MDB, fr(2, 3.0), w, False)
    assert w == before


def folded(vals: list[float], vs=None):  # type: ignore[no-untyped-def]
    return fold(vs or empty_state(CSET), "NYX-TA1", [fr(i, v) for i, v in enumerate(vals)],
                MDB, CSET)  # fmt: skip


def test_snapshot_round_trip() -> None:
    vals = [101.0 - 0.4 * i / 10 for i in range(30)]
    whole, out = folded(vals)
    part, _ = folded(vals[:20])
    obj = json.loads(json.dumps(dump_vehicle(part)))
    assert obj["derived"][L]["t"]  # persisted under derived:<name>
    again = load_vehicle(part.seq, obj, part.set_sha256, part.engine)
    assert again.derived == part.derived
    rest = [fr(i, v) for i, v in enumerate(vals[20:], 20)]
    end, out2 = fold(again, "NYX-TA1", rest, MDB, CSET)
    assert (
        end.derived == whole.derived and out2.computed[-1].values[L] == out.computed[-1].values[L]
    )
    assert out2.computed[-1].values[L] is not None


def test_snapshot_before_this_change_loads_default() -> None:
    part, _ = folded([101.0] * 5)
    obj = json.loads(json.dumps(dump_vehicle(part)))
    del obj["derived"]  # what an older worker saved
    assert load_vehicle(part.seq, obj, part.set_sha256, part.engine).derived == {}
    assert replace(part, derived={}).derived == {}


def test_fold_late_frame_gets_null_and_keeps_window() -> None:
    vs, _ = folded([101.0 - i for i in range(15)])
    vs2, out = fold(vs, "NYX-TA1", [fr(3, 7.0)], MDB, CSET)  # seq 3 again: DUP_OR_LATE
    assert out.dup_or_late == 1 and out.computed[0].values[L] is None
    assert out.computed[0].nulls == ((L, "late"),) and vs2.derived == vs.derived
