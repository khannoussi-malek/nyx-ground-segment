"""LOS/AOS and FROZEN detectors over vehicle time (pure; D-ING-21, data-STALE / LOS at > 2 s)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import get_args

from gs.common.interfaces.dlq import DlqReason
from gs.ingest.events import GroundEvent, frozen_key, link_key
from gs.ingest.seq import SeqState

STALE_S = 2.0
FROZEN_S = 2.0


@dataclass(frozen=True)
class LinkState:
    los_open: bool = False
    epoch: datetime | None = None  # suffix of the open LOS
    last_seq: int | None = None


def _los(vehicle: str, prev: SeqState, rejected: Mapping[str, int] | None = None) -> GroundEvent:
    details: dict[str, object] = {"epoch": prev.epoch, "last_seq": prev.last_seq}
    # D-UI-25: DLQ reasons since the last new frame
    if rejected and (count := sum(rejected.values())):
        top = max(get_args(DlqReason), key=lambda r: rejected.get(r, 0))  # ties: DlqReason order
        details["rejected"] = {"count": count, "reason": top}
    key = link_key("LOS", vehicle, prev.epoch, prev.last_seq)
    return GroundEvent(key, vehicle, "LOS", prev.last_t, details)


def on_silence(
    s: LinkState,
    vehicle: str,
    prev: SeqState | None,
    rejected: Mapping[str, int] | None = None,
) -> tuple[LinkState, tuple[GroundEvent, ...]]:
    """Called by the read loop after STALE_S without a new frame."""
    if s.los_open or prev is None:
        return s, ()
    return LinkState(True, prev.epoch, prev.last_seq), (_los(vehicle, prev, rejected),)


def on_frame(
    s: LinkState, vehicle: str, prev: SeqState | None, t: datetime, lost: int | None
) -> tuple[LinkState, tuple[GroundEvent, ...], bool]:
    """Returns the data-STALE flag (vehicle-time gap > STALE_S) for the engine reset."""
    stale = prev is not None and (t - prev.last_t).total_seconds() > STALE_S
    if not (s.los_open or stale):
        return s, (), False
    s, ev = on_silence(s, vehicle, prev)  # no-op when the LOS is already open
    assert s.epoch is not None and s.last_seq is not None
    details = {"epoch": s.epoch, "last_seq": s.last_seq, "lost_frames": lost}
    aos = GroundEvent(link_key("AOS", vehicle, s.epoch, s.last_seq), vehicle, "AOS", t, details)
    return LinkState(), (*ev, aos), stale


@dataclass(frozen=True)
class FrozenState:
    values: tuple[float | None, ...] | None = None
    first_seq: int = 0
    t_from: datetime | None = None
    reported: bool = False


def frozen_step(
    s: FrozenState,
    vehicle: str,
    epoch: datetime,
    seq: int,
    t: datetime,
    values: tuple[float | None, ...],
) -> tuple[FrozenState, GroundEvent | None]:
    """`values` = every analog mission parameter in order; `==` so -0.0 equals 0.0.
    A NaN never equals itself, so it would never count as frozen (frames reject NaN anyway)."""
    if s.values is None or s.t_from is None or values != s.values:
        return FrozenState(values, seq, t, False), None
    if s.reported or (t - s.t_from).total_seconds() < FROZEN_S:
        return s, None
    details = {"first_seq": s.first_seq, "t_from": s.t_from}
    ev = GroundEvent(frozen_key(vehicle, epoch, s.first_seq), vehicle, "FROZEN", t, details)
    return FrozenState(s.values, s.first_seq, s.t_from, True), ev
