"""GET /api/alarms and POST /api/alarms/{alarm_id}/ack (route stubs; Track B Task 10)."""

from http import HTTPStatus
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request

from gs.alarms.contract import AlarmState
from gs.api.bodies import AckAccepted, AckRequest
from gs.api.deps import ApiError, User, errors
from gs.common.interfaces.base import VehicleId
from gs.common.interfaces.sse import AlarmView

router = APIRouter()


@router.get("/api/alarms", responses=errors(401, 422, 503))
async def list_alarms(
    request: Request,
    user: User,
    vehicle: VehicleId | None = None,
    severity: Literal["YELLOW", "RED"] | None = None,
    state: AlarmState | None = None,
) -> list[AlarmView]:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "GET /api/alarms")


@router.post(
    "/api/alarms/{alarm_id}/ack",
    status_code=202,
    responses=errors(400, 401, 403, 404, 409, 422, 503),
)
async def post_ack(alarm_id: UUID, body: AckRequest, request: Request, user: User) -> AckAccepted:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "POST /api/alarms/{alarm_id}/ack")
