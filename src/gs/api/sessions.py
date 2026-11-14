"""Test sessions and their frozen report (route stubs; Track D Tasks 21-22)."""

from http import HTTPStatus
from typing import Any

from fastapi import APIRouter, Request, Response

from gs.api.bodies import ReportConflict, SessionReport, SessionStart, SessionView
from gs.api.deps import ApiError, User, errors

router = APIRouter()
REPORT_ERRORS: dict[int | str, dict[str, Any]] = {
    **errors(401, 404, 422, 503),
    409: {"model": ReportConflict},
}


def _todo(what: str) -> ApiError:
    return ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", what)


@router.post("/api/sessions", status_code=201, responses=errors(400, 401, 403, 409, 422, 503))
async def start_session(body: SessionStart, request: Request, user: User) -> SessionView:
    raise _todo("POST /api/sessions")


@router.post("/api/sessions/{session_id}/stop", responses=errors(401, 403, 404, 409, 422, 503))
async def stop_session(session_id: int, request: Request, user: User) -> SessionView:
    raise _todo("POST /api/sessions/{session_id}/stop")


@router.get("/api/sessions", responses=errors(401, 503))
async def list_sessions(request: Request, user: User) -> list[SessionView]:
    raise _todo("GET /api/sessions")


@router.get("/api/sessions/{session_id}/report", responses=REPORT_ERRORS)
async def get_report(session_id: int, request: Request, user: User) -> SessionReport:
    raise _todo("GET /api/sessions/{session_id}/report")


@router.get(
    "/api/sessions/{session_id}/report.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}, **REPORT_ERRORS},
)
async def get_report_csv(session_id: int, request: Request, user: User) -> Response:
    raise _todo("GET /api/sessions/{session_id}/report.csv")
