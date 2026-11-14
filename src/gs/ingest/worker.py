"""Ingest loop: read tm:<vehicle>, classify, COMMIT, publish to ui:events, then XACK
(D-ING-01/02/03/22). Own pending entries are read first so a restart loses nothing."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis.asyncio
import redis.exceptions

from gs.common.interfaces import DlqRecord, FrameHeader, Tm
from gs.ingest.store import insert_frames
from gs.ingest.validate import FieldStats, classify

log = logging.getLogger(__name__)

GROUP = "ingest"
COUNT = 100
BLOCK_MS = 1000
UI_EVENTS = "ui:events"
UI_MAXLEN = 3000
DLQ = "tm:dlq"
BACKOFF_START_S = 0.5
BACKOFF_MAX_S = 5.0
Entry = tuple[str, dict[bytes, bytes]]


@dataclass(frozen=True)
class Ctx:
    consumer: str
    model: type[FrameHeader]
    params: Sequence[str]
    redis: redis.asyncio.Redis
    stats: FieldStats
    loop_seen: dict[str, float]  # vehicle -> time.monotonic() of the last loop pass


def to_tm(frame: FrameHeader, params: Sequence[str]) -> Tm:
    return Tm(
        vehicle=frame.vehicle,
        seq=frame.seq,
        t_onboard=frame.t_onboard,
        mode=frame.mode,
        last_cmd_id=frame.last_cmd_id,
        values={p: getattr(frame, p) for p in params},
    )


async def _deliveries(ctx: Ctx, stream: str, entries: list[Entry]) -> list[int]:
    """times_delivered per entry from XPENDING; 1 when the entry is not in the PEL (a replay)."""
    pipe = ctx.redis.pipeline(transaction=False)
    for eid, _ in entries:
        pipe.xpending_range(stream, GROUP, eid, eid, 1)
    found: list[list[dict[str, Any]]] = await pipe.execute()
    return [rows[0]["times_delivered"] if rows else 1 for rows in found]


async def process_batch(
    ctx: Ctx, conn: psycopg.AsyncConnection, stream: str, entries: list[Entry]
) -> int:
    """Returns the number of frames newly stored. Any error propagates with nothing acked."""
    if not entries:
        return 0
    deliveries = await _deliveries(ctx, stream, entries)  # before classify (R8)
    now = datetime.now(UTC)
    results = [
        classify(fields, stream, eid, n, ctx.model, now)
        for (eid, fields), n in zip(entries, deliveries, strict=True)
    ]
    frames = [r for r in results if isinstance(r, FrameHeader)]
    for f in frames:
        ctx.stats.observe(f, ctx.params)
    async with conn.transaction():  # COMMIT
        inserted = await insert_frames(conn, frames, ctx.params)
    if inserted:  # publish only what this commit stored; a redelivery publishes nothing
        pub = ctx.redis.pipeline(transaction=False)
        for f in inserted:
            pub.xadd(
                UI_EVENTS,
                {"data": to_tm(f, ctx.params).model_dump_json()},
                maxlen=UI_MAXLEN,
                approximate=True,
            )
        await pub.execute()
    ack = ctx.redis.pipeline(transaction=True)  # one MULTI: DLQ records + XACK
    for r in results:
        if isinstance(r, DlqRecord):
            ack.xadd(DLQ, {"data": r.model_dump_json()})
    ack.xack(stream, GROUP, *(eid for eid, _ in entries))
    await ack.execute()
    return len(inserted)


async def _ensure_group(r: redis.asyncio.Redis, stream: str) -> None:
    try:
        await r.xgroup_create(stream, GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def run_vehicle(ctx: Ctx, vehicle: str, dsn: str, stop: asyncio.Event) -> None:
    stream = f"tm:{vehicle}"
    backlog, ready, backoff = True, False, BACKOFF_START_S
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, application_name=ctx.consumer
    ) as conn:
        while not stop.is_set():
            ctx.loop_seen[vehicle] = time.monotonic()
            try:
                if not ready:
                    await _ensure_group(ctx.redis, stream)
                    ready = True
                # id 0 = our own pending entries (never blocks); > = new entries
                res: Any = await ctx.redis.xreadgroup(
                    GROUP,
                    ctx.consumer,
                    {stream: "0" if backlog else ">"},
                    count=COUNT,
                    block=None if backlog else BLOCK_MS,
                )
                entries: list[Entry] = [
                    (eid.decode(), fields or {}) for eid, fields in (res[0][1] if res else [])
                ]
                if backlog and not entries:
                    backlog = False
                await process_batch(ctx, conn, stream, entries)
                backoff = BACKOFF_START_S
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
                log.warning("redis unavailable for %s (%s); retry in %.1fs", stream, e, backoff)
                backlog, ready = True, False  # D-ING-08: re-read our PEL after reconnect
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
