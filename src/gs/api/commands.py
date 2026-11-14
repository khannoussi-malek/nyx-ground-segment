"""POST/GET /api/commands (route stubs; Track A Task 6 implements them)."""

from http import HTTPStatus

from fastapi import APIRouter, Request

from gs.api.bodies import CommandAccepted, CommandConflict, CommandRequest
from gs.api.deps import ApiError, User, errors
from gs.common.interfaces.base import UtcTime, VehicleId
from gs.common.interfaces.sse import CommandView

router = APIRouter()


@router.post(
    "/api/commands",
    status_code=202,
    responses={**errors(400, 401, 403, 422, 503), 409: {"model": CommandConflict}},
)
async def post_command(body: CommandRequest, request: Request, user: User) -> CommandAccepted:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "POST /api/commands")


@router.get("/api/commands", responses=errors(401, 422, 503))
async def list_commands(
    request: Request, user: User, vehicle: VehicleId | None = None, since: UtcTime | None = None
) -> list[CommandView]:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "GET /api/commands")
