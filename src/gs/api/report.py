"""Session report: one aggregate over [started_at, ended_at), frozen at the vehicle's watermark
or after FREEZE_AFTER_S (D-DB-06, D-DB-10), plus its CSV form."""

import csv
import io
import json
from datetime import datetime, timedelta

import psycopg
from psycopg import sql

from gs.api.bodies import AlarmTransition, ParamStats, ReportConflict, SessionReport, SessionView
from gs.api.dbguard import utc
from gs.common.interfaces.sse import TransitionView
from gs.common.missiondb import MissionDb
from gs.common.time import iso_z
from gs.common.views import command_views, event_views

FREEZE_AFTER_S = 30
DEFAULT_PERIOD_S = 0.1
LINK_KINDS = ("GAP", "SEQ_JUMP", "SEQ_RESET", "LOS", "AOS")  # a SEQ_RESET is a gap of unknown size
CSV_COLUMNS = (
    "section", "key", "vehicle", "name", "kind", "time", "end_time", "by_user", "unit",
    "min", "max", "avg", "n", "expected", "level", "value", "state", "outcome", "note",
    "frozen_at", "duration_s",
)  # fmt: skip
NOT_TEXT = {"time", "end_time", "frozen_at", "min", "max", "avg", "n", "expected", "value",
            "duration_s"}  # fmt: skip
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _end(s: SessionView) -> datetime:
    assert s.ended_at is not None
    return s.ended_at


async def _period(conn: psycopg.AsyncConnection, vehicle: str) -> float:
    cur = await conn.execute("SELECT period_s FROM seq_state WHERE vehicle = %s", (vehicle,))
    row = await cur.fetchone()
    return float(row[0]) if row else DEFAULT_PERIOD_S


