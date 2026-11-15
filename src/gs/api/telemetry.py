"""GET /api/telemetry/{vehicle}/{param}: M4 chart buckets sized to the chart's pixel width."""

from datetime import timedelta
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query, Request
from psycopg import sql

from gs.api.bodies import Bucket
from gs.api.dbguard import connect, db_503
from gs.api.deps import ApiError, User, errors, mission, vehicles
from gs.common.interfaces.base import UtcTime, VehicleId

router = APIRouter()
MIN_BUCKET = timedelta(milliseconds=100)
MAX_WINDOW = timedelta(hours=24)
WIDTH_MIN, WIDTH_MAX, WIDTH_DEFAULT = 100, 4000, 1500


def _bad(error: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, error, message)


@router.get("/api/telemetry/{vehicle}/{param}", responses=errors(400, 401, 422, 503))
async def get_buckets(
    vehicle: VehicleId,
    param: str,
    request: Request,
    user: User,
    t_from: Annotated[UtcTime, Query(alias="from")],
    t_to: Annotated[UtcTime, Query(alias="to")],
    width: int = WIDTH_DEFAULT,
) -> list[Bucket]:
    if vehicle not in vehicles(request) or param not in mission(request).param_names:
        raise _bad("UNKNOWN_PARAM", f"unknown vehicle or parameter: {vehicle}/{param}")
    if not WIDTH_MIN <= width <= WIDTH_MAX:  # here, not as a Query bound: 400, not 422 (R-t)
        raise _bad("WIDTH_OUT_OF_RANGE", f"width must be {WIDTH_MIN}..{WIDTH_MAX}")
    if t_to <= t_from or t_to - t_from > MAX_WINDOW:
        raise _bad("WINDOW_TOO_LARGE", "window must be longer than 0 and at most 24 h")
    bucket = max((t_to - t_from) / width, MIN_BUCKET)  # a float timedelta, never rounded up
    col = sql.Identifier(param)  # D-DB-04: the name was checked against the mission db
    query = sql.SQL(
        "SELECT time_bucket(%s, t_onboard, %s) AS t, min({c}), max({c}), avg({c}),"
        " first({c}, t_onboard), last({c}, t_onboard), count({c}) FROM telemetry"
        " WHERE vehicle = %s AND t_onboard >= %s AND t_onboard < %s AND {c} IS NOT NULL"
        " GROUP BY 1 ORDER BY 1"
    ).format(c=col)
    with db_503(request):
        async with connect(request) as conn:
            cur = await conn.execute(query, (bucket, t_from, vehicle, t_from, t_to))
            rows = await cur.fetchall()
    # buckets start at `from`, so a window holds at most `width` of them; empty ones are absent
    return [
        Bucket(t=r[0], lo=r[1], hi=r[2], mean=r[3], first=r[4], last=r[5], n=r[6]) for r in rows
    ]
