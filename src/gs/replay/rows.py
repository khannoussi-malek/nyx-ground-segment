"""Recorded alarm history, folded independently of today's alarm engine."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg

from gs.alarms.contract import AlarmState, TransitionKind
from gs.common.interfaces.sse import AlarmView, EventView, TransitionView
from gs.common.views import _rows

# Closing ACKs follow RTN in an alarm's append-only history; apply time is not replay position.
_HISTORY = """
WITH history AS (
 SELECT e.*, a.opened_at, a.closed_at,
        lag(e.kind) OVER (PARTITION BY e.alarm_id ORDER BY e.id) AS previous_kind
 FROM alarm_events e JOIN alarms a USING (alarm_id)
 WHERE e.vehicle = %(vehicle)s
), positioned AS (
 SELECT *, coalesce(trigger_t_onboard, after_t_onboard) AS t,
        (kind = 'CLEAR' OR (kind = 'ACK' AND previous_kind = 'RTN')) AS closes
 FROM history
)
"""


def next_state(
    prev: AlarmState | None, row_kind: TransitionKind, closes: bool, alert: bool = True
) -> AlarmState:
    """D-ALM-08: an already acknowledged RED re-escalation keeps its acknowledgement."""
    if row_kind == "ESCALATE":
        return "ACKED" if prev == "ACKED" and not alert else "UNACK"
    if row_kind == "RAISE":
        return "UNACK"
    if row_kind == "DEESCALATE":
        assert prev is not None
        return prev
    if row_kind == "RTN":
        return "RTN_UNACK"
    if row_kind == "CLEAR" or closes:
        return "CLEARED"
    return "ACKED"


def position(
    kind: Literal["frame", "alarm", "ack", "event"], row: dict[str, Any]
) -> tuple[datetime, int, int, int]:
    if kind == "frame":
        return row["t_onboard"], row["seq"], 0, 0
    if kind == "event":
        return row["t_onboard"], 2**32, 2, row["id"]
    if kind == "ack":
        return row["after_t_onboard"], row["after_seq"], 1, row["id"]
    return row["trigger_t_onboard"], row["trigger_seq"], 1, row["id"]


def alarm_after(prev: AlarmView | None, row: dict[str, Any]) -> AlarmView:
    """Apply one recorded row, retaining the last non-NOMINAL reading after ACK/RTN."""
    state = next_state(prev.state if prev else None, row["kind"], row["closes"], row["alert"])
    level, value = (prev.level, prev.value) if prev else ("NOMINAL", None)
    severity = prev.severity if prev else row["severity"]
    if row["level"] and row["level"] != "NOMINAL":
        level, value, severity = row["level"], row["value"], row["severity"]
    trigger_check = row["check_kind"]
    if trigger_check is None:
        assert prev is not None
        trigger_check = prev.trigger_check
    t = position("ack" if row["kind"] == "ACK" else "alarm", row)[0]
    return AlarmView(
        alarm_id=row["alarm_id"], vehicle=row["vehicle"], param=row["param"], state=state,
        severity=severity, level=level, value=value, trigger_check=trigger_check,
        limits_sha256=row["limits_sha256"], set_sha256=row["set_sha256"],
        opened_at=row["opened_at"],
        closed_at=(row["closed_at"] if row["kind"] == "ACK" else t)
        if state == "CLEARED" else None,
        last=TransitionView(
            key=row["key"], kind=row["kind"], t=t, alert=row["alert"], by_user=row["by_user"],
            note=row["note"], check_kind=row["check_kind"], level=row["level"], value=row["value"],
        ),
        ack_request=None,
    )  # fmt: skip


async def recorded_alarm_rows(
    conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime, t_to: datetime
) -> list[dict[str, Any]]:
    return await _rows(
        conn,
        _HISTORY + "SELECT * FROM positioned WHERE t >= %(a)s AND t < %(b)s"  # noqa: S608 concatenates only the constant _HISTORY SQL fragment, values bound
        " ORDER BY t, coalesce(trigger_seq, after_seq), id",
        {"vehicle": vehicle, "a": t_from, "b": t_to},
    )


async def initial_alarms(
    conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime
) -> list[AlarmView]:
    rows = await _rows(
        conn,
        _HISTORY  # noqa: S608 concatenates only the constant _HISTORY SQL fragment, values bound
        + """, before_start AS (
          SELECT * FROM positioned WHERE t < %(a)s
        ), latest AS (
          SELECT DISTINCT ON (alarm_id) alarm_id, closes FROM before_start
          ORDER BY alarm_id, t DESC, coalesce(trigger_seq, after_seq) DESC, id DESC
        )
        SELECT b.* FROM before_start b JOIN latest l USING (alarm_id)
        WHERE NOT l.closes ORDER BY b.t, coalesce(b.trigger_seq, b.after_seq), b.id
        """,
        {"vehicle": vehicle, "a": t_from},
    )
    alarms: dict[UUID, AlarmView] = {}
    for row in rows:
        aid = row["alarm_id"]
        alarms[aid] = alarm_after(alarms.get(aid), row)
    return sorted(alarms.values(), key=lambda a: (a.opened_at, a.alarm_id), reverse=True)


async def recorded_event_rows(
    conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime, t_to: datetime
) -> list[EventView]:
    rows = await _rows(
        conn,
        "SELECT key, vehicle, kind, t_onboard, details FROM events"
        " WHERE vehicle = %(vehicle)s AND t_onboard >= %(a)s AND t_onboard < %(b)s"
        " ORDER BY t_onboard, id",
        {"vehicle": vehicle, "a": t_from, "b": t_to},
    )
    return [EventView(**row) for row in rows]


async def los_open_at(conn: psycopg.AsyncConnection[Any], vehicle: str, t_from: datetime) -> bool:
    rows = await _rows(
        conn,
        "SELECT kind FROM events WHERE vehicle = %(vehicle)s AND t_onboard < %(a)s"
        " AND kind IN ('LOS', 'AOS') ORDER BY t_onboard DESC, id DESC LIMIT 1",
        {"vehicle": vehicle, "a": t_from},
    )
    return bool(rows and rows[0]["kind"] == "LOS")
