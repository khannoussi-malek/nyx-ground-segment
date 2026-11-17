"""Replays of recorded data over SSE (D-REP-01..06). Replay never takes ACKs or sounds (D-REP-03);
speed is not a security boundary. Replays live in this process: `app.state.replays`."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from http import HTTPStatus
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from psycopg_pool import PoolTimeout

from gs.api import deps
from gs.api.alarms import _db
from gs.api.bodies import ReplayCreated, ReplayRequest
from gs.api.deps import ApiError, User, errors
from gs.api.hub import format_sse
from gs.archiver.s3 import S3_ERRORS
from gs.archiver.window import ArchiveCorrupt, has_data, verify_window
from gs.common.interfaces.replay import ReplayEnd
from gs.replay.run import replay_messages

MAX_WINDOW = timedelta(hours=24)
REPLAY_IDLE_S = 60  # a created replay nobody GETs within this long is dropped
DB_CONNECT_S = 5
SSE_RETRY_MS = 1000  # as the live /api/events
STREAM_ERRORS: tuple[type[BaseException], ...] = (psycopg.Error, PoolTimeout, *S3_ERRORS)
log = logging.getLogger(__name__)
router = APIRouter()


@dataclass(eq=False)
class Replay:
    id: UUID
    user: str
    req: ReplayRequest
    created: float
    stop: asyncio.Event
    taken: bool = field(default=False)  # the first GET owns the stream


def _registry(request: Request) -> dict[UUID, Replay]:
    reg: dict[UUID, Replay] = request.app.state.replays
    return reg


def _own(request: Request, replay_id: UUID, user: str) -> Replay:
    r = _registry(request).get(replay_id)
    if r is None or r.user != user:  # another user's replay is not visible
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "replay not found")
    return r


def _unavailable(what: str, e: BaseException) -> ApiError:
    log.warning("replay: %s: %s: %s", what, type(e).__name__, e)
    return ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", f"{what} unreachable")


@router.post("/api/replays", status_code=201, responses=errors(400, 401, 403, 404, 409, 422, 503))
async def create_replay(body: ReplayRequest, request: Request, user: User) -> ReplayCreated:
    if body.t_from >= body.t_to:
        raise ApiError(HTTPStatus.BAD_REQUEST, "INVALID_WINDOW", "from must be before to")
    if body.t_to - body.t_from > MAX_WINDOW:
        raise ApiError(HTTPStatus.BAD_REQUEST, "WINDOW_TOO_LARGE", "window is longer than 24 h")
    st = request.app.state
    try:
        async with _db(request) as conn:
            if not await has_data(conn, body.vehicle, body.t_from, body.t_to):
                raise ApiError(HTTPStatus.NOT_FOUND, "NO_DATA", "no data in window")
            await verify_window(conn, st.s3, st.s3_bucket, body.vehicle, body.t_from, body.t_to)
    except ArchiveCorrupt as e:
        raise ApiError(HTTPStatus.CONFLICT, "ARCHIVE_CORRUPT", str(e)) from e
    except S3_ERRORS as e:
        raise _unavailable("archive store", e) from e
    r = Replay(uuid4(), user, body, time.monotonic(), asyncio.Event())
    st.replays[r.id] = r

    def expire() -> None:
        if not r.taken and st.replays.pop(r.id, None) is not None:
            r.stop.set()

    asyncio.get_running_loop().call_later(REPLAY_IDLE_S, expire)
    return ReplayCreated(replay_id=r.id)


@router.delete(
    "/api/replays/{replay_id}",
    status_code=204,
    response_class=Response,
    responses=errors(401, 403, 404, 422),
)
async def delete_replay(replay_id: UUID, request: Request, user: User) -> Response:
    r = _own(request, replay_id, user)
    _registry(request).pop(replay_id, None)
    r.stop.set()
    return Response(status_code=204)


async def _stream(request: Request, r: Replay) -> AsyncIterator[bytes]:
    st = request.app.state

    async def sleep(s: float) -> None:  # a stop ends the wait at once
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(r.stop.wait(), s)

    try:
        yield f"retry: {SSE_RETRY_MS}\n\n".encode()
        # its own connection: a replay lasts up to hours and must not starve the pool
        async with await psycopg.AsyncConnection.connect(
            st.database_url,
            autocommit=True,
            connect_timeout=DB_CONNECT_S,
            options="-c default_transaction_read_only=on",  # D-REP-04: replay never writes
        ) as conn:
            msgs = replay_messages(
                conn, st.s3, st.s3_bucket, deps.mission(request), r.id, r.req.vehicle,
                r.req.t_from, r.req.t_to, r.req.speed, sleep,
            )  # fmt: skip
            async with contextlib.aclosing(msgs):
                async for m in msgs:
                    if r.stop.is_set():
                        yield format_sse("replay_end", ReplayEnd(reason="stopped"))
                        return
                    yield format_sse(m.kind, m)
    except STREAM_ERRORS as e:  # the stream ends; the console reconnects
        log.warning("replay %s failed: %s: %s", r.id, type(e).__name__, e)
    finally:  # also on client disconnect (the response task is cancelled)
        st.replays.pop(r.id, None)


@router.get(
    "/api/replays/{replay_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}, **errors(401, 404, 422)},
)
async def replay_events(replay_id: UUID, request: Request, user: User) -> StreamingResponse:
    """SSE of gs.common.interfaces.replay messages (ReplayEvent), framed like /api/events."""
    r = _own(request, replay_id, user)
    if r.taken:
        raise ApiError(HTTPStatus.NOT_FOUND, "NOT_FOUND", "replay not found")
    r.taken = True
    return StreamingResponse(
        _stream(request, r),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