async def _frames(conn: psycopg.AsyncConnection, s: SessionView) -> int:
    cur = await conn.execute(
        "SELECT count(*) FROM telemetry WHERE vehicle = %s AND t_onboard >= %s AND t_onboard < %s",
        (s.vehicle, s.started_at, _end(s)),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _stats(
    conn: psycopg.AsyncConnection, s: SessionView, mdb: MissionDb
) -> tuple[int, dict[str, ParamStats]]:
    """count(*) and per parameter min/max/avg/count in one pass over the window."""
    names = list(mdb.parameters)
    cols = sql.SQL(", ").join(
        sql.SQL("min({c}), max({c}), avg({c}), count({c})").format(c=sql.Identifier(n))
        for n in names
    )
    query = sql.SQL(
        "SELECT count(*){sep}{cols} FROM telemetry"
        " WHERE vehicle = %s AND t_onboard >= %s AND t_onboard < %s"
    ).format(sep=sql.SQL(", " if names else ""), cols=cols)
    cur = await conn.execute(query, (s.vehicle, s.started_at, _end(s)))
    row = await cur.fetchone()
    assert row is not None
    stats = {
        n: ParamStats(
            unit=mdb.parameters[n].unit,
            min=row[1 + 4 * i], max=row[2 + 4 * i], avg=row[3 + 4 * i], n=row[4 + 4 * i],
        )
        for i, n in enumerate(names)
    }  # fmt: skip
    return int(row[0]), stats


async def _transitions(conn: psycopg.AsyncConnection, s: SessionView) -> list[AlarmTransition]:
    cur = await conn.execute(
        "SELECT alarm_id, param, key, kind, coalesce(trigger_t_onboard, processed_at), alert,"
        " by_user, note, check_kind, level, value FROM alarm_events"
        " WHERE vehicle = %s AND alarm_id IS NOT NULL AND processed_at >= %s AND processed_at < %s"
        " ORDER BY id",
        (s.vehicle, s.started_at, _end(s)),
    )
    return [
        AlarmTransition(
            alarm_id=r[0], param=r[1],
            transition=TransitionView(
                key=r[2], kind=r[3], t=r[4], alert=r[5], by_user=r[6], note=r[7],
                check_kind=r[8], level=r[9], value=r[10],
            ),
        )
        for r in map(utc, await cur.fetchall())
    ]  # fmt: skip


async def build_report(
    conn: psycopg.AsyncConnection, s: SessionView, mdb: MissionDb, complete: bool, now: datetime
) -> SessionReport:
    end = _end(s)
    duration = (end - s.started_at).total_seconds()
    expected = round(duration / await _period(conn, s.vehicle))
    frames, parameters = await _stats(conn, s, mdb)
    window = {"vehicle": s.vehicle, "since": s.started_at, "until": end, "limit": None}
    # views are newest first; a report reads in time order
    events = await event_views(conn, kinds=LINK_KINDS, **window)  # type: ignore[arg-type]
    commands = await command_views(conn, **window)  # type: ignore[arg-type]
    return SessionReport(
        session=s, complete=complete,
        note=None if complete else f"incomplete: {frames} of {expected} frames",
        frozen_at=now, duration_s=duration, frames=frames, expected=expected,
        parameters=parameters, link_events=events[::-1], alarms=await _transitions(conn, s),
        commands=commands[::-1],
    )  # fmt: skip


async def _load(conn: psycopg.AsyncConnection, session_id: int) -> SessionView | None:
    cur = await conn.execute(
        "SELECT id, vehicle, name, conductor, started_at, ended_at, ended_by"
        " FROM test_session WHERE id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    cols = ("id", "vehicle", "name", "conductor", "started_at", "ended_at", "ended_by")
    return SessionView(**dict(zip(cols, utc(row), strict=True))) if row else None


async def _stored(conn: psycopg.AsyncConnection, session_id: int) -> SessionReport | None:
    cur = await conn.execute(
        "SELECT report::text FROM session_report WHERE session_id = %s", (session_id,)
    )
    row = await cur.fetchone()
    return SessionReport.model_validate_json(row[0]) if row else None  # P-pf15


async def frozen_report(
    conn: psycopg.AsyncConnection, session_id: int, mdb: MissionDb, now: datetime
) -> SessionReport | ReportConflict | None:
    """None: no such session. A stored report wins; running and not-yet-final sessions are a
    ReportConflict. Freezing is INSERT ... ON CONFLICT DO NOTHING and then reading the stored
    row back, so concurrent readers share one copy (R-p)."""
    if (done := await _stored(conn, session_id)) is not None:
        return done
    s = await _load(conn, session_id)
    if s is None:
        return None
    if s.ended_at is None:
        return ReportConflict(error="SESSION_RUNNING", message="session is still running")
    cur = await conn.execute("SELECT last_t FROM seq_state WHERE vehicle = %s", (s.vehicle,))
    mark = await cur.fetchone()
    complete = mark is not None and mark[0] >= s.ended_at
    if not complete and now < s.ended_at + timedelta(seconds=FREEZE_AFTER_S):
        frames = await _frames(conn, s)
        expected = round(
            (s.ended_at - s.started_at).total_seconds() / await _period(conn, s.vehicle)
        )
        return ReportConflict(
            error="REPORT_PENDING", message="vehicle data has not reached the end of the session",
            frames=frames, expected=expected,
        )  # fmt: skip
    built = await build_report(conn, s, mdb, complete, now)
    await conn.execute(
        "INSERT INTO session_report (session_id, report, csv) VALUES (%s, %s, %s)"
        " ON CONFLICT (session_id) DO NOTHING",
        (session_id, built.model_dump_json(), report_csv(built)),
    )
    return await _stored(conn, session_id)


async def stored_csv(conn: psycopg.AsyncConnection, session_id: int) -> str:
    """The csv column of a frozen report (jsonb loses the parameter order, so never rebuild it)."""
    cur = await conn.execute("SELECT csv FROM session_report WHERE session_id = %s", (session_id,))
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


def _f(col: str, v: object) -> str:
    """Empty for None; operator-typed text that a spreadsheet would run as a formula gets a
    leading ' (P-pf30); numeric and time columns are left alone so -5.0 stays a number."""
    text = "" if v is None else str(v)
    return "'" + text if col not in NOT_TEXT and text.startswith(FORMULA_START) else text


def _t(v: datetime | None) -> str:
    return "" if v is None else iso_z(v)


def report_csv(r: SessionReport) -> str:
    """Rows of five sections in one table; every time is iso_z, None is an empty field."""
    s = r.session
    rows: list[dict[str, object]] = [
        {"section": "summary", "key": s.id, "vehicle": s.vehicle, "name": s.name,
         "kind": "complete" if r.complete else "incomplete", "time": _t(s.started_at),
         "end_time": _t(s.ended_at), "by_user": s.conductor, "n": r.frames,
         "expected": r.expected, "note": r.note, "frozen_at": _t(r.frozen_at),
         "duration_s": r.duration_s}
    ]  # fmt: skip
    rows += [
        {"section": "parameter", "key": k, "name": k, "unit": p.unit, "min": p.min, "max": p.max,
         "avg": p.avg, "n": p.n}
        for k, p in r.parameters.items()
    ]  # fmt: skip
    rows += [
        {"section": "link_event", "key": e.key, "vehicle": e.vehicle, "kind": e.kind,
         "time": _t(e.t_onboard), "note": json.dumps(e.details, sort_keys=True)}
        for e in r.link_events
    ]  # fmt: skip
    rows += [
        {"section": "alarm", "key": a.transition.key, "vehicle": s.vehicle, "name": a.param,
         "kind": a.transition.kind, "time": _t(a.transition.t), "by_user": a.transition.by_user,
         "level": a.transition.level, "value": a.transition.value, "note": a.transition.note}
        for a in r.alarms
    ]  # fmt: skip
    rows += [
        {"section": "command", "key": c.cmd_id, "vehicle": c.vehicle, "name": c.name,
         "kind": c.failure_reason, "time": _t(c.queued_at), "by_user": c.issued_by,
         "state": c.state, "outcome": c.outcome, "note": c.reason}
        for c in r.commands
    ]  # fmt: skip
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for row in rows:
        w.writerow(_f(c, row.get(c)) for c in CSV_COLUMNS)
    return out.getvalue()
