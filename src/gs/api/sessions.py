"""Test sessions and their frozen report (route stubs; Track D Tasks 21-22)."""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import psycopg
from fastapi import APIRouter, Request, Response

from gs.api.bodies import ReportConflict, SessionReport, SessionStart, SessionView
from gs.api.dbguard import connect, db_503, utc
from gs.api.deps import ApiError, User, errors, mission, vehicles
from gs.api.report import frozen_report, stored_csv
from gs.common.audit import audit

router = APIRouter()
REPORT_ERRORS: dict[int | str, dict[str, Any]] = {
    **errors(401, 404, 422, 503),
    409: {"model": ReportConflict},
}


def _todo(what: str) -> ApiError:
    return ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", what)


_COLS = "id, vehicle, name, conductor, started_at, ended_at, ended_by"
_FIELDS = _COLS.split(", ")


def _view(row: tuple[Any, ...]) -> SessionView:
    return SessionView(**dict(zip(_FIELDS, utc(row), strict=True)))


@router.post("/api/sessions", status_code=201, responses=errors(400, 401, 403, 409, 422, 503))
async def start_session(body: SessionStart, request: Request, user: User) -> SessionView:
    if body.vehicle not in vehicles(request):
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "INVALID_ARGS", "vehicle not configured")
    with db_503(request):
        try:
            async with connect(request) as conn, conn.transaction():
                cur = await conn.execute(
                    "INSERT INTO test_session (vehicle, name, conductor) VALUES (%s, %s, %s)"
                    f" RETURNING {_COLS}",  # noqa: S608
                    (body.vehicle, body.name, user),
                )
                row = await cur.fetchone()
                assert row is not None
                await audit(
                    conn,
                    user,
                    "SESSION_START",
                    details={"session_id": row[0], "vehicle": body.vehicle},
                )
        except psycopg.errors.UniqueViolation as e:  # test_session_one_active (D-DB-07)
            raise ApiError(
                HTTPStatus.CONFLICT,
                "SESSION_ACTIVE",
                f"{body.vehicle} already has an active session",
            ) from e
    return _view(row)


@router.post("/api/sessions/{session_id}/stop", responses=errors(401, 403, 404, 409, 422, 503))
async def stop_session(session_id: int, request: Request, user: User) -> SessionView:
    with db_503(request):
        async with connect(request) as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE test_session SET ended_at = clock_timestamp(), ended_by = %s"
                f" WHERE id = %s AND ended_at IS NULL RETURNING {_COLS}",  # noqa: S608
                (user, session_id),
            )
            row = await cur.fetchone()
            if row is None:
                seen = await (
                    await conn.execute("SELECT 1 FROM test_session WHERE id = %s", (session_id,))
                ).fetchone()
                if seen is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"no session {session_id}")
                raise ApiError(HTTPStatus.CONFLICT, "SESSION_STOPPED", "session already stopped")
            await audit(conn, user, "SESSION_STOP", details={"session_id": session_id})
    return _view(row)


@router.get("/api/sessions", responses=errors(401, 503))
async def list_sessions(request: Request, user: User) -> list[SessionView]:
    with db_503(request):
        async with connect(request) as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM test_session ORDER BY started_at DESC, id DESC"  # noqa: S608
            )
            return [_view(r) for r in await cur.fetchall()]


async def _report(request: Request, session_id: int, want_csv: bool = False) -> SessionReport | str:
    with db_503(request):
        async with connect(request) as conn, conn.transaction():
            got = await frozen_report(conn, session_id, mission(request), datetime.now(UTC))
            if isinstance(got, SessionReport) and want_csv:
                return await stored_csv(conn, session_id)
    if got is None:
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"no session {session_id}")
    if isinstance(got, ReportConflict):
        raise ApiError(HTTPStatus.CONFLICT, got.error, got.message, body=got)
    return got


@router.get("/api/sessions/{session_id}/report", responses=REPORT_ERRORS)
async def get_report(session_id: int, request: Request, user: User) -> SessionReport:
    got = await _report(request, session_id)
    assert isinstance(got, SessionReport)
    return got


@router.get(
    "/api/sessions/{session_id}/report.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **REPORT_ERRORS},
)
async def get_report_csv(session_id: int, request: Request, user: User) -> Response:
    return Response(str(await _report(request, session_id, want_csv=True)), media_type="text/csv")
