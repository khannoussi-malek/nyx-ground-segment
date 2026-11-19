"""Pure per-vehicle sequence tracker (R5 "Per-vehicle tracker", D-ING gap/reset rules)."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from gs.ingest.events import GroundEvent, seq_key

MOD = 2**32
HALF = 2**31
RESET_SEQ_MAX = 50
NOMINAL_PERIOD_S = 0.1

Klass = Literal["FIRST", "IN_ORDER", "GAP", "SEQ_JUMP", "DUP_OR_LATE", "SEQ_RESET", "SEQ_ANOMALY"]


@dataclass(frozen=True)
class SeqState:
    epoch: datetime
    last_seq: int
    last_t: datetime
    period_s: float


def _gap(
    kind: Literal["GAP", "SEQ_JUMP"],
    v: str,
    epoch: datetime,
    t: datetime,
    from_seq: int,
    lost: int,
    by_time: int,
) -> GroundEvent:
    details = {"epoch": epoch, "from_seq": from_seq, "lost": lost, "lost_by_time": by_time}
    return GroundEvent(seq_key(kind, v, epoch, from_seq), v, kind, t, details)


def step(
    s: SeqState | None, vehicle: str, seq: int, t: datetime
) -> tuple[SeqState, Klass, tuple[GroundEvent, ...]]:
    if s is None:
        return SeqState(t, seq, t, NOMINAL_PERIOD_S), "FIRST", ()
    d = (seq - s.last_seq - 1) % MOD  # forward distance, wrap-safe (RFC 1982)
    dt = (t - s.last_t).total_seconds()
    if d >= HALF:  # behind expected
        if dt <= 0:
            return s, "DUP_OR_LATE", ()
        kind: Literal["SEQ_RESET", "SEQ_ANOMALY"] = (
            "SEQ_RESET" if seq <= RESET_SEQ_MAX else "SEQ_ANOMALY"
        )
        details = {"epoch": t, "prev_epoch": s.epoch, "from_seq": s.last_seq}
        ev = [GroundEvent(seq_key(kind, vehicle, t), vehicle, kind, t, details)]
        if kind == "SEQ_RESET" and seq > 0:  # frames 0..seq-1 of the new boot never arrived
            ev.append(_gap("GAP", vehicle, t, t, 0, seq, seq))
        return SeqState(t, seq, t, s.period_s), kind, tuple(ev)
    period = 0.9 * s.period_s + 0.1 * dt if d == 0 and dt > 0 else s.period_s
    ns = replace(s, last_seq=seq, last_t=t, period_s=period)
    if d == 0:
        return ns, "IN_ORDER", ()
    by_time = max(round(dt / s.period_s) - 1, 0)
    gk: Literal["GAP", "SEQ_JUMP"] = "GAP" if abs(d - by_time) <= max(2, d // 10) else "SEQ_JUMP"
    return ns, gk, (_gap(gk, vehicle, s.epoch, t, (s.last_seq + 1) % MOD, d, by_time),)
