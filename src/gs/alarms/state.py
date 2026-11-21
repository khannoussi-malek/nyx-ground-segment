"""Engine-state snapshot (D-ALM-10/11): exact JSON round trip, shape version, rebuild."""

import typing
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from gs.alarms.checks import CHECKS
from gs.alarms.contract import Level, OpenAlarm
from gs.alarms.engine import Alarm, CompiledSet, EngineState, Slot, _active_map
from gs.common.time import iso_z


def shape_version(*extra: type) -> int:
    """crc32 of every persisted dataclass's (qualname, field, type) and of each check kind's State
    name (a fieldless State counts too); a shape change changes it. crc32, not hash(): stable
    across processes."""
    classes = {Slot, Alarm, *(c.State for c in CHECKS.values()), *extra}
    rows = sorted((c.__qualname__, f.name, repr(f.type)) for c in classes for f in fields(c))
    kinds = sorted((k, c.State.__qualname__) for k, c in CHECKS.items())
    return zlib.crc32(repr((rows, kinds)).encode()) & 0x7FFFFFFF


_SCALARS = (int, float, str, bool, datetime)


def round_trips(tp: object) -> bool:
    """True if a check-State field annotated `tp` survives dump -> JSON -> load exactly."""
    if typing.get_origin(tp) is tuple:
        args = typing.get_args(tp)
        return len(args) == 2 and args[1] is Ellipsis and args[0] in _SCALARS
    return tp in _SCALARS


def _to_json(v: object) -> object:
    if isinstance(v, datetime):
        return iso_z(v)
    if isinstance(v, tuple | list):
        return [_to_json(x) for x in v]
    return v


def _from_json(tp: object, v: object) -> object:
    """Inverse of _to_json for a field annotated `tp` (tuples and aware datetimes come back)."""
    if tp is datetime:
        if not isinstance(v, str):
            raise ValueError(f"expected an ISO time, got {v!r}")
        return datetime.fromisoformat(v)
    if typing.get_origin(tp) is tuple:
        if not isinstance(v, list):
            raise ValueError(f"expected a list, got {v!r}")
        args = typing.get_args(tp)
        item = args[0] if len(args) == 2 and args[1] is Ellipsis else None
        return tuple(_from_json(item, x) if item else x for x in v)
    return v  # ponytail: no Optional[datetime | tuple] field in any State yet; add a branch then


def _check_state_json(state: object) -> dict[str, Any]:
    if not is_dataclass(state) or isinstance(state, type):
        raise TypeError(f"check state {state!r} is not a dataclass instance")
    return {f.name: _to_json(getattr(state, f.name)) for f in fields(state)}


def dump(state: EngineState) -> dict[str, Any]:
    """JSON-native; slots sorted by (param, kind), alarms by param, so equal states dump equal."""
    return {
        "mode": state.mode,
        "slots": [
            {
                "param": param,
                "kind": s.kind,
                "level": int(s.level),
                "side": s.side,
                "ring": [int(x) for x in s.ring],
                "state": {"kind": s.kind, **_check_state_json(s.state)},
            }
            for (param, _), s in sorted(state.slots.items())
        ],
        "alarms": [
            {
                "param": param,
                "alarm_id": str(a.alarm_id),
                "state": a.state,
                "severity": int(a.severity),
                "side": a.side,
                "acked_sev": None if a.acked_sev is None else int(a.acked_sev),
                "trigger_check": a.trigger_check,
                "opened_at": iso_z(a.opened_at),
            }
            for param, a in sorted(state.alarms.items())
        ],
    }


def _keys(d: Mapping[str, Any], want: set[str], what: str) -> None:
    if set(d) != want:
        raise ValueError(f"{what}: keys {sorted(d)} != {sorted(want)}")


def _load_check_state(d: Mapping[str, Any]) -> object:
    kind = d.get("kind")
    if kind not in CHECKS:
        raise ValueError(f"unknown check kind {kind!r}")
    cls = CHECKS[kind].State
    fs = fields(cls)
    _keys(d, {"kind", *(f.name for f in fs)}, f"{kind} state")
    return cls(**{f.name: _from_json(f.type, d[f.name]) for f in fs})


def _load_slot(d: Mapping[str, Any]) -> tuple[tuple[str, str], Slot]:
    _keys(d, {"param", "kind", "level", "side", "ring", "state"}, "slot")
    state = _load_check_state(d["state"])
    if d["state"]["kind"] != d["kind"]:
        raise ValueError(f"slot kind {d['kind']!r} holds a {d['state']['kind']!r} state")
    ring = tuple(Level(x) for x in d["ring"])
    return (d["param"], d["kind"]), Slot(d["kind"], Level(d["level"]), d["side"], ring, state)


def _load_alarm(d: Mapping[str, Any]) -> tuple[str, Alarm]:
    _keys(d, {"param", "alarm_id", "state", "severity", "side", "acked_sev", "trigger_check",
              "opened_at"}, "alarm")  # fmt: skip
    if d["state"] not in ("UNACK", "ACKED", "RTN_UNACK"):
        raise ValueError(f"alarm state {d['state']!r} is not open")
    acked = None if d["acked_sev"] is None else Level(d["acked_sev"])
    opened = datetime.fromisoformat(d["opened_at"])
    alarm = Alarm(UUID(d["alarm_id"]), d["state"], Level(d["severity"]), d["side"], acked,
                  d["trigger_check"], opened)  # fmt: skip
    return d["param"], alarm


def load(obj: Mapping[str, Any], cset: CompiledSet) -> EngineState:
    """Inverse of dump; `ValueError` on an unknown kind or a shape that does not fit `cset`."""
    try:
        # "mode" is absent in a snapshot older than D-EXT-06: it loads as the default set
        _keys(obj, {"slots", "alarms", *({"mode"} & set(obj))}, "engine state")
        mode = obj.get("mode")
        slots = dict(_load_slot(d) for d in obj["slots"])
        alarms = dict(_load_alarm(d) for d in obj["alarms"])
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"malformed engine state: {e!r}") from e
    want = {(p, c.kind) for p, checks in _active_map(cset, mode).items() for c, _ in checks}
    if set(slots) != want:
        raise ValueError(f"engine state slots {sorted(slots)} do not match the set {sorted(want)}")
    return EngineState(slots, alarms, mode)


def rebuild(
    open_alarms: Sequence[OpenAlarm], cset: CompiledSet, mode: str | None = None
) -> EngineState:
    """A-E1-5 approximation from the `alarms` rows: fresh slots, except each open (not RTN_UNACK)
    alarm's trigger check, seeded at its severity so a still-bad value neither clears nor
    re-alerts (ruling G2 item 6). Side, rings and ROC windows are lost."""
    closed = [a.param for a in open_alarms if a.state == "CLEARED"]
    if closed:
        raise ValueError(f"rebuild takes open alarms; CLEARED rows for {', '.join(closed)}")
    alarms = {
        a.param: Alarm(
            a.alarm_id, a.state, a.severity, None, a.acked_sev, a.trigger_check, a.opened_at
        )  # fmt: skip
        for a in open_alarms
    }
    slots = {}
    for param, checks in _active_map(cset, mode).items():
        a = alarms.get(param)
        seed = (
            (a.trigger_check, a.severity) if a and a.state != "RTN_UNACK" else ("", Level.NOMINAL)
        )
        for chk, _ in checks:
            level = seed[1] if chk.kind == seed[0] else Level.NOMINAL
            slots[(param, chk.kind)] = Slot(chk.kind, level, None, (), chk.initial())
    return EngineState(slots, alarms, mode)
