"""GET /api/telemetry/{vehicle}/{param}: M4 chart buckets (route stub; Track D Task 23)."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query, Request

from gs.api.bodies import Bucket
from gs.api.deps import ApiError, User, errors
from gs.common.interfaces.base import UtcTime, VehicleId

router = APIRouter()


@router.get("/api/telemetry/{vehicle}/{param}", responses=errors(400, 401, 422, 503))
async def get_buckets(
    vehicle: VehicleId,
    param: str,
    request: Request,
    user: User,
    t_from: Annotated[UtcTime, Query(alias="from")],
    t_to: Annotated[UtcTime, Query(alias="to")],
    width: int = 1500,
) -> list[Bucket]:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, "NOT_IMPLEMENTED", "GET /api/telemetry")
