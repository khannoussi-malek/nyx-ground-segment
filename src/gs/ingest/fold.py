"""Per-vehicle fold over one batch: sequence tracker, LOS/AOS, FROZEN, alarm engine (pure)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from gs.alarms import engine
from gs.alarms.contract import Transition
from gs.alarms.engine import CompiledSet, EngineState
from gs.alarms.state import dump, shape_version
from gs.common.interfaces import FrameHeader
from gs.common.time import iso_z
from gs.ingest import link, seq
from gs.ingest.events import GroundEvent
from gs.ingest.link import FrozenState, LinkState
from gs.ingest.seq import SeqState


@dataclass(frozen=True)
class VehicleState:
    seq: SeqState | None
    link: LinkState
    frozen: FrozenState
    set_sha256: str  # the mission-db set `engine` was built with
    engine: EngineState


@dataclass(frozen=True)
class Outcome:
    new_frames: tuple[FrameHeader, ...]
    events: tuple[GroundEvent, ...]
    dup_or_late: int
    transitions: tuple[Transition, ...] = ()


def state_version() -> int:
    """engine_state.schema_version: every dataclass persisted in its `state` jsonb (D-ALM-11)."""
    return shape_version(LinkState, FrozenState)


def empty_state(cset: CompiledSet) -> VehicleState:
    return VehicleState(
        None, LinkState(), FrozenState(), cset.set_sha256, engine.initial_state(cset)
    )


def fold(
    vs: VehicleState,
    vehicle: str,
    frames: Sequence[FrameHeader],
    params: Sequence[str],
    cset: CompiledSet,
) -> tuple[VehicleState, Outcome]:
    """`cset` must be the set `vs.engine` was built with (vs.set_sha256)."""
    s, ls, fs, es = vs.seq, vs.link, vs.frozen, vs.engine
    new: list[FrameHeader] = []
    events: list[GroundEvent] = []
    transitions: list[Transition] = []
    dup = 0
    for f in frames:  # stream order
        prev = s
        s, k, ev = seq.step(prev, vehicle, f.seq, f.t_onboard)
        if not seq.is_new(k):
            dup += 1
            continue
        events += ev
        # G13 / D-ING-16: only a GAP counts lost frames; a counter jump or reset is of unknown size
        lost = ev[0].details["lost"] if k == "GAP" else 0 if k in ("FIRST", "IN_ORDER") else None
        ls, lev, stale = link.on_frame(ls, vehicle, prev, f.t_onboard, lost)
        events += lev
        if k in ("SEQ_RESET", "SEQ_ANOMALY"):
            fs = FrozenState()  # G11: no FROZEN run spans epochs
        values = {p: getattr(f, p) for p in params}
        fs, fev = link.frozen_step(fs, vehicle, s.epoch, f.seq, f.t_onboard, tuple(values.values()))
        if fev is not None:
            events.append(fev)
        # D-ALM-04: GAP, SEQ_RESET and data-STALE restart rings and ROC windows
        reset = stale or any(e.kind in ("GAP", "SEQ_RESET") for e in ev)
        es, trs = engine.step(
            es, cset, vehicle, s.epoch, f.seq, f.t_onboard, s.period_s, values, reset
        )
        transitions += trs
        new.append(f)
    out = Outcome(tuple(new), tuple(events), dup, tuple(transitions))
    return replace(vs, seq=s, link=ls, frozen=fs, engine=es), out


def _t(d: datetime | None) -> str | None:
    return None if d is None else iso_z(d)


def _parse(x: str | None) -> datetime | None:
    return None if x is None else datetime.fromisoformat(x)


def dump_vehicle(vs: VehicleState) -> dict[str, Any]:
    """JSON-ready (jsonb): datetimes as iso_z strings, tuples as lists."""
    ls, fs = vs.link, vs.frozen
    return {
        "engine": dump(vs.engine),
        "link": {"los_open": ls.los_open, "epoch": _t(ls.epoch), "last_seq": ls.last_seq},
        "frozen": {
            "values": None if fs.values is None else list(fs.values),
            "first_seq": fs.first_seq,
            "t_from": _t(fs.t_from),
            "reported": fs.reported,
        },
    }


def load_vehicle(
    s: SeqState | None, obj: Mapping[str, Any] | None, set_sha256: str, es: EngineState
) -> VehicleState:
    """Link and FROZEN state from `obj`; the engine part is loaded by the caller (set-aware)."""
    if obj is None:
        return VehicleState(s, LinkState(), FrozenState(), set_sha256, es)
    lk, fz = obj["link"], obj["frozen"]
    ls = LinkState(lk["los_open"], _parse(lk["epoch"]), lk["last_seq"])
    values = None if fz["values"] is None else tuple(fz["values"])
    fs = FrozenState(values, fz["first_seq"], _parse(fz["t_from"]), fz["reported"])
    return VehicleState(s, ls, fs, set_sha256, es)
