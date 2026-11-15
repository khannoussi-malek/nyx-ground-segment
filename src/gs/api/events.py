"""GET /api/events: hello, snapshot, then the hub's messages and an hb every second (D-UI-02).

Order per connection (R2-08): subscribe to the hub first, then build the snapshot, so anything
committed while the snapshot ran arrives as a delta (the console dedupes by key). No `id:`
lines: no resume; every (re)connect gets a snapshot.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import StreamingResponse

from gs.api import deps
from gs.api.deps import User
from gs.api.hub import HB_S, Hub, format_sse, ms
from gs.api.hub import build_snapshot as build_snapshot  # re-exported: tests patch it here
from gs.common.interfaces import Ground, Hb, Hello

SSE_RETRY_MS = 1000


def _ground(hub: Hub) -> Literal["up", "down"]:
    return "up" if hub.up() else "down"


async def _stream(request: Request) -> AsyncIterator[bytes]:
    st = request.app.state
    hub: Hub = st.hub
    st.sse_open += 1
    client, waits = None, list[asyncio.Future[Any]]()
    try:
        yield f"retry: {SSE_RETRY_MS}\n\n".encode()
        yield format_sse("hello", Hello(server_ms=ms(datetime.now(UTC))))
        client = hub.subscribe()  # before the snapshot (R2-08)
        async with st.pool.connection() as conn:
            snap = await build_snapshot(
                conn, deps.vehicles(request), deps.mission(request).param_names, datetime.now(UTC)
            )
        yield format_sse("snapshot", snap)
        # always the broker state: the console keeps it across reconnects (Task 11 review)
        shown = _ground(hub)
        yield format_sse("ground", Ground(broker=shown))
        stop = asyncio.ensure_future(st.shutdown.wait())
        closed = asyncio.ensure_future(client.closed.wait())  # overflow: end, the client reconnects
        get = asyncio.ensure_future(client.queue.get())
        waits = [stop, closed, get]
        last_hb = time.monotonic()
        while True:
            left = max(0.0, last_hb + HB_S - time.monotonic())
            await asyncio.wait(
                {stop, closed, get}, timeout=left, return_when=asyncio.FIRST_COMPLETED
            )
            if stop.done() or closed.done():
                break
            if get.done():
                frame = get.result()
                if frame.startswith(b"event: ground\n"):  # the hub's own transition
                    shown = "up" if b'"broker":"up"' in frame else "down"
                yield frame
                get = waits[2] = asyncio.ensure_future(client.queue.get())
            if time.monotonic() - last_hb >= HB_S:
                last_hb = time.monotonic()
                if (now := _ground(hub)) != shown:  # a hung reader broadcasts nothing itself
                    shown = now
                    yield format_sse("ground", Ground(broker=now))
                hb = Hb(server_ms=ms(datetime.now(UTC)), processing=dict(hub.processing))
                yield format_sse("hb", hb)
    finally:  # also runs when the client disconnects (the response task is cancelled)
        for w in waits:
            w.cancel()
        if client is not None:
            hub.unsubscribe(client)
        st.sse_open -= 1


async def events(request: Request, _user: User) -> StreamingResponse:
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
