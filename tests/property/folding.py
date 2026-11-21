"""Batch folds for P3 and P4: the real `fold` over frames, cut into batches, with a mission-db
change and operator ACKs at fixed frames and, optionally, a JSON snapshot / restore at each cut."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from gs.alarms.contract import Transition
from gs.alarms.engine import CompiledSet, ack, compile_set, initial_state
from gs.alarms.state import load
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import MissionDb
from gs.ingest.compute import Computed
from gs.ingest.fold import VehicleState, dump_vehicle, fold, load_vehicle
from gs.ingest.link import FrozenState, LinkState
from tests.property.strategies import Run
from tests.support.alarms import V, at


@dataclass(frozen=True)
class Played:
    transitions: list[Transition]
    end: VehicleState
    computed: list[tuple[bool, Computed]]  # (late, computed) per stream entry, in order


@dataclass(frozen=True)
class Frame:
    header: FrameHeader
    late: bool  # a redelivery of an earlier frame with other values (EX-13)


def headers(
    run: Run, late: Sequence[tuple[int, int]] = (), modes: Sequence[str] = ()
) -> list[Frame]:
    """The run's frames as stream entries, frame n in `modes[n]` (TEST when none given); each
    `(i, gap)` of `late` re-delivers frame i with absurd values `gap` frames later."""
    out: list[Frame] = []
    mode = (*modes, *["TEST"] * len(run.frames))
    for n, (seq, t, values, _) in enumerate(run.frames):
        out.append(Frame(_header(seq, t, values, mode[n]), False))
        out += [
            Frame(
                _header(run.frames[i][0], run.frames[i][1], {p: 1e9 for p in values}, mode[i]),
                True,
            )
            for i, gap in late
            if i <= n == min(i + gap, len(run.frames) - 1)
        ]
    return out


def _header(seq: int, t: float, values: dict[str, float | None], mode: str) -> FrameHeader:
    fixed = {"vehicle": V, "seq": seq, "t_onboard": at(t), "mode": mode, "last_cmd_id": None}
    return FrameHeader.model_validate({**fixed, **values})


def _snapshot(vs: VehicleState, csets: dict[str, CompiledSet]) -> VehicleState:
    """Through JSON, as the owner stores it, loaded with the set it was built with (G18)."""
    obj: dict[str, Any] = json.loads(json.dumps(dump_vehicle(vs)))
    es = load(obj["engine"], csets[vs.set_sha256])
    again = load_vehicle(vs.seq, obj, vs.set_sha256, es)
    assert dump_vehicle(again) == dump_vehicle(vs)
    return again


def play(
    frames: Sequence[Frame],
    mdbs: tuple[MissionDb, MissionDb | None],
    switch_at: int,
    acks: frozenset[int],
    cuts: frozenset[int],
    restore: bool = False,
) -> Played:
    """Fold `frames` in batches. Boundaries: `cuts`, every frame whose seq is in `acks` (the
    operator's ACK settles between batches, D-ALM-14) and the first real frame from index
    `switch_at` (a set applies at a batch start). Returns every transition and the end state."""
    sets = [compile_set(m) for m in mdbs if m is not None]
    csets = {c.set_sha256: c for c in sets}
    real = [i for i, f in enumerate(frames) if not f.late]
    switch = real[switch_at] if mdbs[1] is not None and switch_at < len(real) else len(frames)
    bounds = sorted(
        {0, len(frames), *cuts, switch} | {i + 1 for i in real if frames[i].header.seq in acks}
    )
    vs = VehicleState(None, LinkState(), FrozenState(), "", initial_state(sets[0]))
    out: list[Transition] = []
    computed: list[tuple[bool, Computed]] = []
    prev: CompiledSet | None = None
    for a, b in zip(bounds, bounds[1:], strict=False):
        if a >= len(frames) or a == b:
            continue
        mdb, cset = (
            (mdbs[1], sets[1]) if mdbs[1] is not None and a >= switch else (mdbs[0], sets[0])
        )
        vs, o = fold(vs, V, [f.header for f in frames[a:b]], mdb, cset, prev)
        prev = cset
        out += o.transitions
        computed += zip((f.late for f in frames[a:b]), o.computed, strict=True)
        last = frames[b - 1].header
        if not frames[b - 1].late and last.seq in acks:
            es = vs.engine
            for param in sorted(es.alarms):
                es, tr = ack(es, V, param, "op", "note", f"ack-{last.seq}-{param}", last.t_onboard)
                out += [tr] if tr else []
            vs = replace(vs, engine=es)
        if restore and b < len(frames):
            vs = _snapshot(vs, csets)
    return Played(out, vs, computed)
