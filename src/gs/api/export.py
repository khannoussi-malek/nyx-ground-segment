"""GET /api/export (D-EXT-13): a window of one vehicle as CSV or Parquet. The file is written by
the app's one-worker export process (export_calc), one export at a time (EX-02), then sent."""

import os
import tempfile
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from gs.api.dbguard import connect, db_503
from gs.api.deps import ApiError, User, errors, mission, vehicles
from gs.api.replays import _s3, _unavailable
from gs.api.workers import one_at_a_time, parameter_hashes, run_in_worker
from gs.common.interfaces.base import UtcTime, VehicleId

router = APIRouter()
MAX_WINDOW = timedelta(hours=24)
RETRY_AFTER_S = "5"
STAMP = "%Y%m%dT%H%M%SZ"  # file names: no colons


def utc_window(started: datetime, ended: datetime | None) -> tuple[datetime, datetime]:
    """A session's window in UTC: timestamptz arrives in the DB session's time zone, and the file
    name stamps end in a literal Z. A running session ends now."""
    return started.astimezone(UTC), (ended or datetime.now(UTC)).astimezone(UTC)


MEDIA = {"csv": "text/csv", "parquet": "application/vnd.apache.parquet"}


def _bad(error: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, error, message)


def _names(request: Request, text: str | None) -> list[str]:
    """The requested parameters in mission-db order; all of them when `params` is omitted."""
    known = mission(request).param_names
    if text is None:
        return list(known)
    asked = text.split(",")
    if len(set(asked)) != len(asked):
        raise _bad("INVALID_PARAMS", "parameters must be different")
    if unknown := [n for n in asked if n not in known]:
        raise _bad("UNKNOWN_PARAM", f"unknown parameter: {unknown[0]}")
    return [n for n in known if n in asked]


async def _check_vehicle(request: Request, vehicle: str) -> None:
    """Like /api/summary: a live vehicle or one the ingest ever stored a frame for."""
    from gs.api.summary import _known

    with db_503(request):
        async with connect(request) as conn:
            if vehicle not in vehicles(request) and not await _known(conn, vehicle):
                raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"unknown vehicle: {vehicle}")


async def _window(
    request: Request, session: int | None, vehicle: str | None, t_from: datetime | None,
    t_to: datetime | None,
) -> tuple[str, datetime, datetime]:  # fmt: skip
    if session is not None:
        if (vehicle, t_from, t_to) != (None, None, None):
            raise _bad("INVALID_WINDOW", "give a session or vehicle, from and to, not both")
        if not 0 < session < 2**63:
            raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown session")
        with db_503(request):
            async with connect(request) as conn:
                cur = await conn.execute(
                    "SELECT vehicle, started_at, ended_at FROM test_session WHERE id = %s",
                    (session,),
                )
                row = await cur.fetchone()
        if row is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown session")
        vehicle, (t_from, t_to) = row[0], utc_window(row[1], row[2])
    elif vehicle is None or t_from is None or t_to is None:
        raise _bad("INVALID_WINDOW", "give a session, or vehicle, from and to")
    else:
        await _check_vehicle(request, vehicle)
    if t_from >= t_to:
        raise _bad("INVALID_WINDOW", "from must be before to")
    if t_to - t_from > MAX_WINDOW:
        raise _bad("WINDOW_TOO_LARGE", "an export is at most 24 h; use several requests")
    return vehicle, t_from, t_to


@router.get(
    "/api/export",
    response_class=Response,
    responses={
        200: {"content": {m: {} for m in MEDIA.values()}},
        **errors(400, 401, 404, 409, 422, 429, 503),
    },
)
async def get_export(
    request: Request, user: User, vehicle: VehicleId | None = None,
    t_from: Annotated[UtcTime | None, Query(alias="from")] = None,
    t_to: Annotated[UtcTime | None, Query(alias="to")] = None,
    session: int | None = None, params: str | None = None,
    format: Literal["csv", "parquet"] = "csv",  # noqa: A002
) -> Response:  # fmt: skip
    window = await _window(request, session, vehicle, t_from, t_to)
    names = _names(request, params)
    with one_at_a_time(request, "export", {"Retry-After": RETRY_AFTER_S}):
        return await _export(request, window, names, format)


async def _export(
    request: Request, window: tuple[str, datetime, datetime], names: list[str], fmt: str
) -> Response:
    from gs.api import export_calc  # pyarrow: not at api start
    from gs.api.worker_calc import corrupt_text
    from gs.archiver.s3 import S3_ERRORS
    from gs.archiver.window import ArchiveCorrupt, archive_rows, check_rows

    st = request.app.state
    vehicle, t_from, t_to = window
    try:
        with db_503(request):
            async with connect(request) as conn:
                rows = await archive_rows(conn, vehicle, t_from, t_to)
                hashes = await parameter_hashes(conn, vehicle, t_from, t_to)
        await check_rows(_s3(st), st.s3_settings.bucket, rows)  # 409 before any byte is written
    except ArchiveCorrupt as e:
        raise ApiError(HTTPStatus.CONFLICT, "ARCHIVE_CORRUPT", corrupt_text(e)) from e
    except S3_ERRORS as e:
        raise _unavailable("archive store", e) from e
    fd, path = tempfile.mkstemp(prefix="nyx-export-", suffix=f".{fmt}")
    os.close(fd)
    try:
        await run_in_worker(
            request, "export", export_calc.export, st.database_url, st.s3_settings, vehicle,
            t_from, t_to, names, fmt, path, hashes,
        )  # fmt: skip
    except BaseException:
        os.unlink(path)
        raise
    name = f"{vehicle}_{t_from:{STAMP}}_{t_to:{STAMP}}.{fmt}"
    meta = export_calc.header(hashes)
    return FileResponse(
        path, media_type=MEDIA[fmt], filename=name, background=BackgroundTask(os.unlink, path),
        headers={f"X-{k.replace('_', '-').title()}": v for k, v in meta.items()},
    )  # fmt: skip
