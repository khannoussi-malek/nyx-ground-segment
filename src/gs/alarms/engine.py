"""Alarm engine (D-ALM-03..09): persistence, one alarm per (vehicle, param), ISA-18.2 life cycle.

Pure: `step` and `ack` return a new EngineState and the transitions to persist; nothing is
mutated. Checks are dispatched through the registry (`CHECKS[c.type]`), so a plug-in check kind
runs without engine edits (ALM-07).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from gs.alarms.checks import CHECKS, Check, Sample
from gs.alarms.contract import AlarmState, Level, Transition, TransitionKind
from gs.common.missiondb import MissionDb
from gs.ingest.events import alarm_id_for, transition_key

SYSTEM_USER = "system"


@dataclass(frozen=True)
class Slot:
    kind: str
    level: Level  # debounced level
    side: str | None  # side of the latest non-NOMINAL reading while level > NOMINAL
    ring: tuple[Level, ...]  # last N raw reading levels
    state: object  # the check's own State


@dataclass(frozen=True)
class Alarm:
    alarm_id: UUID
    state: AlarmState
    severity: Level  # last non-NOMINAL severity (an RTN_UNACK alarm is at NOMINAL)
    side: str | None
    acked_sev: Level | None
    trigger_check: str
    opened_at: datetime


@dataclass(frozen=True)
class EngineState:
    slots: Mapping[tuple[str, str], Slot]  # (param, check kind)
    alarms: Mapping[str, Alarm]  # open alarms by param


@dataclass(frozen=True)
class CompiledSet:
    set_sha256: str
    limits_sha256: str
    checks: Mapping[str, tuple[tuple[Check, int], ...]]  # param -> (check, persistence), file order


def compile_set(mdb: MissionDb) -> CompiledSet:
    checks: dict[str, tuple[tuple[Check, int], ...]] = {}
    for param, entry in mdb.limits.items():
        compiled = []
        for c in entry.checks:
            if c.persistence is None:
                raise ValueError(f"{param}/{c.type}: persistence not resolved")
            compiled.append((CHECKS[c.type](c), c.persistence))
        checks[param] = tuple(compiled)
    return CompiledSet(mdb.set_sha256, mdb.limits_sha256, checks)


def initial_state(cset: CompiledSet) -> EngineState:
    slots = {
        (param, chk.kind): Slot(chk.kind, Level.NOMINAL, None, (), chk.initial())
        for param, checks in cset.checks.items()
        for chk, _ in checks
    }
    return EngineState(slots, {})


def _slot_step(slot: Slot, chk: Check, n: int, s: Sample | None) -> Slot:
    if s is None:  # NULL sample or reset: keep the level, restart ring and check state (D-ALM-04)
        return replace(slot, ring=(), state=chk.initial())
    st, r = chk.step(slot.state, s, slot.level)
    if r is None:  # no opinion: nothing pushed
        return replace(slot, state=st)
    ring = (*slot.ring, r.level)[-n:]
    level = slot.level
    if len(ring) == n and min(ring) > level:
        level = min(ring)
    elif len(ring) == n and max(ring) < level:
        level = max(ring)
    side = None if level == Level.NOMINAL else (r.side or slot.side)
    return Slot(slot.kind, level, side, ring, st)


@dataclass(frozen=True)
class _Frame:
    vehicle: str
    epoch: datetime
    seq: int
    t: datetime


def _transition(
    f: _Frame,
    param: str,
    kind: TransitionKind,
    a: Alarm,
    sev: Level,
    trigger: Slot | None,
    alert: bool,
    closed: bool = False,
) -> Transition:
    return Transition(
        key=transition_key(f.vehicle, param, f.epoch, f.seq, kind),
        alarm_id=a.alarm_id,
        vehicle=f.vehicle,
        param=param,
        kind=kind,
        check_kind=trigger.kind if trigger else None,
        level=f"{sev.name}_{trigger.side}" if trigger else None,
        severity=sev,
        alarm_state=a.state,
        acked_sev=a.acked_sev,
        trigger_check=a.trigger_check,
        trigger_seq=f.seq,
        trigger_t=f.t,
        alert=alert,
        by_user=SYSTEM_USER,
        note=None,
        opened_at=a.opened_at,
        closed_at=f.t if closed else None,
    )


def _rise(
    f: _Frame, param: str, a: Alarm | None, sev: Level, trig: Slot
) -> tuple[Alarm, Transition]:
    red = sev == Level.RED  # only RED alerts (D-ALM-09)
    if a is None or a.state == "RTN_UNACK":  # a new alarm, or RAISE again on the RTN_UNACK one
        if a is None:
            aid = alarm_id_for(transition_key(f.vehicle, param, f.epoch, f.seq, "RAISE"))
            a = Alarm(aid, "UNACK", sev, None, None, trig.kind, f.t)
        new = replace(a, state="UNACK", severity=sev, side=trig.side, trigger_check=trig.kind)
        return new, _transition(f, param, "RAISE", new, sev, trig, red)
    keep_ack = a.state == "ACKED" and a.acked_sev == Level.RED  # D-ALM-08: red already acked
    state: AlarmState = "ACKED" if keep_ack else "UNACK"
    new = replace(a, state=state, severity=sev, side=trig.side, trigger_check=trig.kind)
    return new, _transition(f, param, "ESCALATE", new, sev, trig, red and not keep_ack)


def _fall(
    f: _Frame, param: str, a: Alarm, sev: Level, trig: Slot
) -> tuple[Alarm | None, Transition]:
    if sev > Level.NOMINAL:
        new = replace(a, severity=sev, side=trig.side, trigger_check=trig.kind)
        return new, _transition(f, param, "DEESCALATE", new, sev, trig, False)
    if a.state == "ACKED":
        cleared = replace(a, state="CLEARED")
        return None, _transition(f, param, "CLEAR", cleared, sev, None, False, closed=True)
    rtn = replace(a, state="RTN_UNACK")
    return rtn, _transition(f, param, "RTN", rtn, sev, None, False)


def step(
    state: EngineState,
    cset: CompiledSet,
    vehicle: str,
    epoch: datetime,
    seq: int,
    t: datetime,
    period_s: float,
    values: Mapping[str, float | None],
    reset: bool,
) -> tuple[EngineState, tuple[Transition, ...]]:
    """One frame. `reset` (GAP, SEQ_RESET, data-STALE; the caller decides) acts like a NULL."""
    f = _Frame(vehicle, epoch, seq, t)
    slots, alarms = dict(state.slots), dict(state.alarms)
    out: list[Transition] = []
    for param, checks in cset.checks.items():
        v = values.get(param)
        sample = None if v is None or reset else Sample(seq, t.timestamp(), v, period_s)
        new = [_slot_step(slots[(param, c.kind)], c, n, sample) for c, n in checks]
        slots.update({(param, s.kind): s for s in new})
        sev = max(s.level for s in new)
        trig = next(s for s in new if s.level == sev)  # first slot at that level, file order
        a = alarms.get(param)
        alarm: Alarm | None
        cur = Level.NOMINAL if a is None or a.state == "RTN_UNACK" else a.severity
        if sev > cur:
            alarm, tr = _rise(f, param, a, sev, trig)
        elif sev < cur and a is not None:
            alarm, tr = _fall(f, param, a, sev, trig)
        else:
            continue
        out.append(tr)
        if alarm is None:
            alarms.pop(param)
        else:
            alarms[param] = alarm
    return EngineState(slots, alarms), tuple(out)


def ack(
    state: EngineState, vehicle: str, param: str, user: str, note: str, key: str, t: datetime
) -> tuple[EngineState, Transition | None]:
    """UNACK -> ACKED, RTN_UNACK -> CLEARED; anything else is a no-op (superseded)."""
    if not note.strip():
        raise ValueError("an ACK needs a note (D-ALM-07)")
    a = state.alarms.get(param)
    if a is None or a.state not in ("UNACK", "RTN_UNACK"):
        return state, None
    clears = a.state == "RTN_UNACK"
    new = replace(a, state="CLEARED" if clears else "ACKED", acked_sev=a.severity)
    alarms = {p: x for p, x in state.alarms.items() if p != param}
    if not clears:
        alarms[param] = new
    tr = Transition(
        key=key,
        alarm_id=a.alarm_id,
        vehicle=vehicle,
        param=param,
        kind="ACK",
        check_kind=None,
        level=None,
        severity=a.severity,
        alarm_state=new.state,
        acked_sev=new.acked_sev,
        trigger_check=a.trigger_check,
        trigger_seq=None,
        trigger_t=None,
        alert=False,
        by_user=user,
        note=note,
        opened_at=a.opened_at,
        closed_at=t if clears else None,
    )
    return EngineState(state.slots, alarms), tr
