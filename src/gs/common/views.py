"""Read-only SQL → SSE views; one source for the worker publisher, the snapshot, REST and the
report. Every loader runs on the caller's connection (inside its transaction if it has one)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from gs.commanding.outcome import outcome
from gs.common.interfaces.sse import (
    AckRequestView,
    AlarmView,
    CommandView,
    EventView,
    TransitionView,
)

_COMMANDS = """
SELECT cmd_id, vehicle, name, args, hazardous, reason, issued_by, state, failure_reason,
       queued_at, sent_at, received_at, verified_at, failed_at, late_verified_at, late_ack_at,
       late_ack_accepted
FROM commands
WHERE (%(vehicle)s::text IS NULL OR vehicle = %(vehicle)s)
  AND (%(since)s::timestamptz IS NULL OR queued_at >= %(since)s)
  AND (%(until)s::timestamptz IS NULL OR queued_at < %(until)s)
  AND (%(ids)s::uuid[] IS NULL OR cmd_id = ANY(%(ids)s))
ORDER BY queued_at DESC, cmd_id
LIMIT %(limit)s
"""

# last = newest alarm_events row; lv = newest row with a level (its value goes with it, P-pf4)
_ALARMS = """
SELECT a.alarm_id, a.vehicle, a.param, a.state, a.severity, a.trigger_check, a.limits_sha256,
       a.set_sha256, a.opened_at, a.closed_at,
       CASE WHEN a.state IN ('RTN_UNACK', 'CLEARED') THEN 'NOMINAL' ELSE lv.level END AS level,
       lv.value,
       last.key AS t_key, last.kind AS t_kind,
       coalesce(last.trigger_t_onboard, last.processed_at) AS t_t, last.alert AS t_alert,
       last.by_user AS t_by_user, last.note AS t_note, last.check_kind AS t_check_kind,
       last.level AS t_level, last.value AS t_value,
       r.request_id, r.by_user AS r_by_user, r.status AS r_status, r.status_reason
FROM alarms a
JOIN LATERAL (SELECT * FROM alarm_events e WHERE e.alarm_id = a.alarm_id
              ORDER BY e.id DESC LIMIT 1) last ON true
LEFT JOIN LATERAL (SELECT e.level, e.value FROM alarm_events e
                   WHERE e.alarm_id = a.alarm_id AND e.level IS NOT NULL
                   ORDER BY e.id DESC LIMIT 1) lv ON true
LEFT JOIN LATERAL (SELECT * FROM alarm_ack_request q WHERE q.alarm_id = a.alarm_id
                   ORDER BY q.requested_at DESC LIMIT 1) r ON true
WHERE (%(vehicle)s::text IS NULL OR a.vehicle = %(vehicle)s)
  AND (%(severity)s::text IS NULL OR a.severity = %(severity)s)
  AND (%(state)s::text IS NULL OR a.state = %(state)s)
  AND (%(ids)s::uuid[] IS NULL OR a.alarm_id = ANY(%(ids)s))
  AND (NOT %(open_only)s OR a.state <> 'CLEARED')
ORDER BY a.opened_at DESC, a.alarm_id
"""

# ponytail: no index on coalesce(t_onboard, created_at); add one when events outgrow a scan
_EVENTS = """
SELECT key, vehicle, kind, t_onboard, details
FROM events
WHERE (%(vehicle)s::text IS NULL OR vehicle = %(vehicle)s OR vehicle IS NULL)
  AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s))
  AND (%(since)s::timestamptz IS NULL OR coalesce(t_onboard, created_at) >= %(since)s)
  AND (%(until)s::timestamptz IS NULL OR coalesce(t_onboard, created_at) < %(until)s)
ORDER BY coalesce(t_onboard, created_at) DESC, id DESC
LIMIT %(limit)s
"""


def _utc(row: dict[str, Any]) -> dict[str, Any]:
    """timestamptz arrives in the session's TimeZone; the views take UTC only."""
    return {k: v.astimezone(UTC) if isinstance(v, datetime) else v for k, v in row.items()}


async def _rows(
    conn: psycopg.AsyncConnection, query: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return [_utc(r) for r in await cur.fetchall()]


async def command_views(
    conn: psycopg.AsyncConnection,
    *,
    vehicle: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cmd_ids: Sequence[UUID] | None = None,
    limit: int | None = 50,
) -> list[CommandView]:
    """Newest queued_at first; `until` exclusive; limit None = all."""
    ids = None if cmd_ids is None else list(cmd_ids)
    p = {"vehicle": vehicle, "since": since, "until": until, "ids": ids, "limit": limit}
    return [
        CommandView(
            **r,
            outcome=outcome(r["failure_reason"], r["late_verified_at"], r["late_ack_accepted"]),
        )
        for r in await _rows(conn, _COMMANDS, p)
    ]


def _alarm(r: dict[str, Any]) -> AlarmView:
    last = TransitionView(
        key=r["t_key"], kind=r["t_kind"], t=r["t_t"], alert=r["t_alert"],
        by_user=r["t_by_user"], note=r["t_note"], check_kind=r["t_check_kind"],
        level=r["t_level"], value=r["t_value"],
    )  # fmt: skip
    ack = None
    if r["request_id"] is not None:
        ack = AckRequestView(
            request_id=r["request_id"], by_user=r["r_by_user"], status=r["r_status"],
            status_reason=r["status_reason"],
        )  # fmt: skip
    return AlarmView(
        alarm_id=r["alarm_id"], vehicle=r["vehicle"], param=r["param"], state=r["state"],
        severity=r["severity"], level=r["level"] or "NOMINAL", value=r["value"],
        trigger_check=r["trigger_check"], limits_sha256=r["limits_sha256"],
        set_sha256=r["set_sha256"], opened_at=r["opened_at"], closed_at=r["closed_at"],
        last=last, ack_request=ack,
    )  # fmt: skip


async def alarm_views(
    conn: psycopg.AsyncConnection,
    *,
    vehicle: str | None = None,
    severity: str | None = None,
    state: str | None = None,
    alarm_ids: Sequence[UUID] | None = None,
    open_only: bool = True,
) -> list[AlarmView]:
    """Newest opened_at first; an alarm without any alarm_events row is not listed."""
    ids = None if alarm_ids is None else list(alarm_ids)
    p = {"vehicle": vehicle, "severity": severity, "state": state, "ids": ids,
         "open_only": open_only}  # fmt: skip
    return [_alarm(r) for r in await _rows(conn, _ALARMS, p)]


async def event_views(
    conn: psycopg.AsyncConnection,
    *,
    vehicle: str | None = None,
    kinds: Sequence[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = 50,
) -> list[EventView]:
    """Newest first by coalesce(t_onboard, created_at); `until` exclusive; a vehicle filter
    keeps the ground events (vehicle NULL: LIMITS_REJECTED, INTERFACE_MISMATCH)."""
    p = {"vehicle": vehicle, "kinds": None if kinds is None else list(kinds), "since": since,
         "until": until, "limit": limit}  # fmt: skip
    return [EventView(**r) for r in await _rows(conn, _EVENTS, p)]
