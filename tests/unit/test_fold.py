import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from gs.alarms import state
from gs.alarms.engine import compile_set, initial_state
from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.missiondb import MissionDb, load, load_dir
from gs.ingest.events import applied_key, details_json, transition_key
from gs.ingest.fold import (
    VehicleState,
    dump_vehicle,
    fold,
    load_vehicle,
    state_version,
)
from gs.ingest.link import FrozenState, LinkState
from gs.ingest.seq import SeqState
from gs.simulator.nominals import NOMINALS
from tests.support.mission import MISSION_DIR
from tests.support.recompute import empty_state

V = "NYX-TA1"
T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
MDB = load_dir(MISSION_DIR)
CSET = compile_set(MDB)
SHA = CSET.set_sha256
ES = initial_state(CSET)
PARAMS = MDB.param_names


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
    vs, out = fold(vs or empty_state(CSET), V, frames, MDB, CSET)
    return vs, [e.kind for e in out.events]


def test_dup_frames_not_new() -> None:
    frames = [fr(0, 0.0), fr(1, 0.1), fr(1, 0.1), fr(2, 0.2)]
    _, out = fold(empty_state(CSET), V, frames, MDB, CSET)
    assert [f.seq for f in out.new_frames] == [0, 1, 2] and out.dup_or_late == 1
    assert out.events == ()


def test_gap_reveals_los_aos_with_lost() -> None:
    frames = [fr(i, i / 10) for i in range(10)] + [fr(i, i / 10) for i in range(110, 120)]
    vs, out = fold(empty_state(CSET), V, frames, MDB, CSET)
    assert [e.kind for e in out.events] == ["GAP", "LOS", "AOS"]
    gap, _, aos = out.events
    assert gap.details["lost"] == 100 and aos.details["lost_frames"] == 100
    assert vs.seq is not None and vs.seq.last_seq == 119 and not vs.link.los_open


def test_reset_aos_lost_unknown() -> None:
    frames = [fr(i, i / 10) for i in range(100)] + [fr(0, 15.0)]
    _, out = fold(empty_state(CSET), V, frames, MDB, CSET)
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
    ES,
)


def test_dump_load_roundtrip() -> None:
    assert load_vehicle(S, dump_vehicle(VS), SHA, ES) == VS
    via_json = json.loads(details_json(dump_vehicle(VS)))  # what jsonb hands back
    assert load_vehicle(S, via_json, SHA, ES) == VS
    assert load_vehicle(S, None, SHA, ES) == VehicleState(S, LinkState(), FrozenState(), SHA, ES)


def test_frozen_run_survives_reload() -> None:
    frames = [fr(i, i / 10, vary=False) for i in range(25)]
    vs, _ = run(frames[:15])
    vs = load_vehicle(vs.seq, json.loads(json.dumps(dump_vehicle(vs))), SHA, vs.engine)
    _, out = fold(vs, V, frames[15:], MDB, CSET)
    ((f,),) = [out.events]
    assert (
        f.kind == "FROZEN"
        and f.details["first_seq"] == 0
        and f.t_onboard == T0 + timedelta(seconds=2)
    )


def test_state_version_stable_positive() -> None:
    assert state_version() == state_version() and 0 <= state_version() < 2**31


def test_seq_jump_aos_lost_unknown() -> None:  # G13, D-ING-16: counter jump ≠ lost frames
    frames = [fr(i, i / 10) for i in range(10)] + [fr(1010, 0.9 + 10.0)]
    _, out = fold(empty_state(CSET), V, frames, MDB, CSET)
    assert [e.kind for e in out.events] == ["SEQ_JUMP", "LOS", "AOS"]
    assert out.events[0].details["lost"] == 1000 and out.events[2].details["lost_frames"] is None


def test_in_order_aos_lost_zero() -> None:  # stale but in order: nothing lost
    frames = [fr(0, 0.0), fr(1, 5.0)]
    _, out = fold(empty_state(CSET), V, frames, MDB, CSET)
    assert [e.kind for e in out.events] == ["LOS", "AOS"] and out.events[1].details[
        "lost_frames"
    ] == 0


