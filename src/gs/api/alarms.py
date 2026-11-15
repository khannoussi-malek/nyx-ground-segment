"""GET /api/alarms and POST /api/alarms/{alarm_id}/ack (D-ALM-14). The API only records the
request; the owner worker applies it (it is the only writer of alarm state)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from psycopg_pool import PoolTimeout

from gs.alarms.contract import AlarmState
from gs.api.bodies import AckAccepted, AckRequest
from gs.api.deps import ApiError, User, errors
from gs.common.audit import audit
from gs.common.interfaces.base import VehicleId
from gs.common.interfaces.sse import AlarmView
from gs.common.views import alarm_views

DB_TIMEOUT_S = 5.0  # waiting for a pool connection before answering 503
router = APIRouter()
log = logging.getLogger(__name__)


@asynccontextmanager
async def _db(request: Request) -> AsyncIterator[psycopg.AsyncConnection]:
    """A pool connection; an unreachable database is 503 UNAVAILABLE, never a 500."""
    try:
        async with request.app.state.pool.connection(timeout=DB_TIMEOUT_S) as conn:
            yield conn
    except (psycopg.OperationalError, PoolTimeout) as e:
        log.warning("database error on %s: %s: %s", request.url.path, type(e).__name__, e)
        raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", "database unreachable") from e


@router.get("/api/alarms", responses=errors(401, 422, 503))
async def list_alarms(
    request: Request,
    user: User,
    vehicle: VehicleId | None = None,
    severity: Literal["YELLOW", "RED"] | None = None,
    state: AlarmState | None = None,
) -> list[AlarmView]:
    # open alarms (newest first) unless state=CLEARED asks for closed ones; a comment, not a
    # docstring: FastAPI would put it in the frozen OpenAPI document
    async with _db(request) as conn:
        return await alarm_views(
            conn, vehicle=vehicle, severity=severity, state=state, open_only=state != "CLEARED"
        )


async def _stored_status(conn: psycopg.AsyncConnection, request_id: UUID) -> str | None:
    cur = await conn.execute(
        "SELECT status FROM alarm_ack_request WHERE request_id = %s", (request_id,)
    )
    row = await cur.fetchone()
    return None if row is None else str(row[0])


@router.post(
    "/api/alarms/{alarm_id}/ack",
    status_code=202,
    responses=errors(400, 401, 403, 404, 409, 422, 503),
)
async def post_ack(alarm_id: UUID, body: AckRequest, request: Request, user: User) -> AckAccepted:
    # idempotent per request_id: a repeat returns the stored status (also once the alarm has
    # cleared), with no second row and no second ALARM_ACK audit
    if not body.note.strip():
        raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, "NOTE_REQUIRED", "an ACK needs a note")
    rid = body.request_id
    async with _db(request) as conn, conn.transaction():
        if (stored := await _stored_status(conn, rid)) is not None:
            return AckAccepted.model_validate({"request_id": rid, "status": stored})
        cur = await conn.execute(
            "SELECT vehicle, state FROM alarms WHERE alarm_id = %s", (alarm_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "ALARM_NOT_FOUND", f"no alarm {alarm_id}")
        vehicle, state = row
        if state == "CLEARED":
            raise ApiError(HTTPStatus.CONFLICT, "ALARM_CLEARED", "the alarm is already cleared")
        cur = await conn.execute(
            "INSERT INTO alarm_ack_request (request_id, alarm_id, vehicle, by_user, note)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (request_id) DO NOTHING",
            (rid, alarm_id, vehicle, user, body.note),
        )
        if cur.rowcount:  # a concurrent twin may have won the insert
            details = {"vehicle": vehicle, "alarm_id": alarm_id, "request_id": rid}
            await audit(conn, user, "ALARM_ACK", body.note, details)
        status = await _stored_status(conn, rid)
    return AckAccepted.model_validate({"request_id": rid, "status": status})
