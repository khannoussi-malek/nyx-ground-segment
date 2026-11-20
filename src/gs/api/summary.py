"""GET /api/summary: the "Since..." list (D-EXT-21): the session report's alarm, command and
event queries over [since, now) instead of a session. Reads alarm_events, events, commands and
(for the default `since`) audit_log and test_session; no archive, no new table."""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Request

from gs.api.bodies import AlarmTransition, Summary, SummaryItem
from gs.api.dbguard import connect, db_503
from gs.api.deps import ApiError, User, errors, vehicles
from gs.api.report import alarm_rows, alarm_transition
from gs.common.interfaces.base import UtcTime, VehicleId
from gs.common.interfaces.sse import CommandView, EventView
from gs.common.views import command_views, timed_event_views

router = APIRouter()
MAX_WINDOW = timedelta(days=7)
KINDS = (
    "LOS",
    "AOS",
    "GAP",
    "SEQ_RESET",
    "LIMITS_APPLIED",
    "ARCHIVE_CORRUPT",
    "INTERFACE_MISMATCH",
)
SinceSource = Literal["query", "last_logout", "session_start"]


def _bad(error: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, error, message)


def _clock(t: datetime | None) -> str:
    return "?" if t is None else t.astimezone(UTC).strftime("%H:%M:%SZ")


def _sha(e: EventView, k: str) -> str:
    v = e.details.get(k)
    return (v if isinstance(v, str) else "?")[:8]


def _frames(n: object) -> str:
    if not isinstance(n, int):
        return "unknown number of frames lost"
    return f"{n} {'frame' if n == 1 else 'frames'} lost"


def alarm_text(vehicle: str, a: AlarmTransition) -> str:
    tr = a.transition
    what = f"{vehicle} {a.param}"
    level = f" {tr.level}" if tr.level else ""
    value = "" if tr.value is None else f" ({tr.value:g})"
    match tr.kind:
        case "RAISE":
            return f"{what} alarm raised{level}{value}"
        case "ESCALATE":
            return f"{what} alarm escalated to{level}{value}"
        case "DEESCALATE":
            return f"{what} alarm eased to{level}{value}"
        case "RTN":
            return f"{what} back in limits, still to acknowledge"
        case "ACK":
            return f"{what} alarm acknowledged by {tr.by_user}: {tr.note}"
        case _:
            return f"{what} alarm cleared"


def command_text(c: CommandView) -> str:
    args = ", ".join(f"{k} = {v}" for k, v in c.args.items())
    head = f"{c.issued_by} sent {c.name}{f' ({args})' if args else ''} to {c.vehicle}"
    if c.failure_reason is None:
        return f"{head} – {c.state}"
    outcome = {
        "EXECUTED_LATE": "vehicle executed it late – do NOT resend",
        "NOT_EXECUTED": "vehicle did not execute it – safe to resend after checking why",
        "RECEIVED_LATE": "vehicle accepted it late, not yet seen executing – wait",
        "NOT_SENT": "not sent – safe to retry",
    }.get(c.outcome or "", "outcome unknown: check the vehicle before resending")
    return f"{head} – FAILED ({c.failure_reason}) – {outcome}"


def event_text(e: EventView, t: datetime) -> str:
    v = e.vehicle or ""
    d = e.details
    match e.kind:
        case "LOS":
            return f"{v} LOS since {_clock(t)}"
        case "AOS":
            return f"{v} AOS – {_frames(d.get('lost_frames'))}"
        case "GAP":
            return f"{v} GAP – {_frames(d.get('lost'))}"
        case "SEQ_RESET":
            return f"{v} SEQ RESET – gap of unknown size"
        case "LIMITS_APPLIED":
            return (
                f"mission db applied: limits {_sha(e, 'limits_sha256')} · commands "
                f"{_sha(e, 'commands_sha256')} · parameters {_sha(e, 'parameters_sha256')}"
            )
        case "INTERFACE_MISMATCH":
            detail = d.get("detail")
            return f"interface mismatch on {d.get('stream', '?')}" + (
                f": {detail}" if isinstance(detail, str) else ""
            )
        case _:
            return f"{v} {e.kind}".strip()


async def _default_since(
    conn: psycopg.AsyncConnection, vehicle: str, user: str
) -> tuple[datetime, SinceSource] | None:
    """The caller's last LOGOUT, else the start of the vehicle's active session."""
    cur = await conn.execute(
        "SELECT max(at) FROM audit_log WHERE actor = %s AND action = 'LOGOUT'", (user,)
    )
    row = await cur.fetchone()
    if row and row[0] is not None:
        return row[0].astimezone(UTC), "last_logout"
    cur = await conn.execute(
        "SELECT started_at FROM test_session WHERE vehicle = %s AND ended_at IS NULL", (vehicle,)
    )
    row = await cur.fetchone()
    return (row[0].astimezone(UTC), "session_start") if row else None


async def _known(conn: psycopg.AsyncConnection, vehicle: str) -> bool:
    """A recorded vehicle (NYX-TA9) is one the ingest has ever stored a frame for."""
    cur = await conn.execute("SELECT 1 FROM seq_state WHERE vehicle = %s", (vehicle,))
    return await cur.fetchone() is not None


def _items(
    vehicle: str,
    alarms: list[Any],
    commands: list[CommandView],
    events: list[tuple[datetime, EventView]],
) -> list[SummaryItem]:
    items = [
        SummaryItem(t=a.transition.t, source="alarm", kind=a.transition.kind,
                    text=alarm_text(vehicle, a), alarm=a)
        for a in map(alarm_transition, alarms)
    ] + [
        SummaryItem(t=c.queued_at, source="command", kind="COMMAND", text=command_text(c),
                    command=c)
        for c in commands
    ] + [
        SummaryItem(t=t, source="event", kind=e.kind, text=event_text(e, t), event=e)
        for t, e in events
    ]  # fmt: skip
    return sorted(items, key=lambda i: i.t)  # stable: alarms, commands, events at one instant


@router.get("/api/summary", responses=errors(400, 401, 404, 422, 503))
async def get_summary(
    request: Request, user: User, vehicle: VehicleId, since: UtcTime | None = None
) -> Summary:
    now = datetime.now(UTC)
    if since is not None and since > now:
        raise _bad("INVALID_WINDOW", "since is in the future")
    with db_503(request):
        async with connect(request) as conn:
            if vehicle not in vehicles(request) and not await _known(conn, vehicle):
                raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"unknown vehicle: {vehicle}")
            source: SinceSource
            default = None if since is not None else await _default_since(conn, vehicle, user)
            if since is not None:
                start, source = since, "query"
            elif default is None:
                raise _bad("SINCE_REQUIRED", "no earlier logout or active session: give `since`")
            else:
                start, source = default
            if now - start > MAX_WINDOW:
                raise _bad("WINDOW_TOO_LARGE", "the window is at most 7 days")
            alarms = await alarm_rows(conn, vehicle, start, now)
            commands = await command_views(
                conn, vehicle=vehicle, since=start, until=now, limit=None
            )
            events = await timed_event_views(
                conn, vehicle=vehicle, kinds=KINDS, since=start, until=now, limit=None
            )
    return Summary(vehicle=vehicle, since=start, until=now, since_source=source,
                   items=_items(vehicle, alarms, commands, events))  # fmt: skip