def nf(seq: int, t: float, temp: float = 22.0) -> FrameHeader:
    """Every parameter at its design nominal, cabin_temp_c at `temp`."""
    vals = {p: nom for p, (nom, _) in NOMINALS.items()} | {"cabin_temp_c": temp}
    return MODEL(vehicle=V, seq=seq, t_onboard=T0 + timedelta(seconds=t), mode="TEST",
                 last_cmd_id=None, **vals)  # fmt: skip


def raises(frames: list[FrameHeader]) -> list[tuple[str, int | None]]:
    _, out = fold(empty_state(CSET), V, frames, MDB, CSET)
    return [(t.kind, t.trigger_seq) for t in out.transitions]


def test_engine_raises_on_new_frames_only() -> None:  # D-ING-17: duplicates and LATE never step
    frames = [nf(i, i / 10, 22.0 if i < 5 else 31.0) for i in range(8)]
    assert raises(frames) == [("RAISE", 7)]
    dup = [*frames[:6], frames[5], nf(3, 0.05, 31.0), *frames[6:]]  # a duplicate + a LATE frame
    assert raises(dup) == [("RAISE", 7)]


def test_gap_restarts_persistence() -> None:  # D-ALM-04: two bad frames + GAP + one ≠ three
    pre = [nf(i, i / 10, 22.0 if i < 5 else 31.0) for i in range(7)]
    post = [nf(i, i / 10, 31.0) for i in range(20, 23)]
    assert raises(pre + post) == [("RAISE", 22)]


def test_stale_restarts_persistence() -> None:  # in order, but > 2 s of vehicle time
    pre = [nf(i, i / 10, 22.0 if i < 5 else 31.0) for i in range(7)]
    post = [nf(i, 5.0 + i / 10, 31.0) for i in range(7, 10)]
    assert raises(pre + post) == [("RAISE", 9)]


def test_engine_snapshot_continues_exactly() -> None:  # D-ALM-10 at the fold level
    frames = [nf(i, i / 10, 22.0 + i * 0.1) for i in range(120)]  # ramp through yellow, red
    whole = fold(empty_state(CSET), V, frames, MDB, CSET)[1].transitions
    vs, a = fold(empty_state(CSET), V, frames[:58], MDB, CSET)
    obj = json.loads(json.dumps(dump_vehicle(vs)))
    vs = load_vehicle(vs.seq, obj, SHA, state.load(obj["engine"], CSET))
    _, b = fold(vs, V, frames[58:], MDB, CSET)
    assert len(whole) >= 2 and a.transitions + b.transitions == whole


def shipped_raw() -> list[bytes]:
    return [
        (MISSION_DIR / n).read_bytes() for n in ("limits.yaml", "commands.yaml", "parameters.yaml")
    ]


def unchecked_temp() -> MissionDb:
    """The shipped set with cabin_temp_c's limit check removed."""
    raw = shipped_raw()
    line = next(ln for ln in raw[0].decode().splitlines() if "cabin_temp_c" in ln)
    raw[0] = raw[0].replace(line.encode(), b"  cabin_temp_c: {checks: []}")
    return load(*raw)


def test_first_frame_applies_the_set() -> None:  # no engine_state row yet: LIMITS_APPLIED
    fresh = replace(empty_state(CSET), set_sha256="")
    vs, out = fold(fresh, V, [nf(5, 0.5), nf(6, 0.6)], MDB, CSET)
    (ev,) = out.events
    t5 = T0 + timedelta(seconds=0.5)
    assert (ev.kind, ev.key, ev.t_onboard) == ("LIMITS_APPLIED", applied_key(V, t5, 5), t5)
    assert ev.details == {
        "first_seq": 5, "set_sha256": MDB.set_sha256, "limits_sha256": MDB.limits_sha256,
        "commands_sha256": MDB.commands_sha256, "parameters_sha256": MDB.parameters_sha256,
    }  # fmt: skip
    assert vs.set_sha256 == SHA
    _, out = fold(vs, V, [nf(7, 0.7)], MDB, CSET)
    assert out.events == ()  # applied once


