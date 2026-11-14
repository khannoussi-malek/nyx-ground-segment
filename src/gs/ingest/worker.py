"""Ingest loop: read tm:<vehicle>, classify, COMMIT, publish to ui:events, then XACK
(D-ING-01/02/03/22). Own pending entries are read first so a restart loses nothing."""

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis.asyncio
import redis.exceptions

from gs.common.interfaces import DlqRecord, FrameHeader, Tm
from gs.ingest.events import GroundEvent, reset_key
from gs.ingest.fold import Outcome, VehicleState, fold, load_vehicle, state_version
from gs.ingest.link import STALE_S, on_silence
from gs.ingest.store import insert_frames, load_engine_row, load_seq_state, write_outcome
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
    set_sha256: str  # mission-db set the vehicle state is built with
    dup_or_late: Counter[str]  # vehicle -> frames the tracker saw as duplicate or late


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
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    stream: str,
    entries: list[Entry],
    vs: VehicleState,
    extra: Sequence[GroundEvent] = (),
) -> tuple[VehicleState, int]:
    """One frame transaction (D-ING-22); returns the new state and the frames newly stored.
    `extra` events (ENGINE_STATE_RESET at start) ride in the same transaction.
    Any error propagates with nothing acked and the caller's state unchanged."""
    if not entries:
        return vs, 0
    vehicle = stream.removeprefix("tm:")
    deliveries = await _deliveries(ctx, stream, entries)  # before classify (R8)
    now = datetime.now(UTC)
    results = [
        classify(fields, stream, eid, n, ctx.model, now)
        for (eid, fields), n in zip(entries, deliveries, strict=True)
    ]
    frames = [r for r in results if isinstance(r, FrameHeader)]
    for f in frames:
        ctx.stats.observe(f, ctx.params)
    nvs, out = fold(vs, vehicle, frames, ctx.params)
    out = Outcome(out.new_frames, (*extra, *out.events), out.dup_or_late)
    async with conn.transaction():  # COMMIT
        inserted = await insert_frames(conn, frames, ctx.params)  # LATE frames still insert
        await write_outcome(conn, vehicle, nvs, out)
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
    ctx.dup_or_late[vehicle] += out.dup_or_late
    return nvs, len(inserted)


async def load_state(
    ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str
) -> tuple[VehicleState, tuple[GroundEvent, ...]]:
    """State at start; a row of another state version is ignored → ENGINE_STATE_RESET."""
    s = await load_seq_state(conn, vehicle)
    row = await load_engine_row(conn, vehicle)
    if row is None or row[0] == state_version():
        return load_vehicle(s, row[2] if row else None, ctx.set_sha256), ()
    vs = load_vehicle(s, None, ctx.set_sha256)
    if s is None:
        return vs, ()
    details = {"found_version": row[0], "version": state_version(), "epoch": s.epoch}
    key = reset_key(vehicle, s.epoch, s.last_seq)
    return vs, (GroundEvent(key, vehicle, "ENGINE_STATE_RESET", s.last_t, details),)


async def _silence(
    conn: psycopg.AsyncConnection, vehicle: str, vs: VehicleState, extra: Sequence[GroundEvent]
) -> VehicleState:
    """LOS after STALE_S without a new frame: LOS (+ pending `extra`) and engine_state in one
    small transaction. Returns `vs` unchanged when no LOS was due."""
    ls, ev = on_silence(vs.link, vehicle, vs.seq)
    if not ev:
        return vs
    nvs = VehicleState(vs.seq, ls, vs.frozen, vs.set_sha256)
    async with conn.transaction():
        await write_outcome(conn, vehicle, nvs, Outcome((), (*extra, *ev), 0))
    return nvs


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
        vs, extra = await load_state(ctx, conn, vehicle)
        last_new = time.monotonic()
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
                nvs, _ = await process_batch(ctx, conn, stream, entries, vs, extra)
                if entries:
                    extra = ()
                if nvs.seq != vs.seq:  # a new frame advanced the tracker
                    last_new = time.monotonic()
                vs = nvs
                if time.monotonic() - last_new > STALE_S:
                    nvs = await _silence(conn, vehicle, vs, extra)
                    if nvs is not vs:
                        vs, extra = nvs, ()
                backoff = BACKOFF_START_S
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
                log.warning("redis unavailable for %s (%s); retry in %.1fs", stream, e, backoff)
                backlog, ready = True, False  # D-ING-08: re-read our PEL after reconnect
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)
                last_new = time.monotonic()  # a broker outage is not loss of signal (D-UI-04/15)
