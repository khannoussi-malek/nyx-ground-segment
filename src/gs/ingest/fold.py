"""Per-vehicle fold over one batch: sequence tracker, LOS/AOS, FROZEN (pure)."""

import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any

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
    set_sha256: str


@dataclass(frozen=True)
class Outcome:
    new_frames: tuple[FrameHeader, ...]
    events: tuple[GroundEvent, ...]
    dup_or_late: int


def state_version() -> int:
    # placeholder: gs.alarms.state.shape_version — Task 16 swaps in
    # shape_version(LinkState, FrozenState) here; callers stay unchanged
    sig = [
        (c.__qualname__, f.name, str(f.type))
        for c in (SeqState, LinkState, FrozenState)
        for f in fields(c)
    ]
    return zlib.crc32(repr(sorted(sig)).encode()) & 0x7FFFFFFF


def empty_state(set_sha256: str) -> VehicleState:
    return VehicleState(None, LinkState(), FrozenState(), set_sha256)


def fold(
    vs: VehicleState, vehicle: str, frames: Sequence[FrameHeader], params: Sequence[str]
) -> tuple[VehicleState, Outcome]:
    s, ls, fs = vs.seq, vs.link, vs.frozen
    new: list[FrameHeader] = []
    events: list[GroundEvent] = []
    dup = 0
    for f in frames:  # stream order
        prev = s
        s, k, ev = seq.step(prev, vehicle, f.seq, f.t_onboard)
        if not seq.is_new(k):
            dup += 1
            continue
        events += ev
        lost = (
            None if k in ("SEQ_RESET", "SEQ_ANOMALY") else next((e.details["lost"] for e in ev), 0)
        )
        ls, lev, _stale = link.on_frame(
            ls, vehicle, prev, f.t_onboard, lost
        )  # stale → engine reset, Task 16
        events += lev
        if k in ("SEQ_RESET", "SEQ_ANOMALY"):
            fs = FrozenState()  # G11: no FROZEN run spans epochs
        values = tuple(getattr(f, p) for p in params)
        fs, fev = link.frozen_step(fs, vehicle, s.epoch, f.seq, f.t_onboard, values)
        if fev is not None:
            events.append(fev)
        new.append(f)
    return VehicleState(s, ls, fs, vs.set_sha256), Outcome(tuple(new), tuple(events), dup)


def _t(d: datetime | None) -> str | None:
    return None if d is None else iso_z(d)


def _parse(x: str | None) -> datetime | None:
    return None if x is None else datetime.fromisoformat(x)


def dump_vehicle(vs: VehicleState) -> dict[str, Any]:
    """JSON-ready (jsonb): datetimes as iso_z strings, tuples as lists."""
    ls, fs = vs.link, vs.frozen
    return {
        "link": {"los_open": ls.los_open, "epoch": _t(ls.epoch), "last_seq": ls.last_seq},
        "frozen": {
            "values": None if fs.values is None else list(fs.values),
            "first_seq": fs.first_seq,
            "t_from": _t(fs.t_from),
            "reported": fs.reported,
        },
    }


def load_vehicle(
    s: SeqState | None, obj: Mapping[str, Any] | None, set_sha256: str
) -> VehicleState:
    if obj is None:
        return VehicleState(s, LinkState(), FrozenState(), set_sha256)
    lk, fz = obj["link"], obj["frozen"]
    ls = LinkState(lk["los_open"], _parse(lk["epoch"]), lk["last_seq"])
    values = None if fz["values"] is None else tuple(fz["values"])
    fs = FrozenState(values, fz["first_seq"], _parse(fz["t_from"]), fz["reported"])
    return VehicleState(s, ls, fs, set_sha256)
