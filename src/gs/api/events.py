"""GET /api/events: hello, snapshot of the latest rows, then tm deltas from ui:events (D-UI-02).

Order per connection (R2-08): remember the stream tail, build the snapshot, then XREAD after the
tail, so anything committed while the snapshot ran arrives as a delta. No `id:` lines: no resume.
"""

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import Request
from fastapi.responses import StreamingResponse
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import ValidationError

from gs.api import deps
from gs.api.deps import User
from gs.common.interfaces import Hb, Hello, Snapshot, Tm
from gs.common.interfaces.base import StrictModel
from gs.common.uievents import UI_EVENTS

SSE_RETRY_MS = 1000
HB_S = 1.0
log = logging.getLogger(__name__)


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def format_sse(event: str, model: StrictModel) -> bytes:
    return f"event: {event}\ndata: {model.model_dump_json()}\n\n".encode()


async def build_snapshot(
    conn: psycopg.AsyncConnection, vehicles: Sequence[str], params: Sequence[str], now: datetime
) -> Snapshot:
    cols = ["vehicle", "t_onboard", "seq", "mode", "last_cmd_id", *params]
    query = sql.SQL(
        "SELECT DISTINCT ON (vehicle) {} FROM telemetry WHERE vehicle = ANY(%s)"
        " ORDER BY vehicle, t_onboard DESC"
    ).format(sql.SQL(", ").join(map(sql.Identifier, cols)))
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, (list(vehicles),))
        rows = await cur.fetchall()
    out: dict[str, Tm] = {}
    for r in rows:
        t = r["t_onboard"].astimezone(UTC)
        out[r["vehicle"]] = Tm(
            vehicle=r["vehicle"],
            seq=r["seq"],
            t_onboard=t,
            mode=r["mode"],
            last_cmd_id=r["last_cmd_id"],
            values={p: r[p] for p in params},
            age_ms=_ms(now) - _ms(t),
        )
    return Snapshot(vehicles=out)


def _delta(data: str, now: datetime) -> Tm | None:
    try:
        tm = Tm.model_validate_json(data)
    except ValidationError as e:  # a poison entry must not kill every stream
        log.warning("skipping malformed ui:events entry: %s", e)
        return None
    return tm.model_copy(update={"age_ms": _ms(now) - _ms(tm.t_onboard)})


async def _stream(request: Request) -> AsyncIterator[bytes]:
    st = request.app.state
    st.sse_open += 1
    stop = asyncio.ensure_future(st.shutdown.wait())
    read: asyncio.Future[Any] | None = None
    try:
        yield f"retry: {SSE_RETRY_MS}\n\n".encode()
        yield format_sse("hello", Hello(server_ms=_ms(datetime.now(UTC))))
        tail = await st.redis.xrevrange(UI_EVENTS, count=1)
        last_id = tail[0][0] if tail else "0-0"
        async with st.pool.connection() as conn:
            snap = await build_snapshot(
                conn, deps.vehicles(request), deps.mission(request).param_names, datetime.now(UTC)
            )
        yield format_sse("snapshot", snap)
        last_hb = time.monotonic()
        while not st.shutdown.is_set():
            block = max(1, math.ceil((last_hb + HB_S - time.monotonic()) * 1000))
            read = asyncio.ensure_future(st.redis.xread({UI_EVENTS: last_id}, block=block))
            await asyncio.wait({read, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not read.done():  # shutdown: do not sit out the XREAD block
                break
            for _, entries in read.result() or []:
                for entry_id, fields in entries:
                    last_id = entry_id
                    if tm := _delta(fields["data"], datetime.now(UTC)):
                        yield format_sse("tm", tm)
            if time.monotonic() - last_hb >= HB_S:
                last_hb = time.monotonic()
                yield format_sse("hb", Hb(server_ms=_ms(datetime.now(UTC))))
    finally:  # also runs when the client disconnects (the response task is cancelled)
        stop.cancel()
        if read is not None:
            read.cancel()
        st.sse_open -= 1


async def events(request: Request, _user: User) -> StreamingResponse:
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
