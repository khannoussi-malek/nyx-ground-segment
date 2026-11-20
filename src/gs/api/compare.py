"""GET /api/compare (D-EXT-11): sessions overlaid on one bucket width. The reading and the maths
run in the app's one-worker compare process (compare_calc), one compare at a time (EX-02)."""

import logging
from datetime import UTC, datetime, timedelta
from http import HTTPStatus

from fastapi import APIRouter, Request

from gs.api.bodies import Compare, CompareBucket, CompareSeries, CompareSession
from gs.api.dbguard import connect, db_503
from gs.api.deps import ApiError, User, errors, mission
from gs.api.replays import _s3, _unavailable
from gs.api.workers import one_at_a_time, run_in_worker

router = APIRouter()
MAX_WINDOW = timedelta(hours=24)
SESSIONS = (2, 6)
PARAMS = (1, 4)
log = logging.getLogger(__name__)


def _bad(error: str, message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, error, message)


def _ids(text: str) -> list[int]:
    try:
        ids = [int(x) for x in text.split(",")]
    except ValueError:
        raise _bad("INVALID_SESSIONS", "sessions: comma-separated session ids") from None
    if not SESSIONS[0] <= len(ids) <= SESSIONS[1] or len(set(ids)) != len(ids):
        raise _bad("INVALID_SESSIONS", "give 2 to 6 different sessions")
    if any(not 0 < i < 2**63 for i in ids):
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown session")
    return ids


def _params(request: Request, text: str) -> list[str]:
    names = text.split(",") if text else []
    if not PARAMS[0] <= len(names) <= PARAMS[1] or len(set(names)) != len(names):
        raise _bad("INVALID_PARAMS", "give 1 to 4 different parameters")
    unknown = [n for n in names if n not in mission(request).param_names]
    if unknown:
        raise _bad("UNKNOWN_PARAM", f"unknown parameter: {unknown[0]}")
    return names


async def _sessions(
    request: Request, ids: list[int]
) -> list[tuple[int, str, str, datetime, datetime | None]]:
    async with connect(request) as conn:
        cur = await conn.execute(
            "SELECT id, vehicle, name, started_at, ended_at FROM test_session WHERE id = ANY(%s)",
            (ids,),
        )
        found = {r[0]: r for r in await cur.fetchall()}
    if len(found) != len(ids):
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "unknown session")
    return [
        (i, v, n, s.astimezone(UTC), e and e.astimezone(UTC))
        for i, v, n, s, e in (found[i] for i in ids)
    ]


@router.get("/api/compare", responses=errors(400, 401, 404, 409, 422, 429, 503))
async def get_compare(request: Request, user: User, sessions: str, params: str) -> Compare:
    ids, names = _ids(sessions), _params(request, params)
    with one_at_a_time(request, "compare"):
        return await _compare(request, ids, names)


async def _compare(request: Request, ids: list[int], names: list[str]) -> Compare:
    from gs.api import compare_calc  # pyarrow: not at api start
    from gs.api.worker_calc import corrupt_text
    from gs.archiver.s3 import S3_ERRORS  # lazy: see replays._s3
    from gs.archiver.window import ArchiveCorrupt, archive_rows, check_rows

    st = request.app.state
    now = datetime.now(UTC)
    try:
        with db_503(request):
            found = await _sessions(request, ids)
            ends = [compare_calc.session_end(e, now) for *_, e in found]
            spans = [e - s for (_, _, _, s, _), e in zip(found, ends, strict=True)]
            if max(spans) > MAX_WINDOW:
                raise _bad("WINDOW_TOO_LARGE", "a session is longer than 24 h")
            async with connect(request) as conn:
                rows = [await archive_rows(conn, v, s, e) for (_, v, _, s, _), e in
                        zip(found, ends, strict=True)]  # fmt: skip
        for r in rows:  # no connection held: MinIO reads
            await check_rows(_s3(st), st.s3_settings.bucket, r)
        width = compare_calc.bucket_width_s([sp.total_seconds() for sp in spans])
        jobs = [(v, s, e) for (_, v, _, s, _), e in zip(found, ends, strict=True)]
        out = await run_in_worker(
            request, "compare", compare_calc.compute, st.database_url, st.s3_settings, jobs,
            names, width,
        )  # fmt: skip
    except ArchiveCorrupt as e:
        log.warning("compare: %s", corrupt_text(e))
        raise ApiError(HTTPStatus.CONFLICT, "ARCHIVE_CORRUPT", corrupt_text(e)) from e
    except S3_ERRORS as e:
        raise _unavailable("archive store", e) from e
    series = iter(out)
    return Compare(
        bucket_s=width,
        sessions=[
            CompareSession(id=i, vehicle=v, name=n, started_at=s, ended_at=e,
                           duration_s=sp.total_seconds())
            for (i, v, n, s, e), sp in zip(found, spans, strict=True)
        ],
        series=[
            CompareSeries(session_id=i, param=p,
                          buckets=[CompareBucket(t_rel_s=t, lo=lo, hi=hi, mean=m, n=c)
                                   for t, lo, hi, m, c in next(series)])
            for (i, *_) in found for p in names
        ],
    )  # fmt: skip