def test_new_set_switches_before_the_frame_is_classified() -> None:  # D-ALM-12, R8 7-8
    vs, out = fold(empty_state(CSET), V, [nf(i, i / 10, 31.0) for i in range(3)], MDB, CSET)
    assert [t.kind for t in out.transitions] == ["RAISE"]
    mdb = unchecked_temp()
    new = compile_set(mdb)
    with pytest.raises(ValueError, match="built with"):
        fold(vs, V, [nf(3, 0.3, 31.0)], mdb, new)
    dup, out = fold(vs, V, [nf(2, 0.2, 31.0)], mdb, new, CSET)  # no new frame: not applied yet
    assert out.events == () and dup.set_sha256 == SHA
    vs, out = fold(vs, V, [nf(3, 0.3, 31.0)], mdb, new, CSET)
    assert [e.kind for e in out.events] == ["LIMITS_APPLIED"]
    ((kind, seq, note),) = [(t.kind, t.trigger_seq, t.note) for t in out.transitions]
    assert (kind, seq, note) == ("RTN", 3, "LIMITS_CHANGED") and vs.set_sha256 == new.set_sha256


def test_seq_reset_restarts_persistence() -> None:  # reboot < 2 s later: SEQ_RESET, no GAP/STALE
    pre = [nf(i, i / 10, 22.0 if i < 5 else 31.0) for i in range(7)]
    post = [nf(i, 0.7 + i / 10, 31.0) for i in range(3)]  # seq back to 0, 100 ms later
    vs, _ = fold(empty_state(CSET), V, pre, MDB, CSET)
    _, out = fold(vs, V, post, MDB, CSET)  # a batch that starts in the old epoch
    assert [e.kind for e in out.events] == ["SEQ_RESET"]
    (t,) = out.transitions
    epoch = T0 + timedelta(seconds=0.7)  # the new boot's epoch
    assert (t.kind, t.trigger_seq) == ("RAISE", 2)
    assert t.key == transition_key(V, "cabin_temp_c", epoch, 2, "RAISE")


def test_no_snapshot_rows_reseeded_for_the_applied_set() -> None:  # set changed before frame 1
    vs, _ = fold(empty_state(CSET), V, [nf(i, i / 10, 31.0) for i in range(3)], MDB, CSET)
    fresh = replace(empty_state(CSET), set_sha256="", engine=vs.engine)  # as load_state builds
    mdb = unchecked_temp()
    vs, out = fold(fresh, V, [nf(0, 9.0, 31.0)], mdb, compile_set(mdb))
    assert [e.kind for e in out.events] == ["LIMITS_APPLIED"] and out.transitions == ()
    assert "cabin_temp_c" in vs.engine.alarms and ("cabin_temp_c", "limit") not in vs.engine.slots


def test_reboot_frame_keyed_in_new_epoch() -> None:  # N = 1: the revealing frame itself raises
    raw = shipped_raw()
    raw[0] = raw[0].replace(b"persistence: 3", b"persistence: 1")
    mdb = load(*raw)
    cset = compile_set(mdb)
    vs, _ = fold(empty_state(cset), V, [nf(i, i / 10) for i in range(7)], mdb, cset)
    _, out = fold(vs, V, [nf(0, 0.7, 31.0)], mdb, cset)
    (t,) = out.transitions
    epoch = T0 + timedelta(seconds=0.7)
    assert t.key == transition_key(V, "cabin_temp_c", epoch, 0, "RAISE")


def test_set_applied_at_the_first_new_frame_of_a_batch() -> None:  # not at a duplicate
    vs, _ = fold(empty_state(CSET), V, [nf(i, i / 10) for i in range(3)], MDB, CSET)
    mdb = unchecked_temp()
    _, out = fold(vs, V, [nf(2, 0.2), nf(3, 0.3)], mdb, compile_set(mdb), CSET)
    (ev,) = out.events
    assert ev.details["first_seq"] == 3 and ev.key.endswith("/3")


def test_set_that_adds_a_check_classifies_the_switch_frame() -> None:  # switch, then step
    mdb = unchecked_temp()
    old = compile_set(mdb)
    vs, _ = fold(empty_state(old), V, [nf(i, i / 10) for i in range(3)], mdb, old)
    _, out = fold(vs, V, [nf(i, i / 10, 31.0) for i in range(3, 6)], MDB, CSET, old)
    assert [(t.kind, t.trigger_seq) for t in out.transitions] == [("RAISE", 5)]
