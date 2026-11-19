"""Re-run the pure fold over recorded frames and ACKs: the keys step() derives must be the keys
that were stored (D-REP-05)."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

import pyarrow as pa

from gs.alarms import engine
from gs.alarms.engine import compile_set
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import MissionDb
from gs.ingest.fold import VehicleState, fold
from gs.ingest.link import FrozenState, LinkState


def empty_state(cset: engine.CompiledSet) -> VehicleState:
    return VehicleState(
        None, LinkState(), FrozenState(), cset.set_sha256, engine.initial_state(cset)
    )


def _frame(row: dict[str, Any]) -> FrameHeader:
    cmd = row["last_cmd_id"]
    return FrameHeader.model_validate(row | {"last_cmd_id": UUID(cmd) if cmd else None})


def recompute_keys(
    frames: Sequence[pa.Table],
    acks: Sequence[Mapping[str, Any]],
    sets: Mapping[str, MissionDb],
    vehicle: str,
    applied: Sequence[tuple[int, str]],
) -> tuple[list[str], list[str]]:
    """`applied` = the recorded LIMITS_APPLIED (first_seq, set_sha256), the first being the set
    the vehicle started with. Each ACK (rows of alarm_events kind ACK) is applied right after the
    frame (after_epoch, after_seq), i.e. before the next one, with its recorded key, user, note
    and time. Returns (transition keys in order, keys of the events the fold produced)."""
    compiled = {sha: compile_set(m) for sha, m in sets.items()}
    switch = dict(applied)
    vs = replace(empty_state(compiled[applied[0][1]]), set_sha256="")
    pending = list(acks)
    transitions: list[str] = []
    events: list[str] = []
    for table in frames:
        for row in table.to_pylist():
            f = _frame(row)
            sha = switch.get(f.seq, vs.set_sha256)
            old = compiled.get(vs.set_sha256)
            vs, out = fold(vs, vehicle, [f], sets[sha], compiled[sha], old)
            transitions += [t.key for t in out.transitions]
            events += [e.key for e in out.events]
            assert vs.seq is not None
            for a in [
                a for a in pending if (a["after_epoch"], a["after_seq"]) == (vs.seq.epoch, f.seq)
            ]:
                pending.remove(a)
                es, tr = engine.ack(
                    vs.engine, vehicle, a["param"], a["by_user"], a["note"], a["key"],
                    a["after_t_onboard"],
                )  # fmt: skip
                vs = replace(vs, engine=es)
                transitions += [tr.key] if tr else []
    assert not pending, f"ACKs not applied: {[a['key'] for a in pending]}"
    return transitions, events
