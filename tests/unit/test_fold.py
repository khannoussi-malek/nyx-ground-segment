import json
from datetime import UTC, datetime, timedelta

from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.events import details_json
from gs.ingest.fold import (
    VehicleState,
    dump_vehicle,
    empty_state,
    fold,
    load_vehicle,
    state_version,
)
from gs.ingest.link import FrozenState, LinkState
from gs.ingest.seq import SeqState
from tests.support.mission import MISSION_DIR

V = "NYX-TA1"
T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
SHA = "a" * 64
PARAMS = load_dir(MISSION_DIR).param_names
MODEL = build_frame_model(PARAMS)


def fr(seq: int, t: float, base: float = 1.0, vary: bool = True) -> FrameHeader:
    vals = {p: base + (seq * 0.001 if vary else 0.0) + i for i, p in enumerate(PARAMS)}
    return MODEL(
        vehicle=V, seq=seq, t_onboard=T0 + timedelta(seconds=t), mode="TEST", last_cmd_id=None,
        **vals,
    )  # fmt: skip


def run(
    frames: list[FrameHeader], vs: VehicleState | None = None
) -> tuple[VehicleState, list[str]]:
    vs, out = fold(vs or empty_state(SHA), V, frames, PARAMS)
    return vs, [e.kind for e in out.events]


def test_dup_frames_not_new() -> None:
    frames = [fr(0, 0.0), fr(1, 0.1), fr(1, 0.1), fr(2, 0.2)]
    _, out = fold(empty_state(SHA), V, frames, PARAMS)
    assert [f.seq for f in out.new_frames] == [0, 1, 2] and out.dup_or_late == 1
    assert out.events == ()


def test_gap_reveals_los_aos_with_lost() -> None:
    frames = [fr(i, i / 10) for i in range(10)] + [fr(i, i / 10) for i in range(110, 120)]
    vs, out = fold(empty_state(SHA), V, frames, PARAMS)
    assert [e.kind for e in out.events] == ["GAP", "LOS", "AOS"]
    gap, _, aos = out.events
    assert gap.details["lost"] == 100 and aos.details["lost_frames"] == 100
    assert vs.seq is not None and vs.seq.last_seq == 119 and not vs.link.los_open


def test_reset_aos_lost_unknown() -> None:
    frames = [fr(i, i / 10) for i in range(100)] + [fr(0, 15.0)]
    _, out = fold(empty_state(SHA), V, frames, PARAMS)
    assert [e.kind for e in out.events] == ["SEQ_RESET", "LOS", "AOS"]
    assert out.events[2].details["lost_frames"] is None


def test_frozen_on_new_frames_only() -> None:
    frames = [fr(i, i / 10, vary=False) for i in range(25)]
    _, kinds = run(frames)
    assert kinds == ["FROZEN"]
    # duplicates of an equal run do not advance it
    vs, kinds = run([fr(i, i / 10, vary=False) for i in range(10)])
    vs, kinds = run([fr(i, i / 10, vary=False) for i in range(10)] * 3, vs)
    assert kinds == [] and vs.frozen.t_from == T0


def test_reset_restarts_frozen() -> None:  # G11: no FROZEN run spans epochs
    old = [fr(i, i / 10 - 3.0, vary=False) for i in range(30, 45)]  # t 0..1.4, equal
    new = [fr(i, 2.0 + i / 10, vary=False) for i in range(0, 15)]  # reset at t 2.0, same values
    vs, kinds = run(old + new)
    assert kinds == ["SEQ_RESET"]  # 3.4 s of equal values, but no run within one epoch ≥ 2 s
    assert vs.frozen.first_seq == 0 and vs.frozen.t_from == T0 + timedelta(seconds=2.0)
    vs, kinds = run([fr(i, 2.0 + i / 10, vary=False) for i in range(15, 25)], vs)
    assert kinds == ["FROZEN"]


def test_anomaly_restarts_frozen() -> None:
    old = [fr(i, i / 10, vary=False) for i in range(500, 515)]
    vs, kinds = run(old + [fr(100, 52.0, vary=False)])  # 0.6 s on, equal values
    assert kinds == ["SEQ_ANOMALY"] and vs.frozen.first_seq == 100


S = SeqState(epoch=T0, last_seq=7, last_t=T0, period_s=0.1)
VS = VehicleState(
    S,
    LinkState(True, T0, 7),
    FrozenState((1.5, None, -0.0, 3.25), 3, T0 + timedelta(seconds=0.3), False),
    SHA,
)


def test_dump_load_roundtrip() -> None:
    assert load_vehicle(S, dump_vehicle(VS), SHA) == VS
    via_json = json.loads(details_json(dump_vehicle(VS)))  # what jsonb hands back
    assert load_vehicle(S, via_json, SHA) == VS
    assert load_vehicle(S, None, SHA) == VehicleState(S, LinkState(), FrozenState(), SHA)


def test_frozen_run_survives_reload() -> None:
    frames = [fr(i, i / 10, vary=False) for i in range(25)]
    vs, _ = run(frames[:15])
    vs = load_vehicle(vs.seq, json.loads(json.dumps(dump_vehicle(vs))), SHA)
    _, out = fold(vs, V, frames[15:], PARAMS)
    ((f,),) = [out.events]
    assert (
        f.kind == "FROZEN"
        and f.details["first_seq"] == 0
        and f.t_onboard == T0 + timedelta(seconds=2)
    )


def test_state_version_stable_positive() -> None:
    assert state_version() == state_version() and 0 <= state_version() < 2**31
