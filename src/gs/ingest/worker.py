"""Ingest loop per vehicle: own it (advisory lock), drain pending entries, then read new ones;
classify, COMMIT, publish to ui:events, then XACK (D-ING-01/02/03/10/11/12/22)."""

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg
import redis.asyncio
import redis.exceptions

from gs.common.interfaces import DlqRecord, FrameHeader, Tm
from gs.ingest.errors import POISON_DELIVERIES, RETRIES, OperationalDbError, pg_error_class
from gs.ingest.events import GroundEvent, poison_key, reset_key
from gs.ingest.fold import Outcome, VehicleState, fold, load_vehicle, state_version
from gs.ingest.link import STALE_S, on_silence
from gs.ingest.owner import acquire, fence, release, waiting
from gs.ingest.store import insert_frames, load_engine_row, load_seq_state, write_outcome
from gs.ingest.validate import FieldStats, classify, poison

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
Item = tuple[str, dict[bytes, bytes], int, FrameHeader]  # entry id, fields, deliveries, frame


@dataclass(frozen=True)
class Timing:
    """Loop periods in seconds (design values; tests pass shorter ones)."""

    start_delay_s: float = 2.0  # non-home vehicles, once at startup (D-ING-10)
    handback_s: float = 5.0
    reclaim_s: float = 5.0
    tick_s: float = 5.0
    los_s: float = STALE_S
    hb_ttl_s: int = 3
    watchdog_s: float = 30.0


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
    homes: Mapping[str, str]  # vehicle -> home consumer (INGEST_HOMES)
    timing: Timing = Timing()
    owned: set[str] = field(default_factory=set)  # mutated only by each vehicle's own task


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
    `extra` events (ENGINE_STATE_RESET at start) ride in the same transaction. Data errors are
    isolated per frame (D-ING-04); an operational Postgres error rolls back, resets the batch's
    delivery counts and raises OperationalDbError (D-ING-24). Nothing is acked on any error."""
    if not entries:
        return vs, 0
    deliveries = await _deliveries(ctx, stream, entries)  # before classify (R8)
    # an entry already judged poison keeps its count; resetting it would start a new crash loop
    ids = [eid for (eid, _), n in zip(entries, deliveries, strict=True) if n <= POISON_DELIVERIES]
    try:
        return await _process(ctx, conn, stream, entries, deliveries, vs, extra)
    except psycopg.Error as e:
        # data errors are isolated inside _process; the only Postgres work after isolation is the
        # closing events write (event inserts ON CONFLICT DO NOTHING + the state upserts that
        # just committed per frame), which has no data-class failure, so this is operational
        await _reset_deliveries(ctx, stream, ids)
        raise OperationalDbError(_why(e)) from e
    except asyncio.CancelledError:  # a sibling vehicle's exit; this batch is not at fault
        await _reset_deliveries(ctx, stream, ids)
        raise


async def _process(
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    stream: str,
    entries: list[Entry],
    deliveries: list[int],
    vs: VehicleState,
    extra: Sequence[GroundEvent],
) -> tuple[VehicleState, int]:
    vehicle = stream.removeprefix("tm:")
    now = datetime.now(UTC)
    dlq: list[DlqRecord] = []
    events = list(extra)
    items: list[Item] = []
    for (eid, fields), n in zip(entries, deliveries, strict=True):
        if n > POISON_DELIVERIES:  # D-ING-04: not processed again
            dlq.append(poison(fields, stream, eid, n, f"delivered {n} times", now))
            events.append(_poison_event(stream, vehicle, eid, n))
            continue
        r = classify(fields, stream, eid, n, ctx.model, now)
        if isinstance(r, DlqRecord):
            dlq.append(r)
        else:
            ctx.stats.observe(r, ctx.params)
            items.append((eid, fields, n, r))
    try:
        nvs, dup, inserted = await _commit(ctx, conn, vehicle, [i[3] for i in items], vs, events)
    except Exception as e:  # noqa: BLE001  D-ING-04: a processing exception is the frame's fault
        if _operational(e):
            raise
        log.warning("data error in a batch of %s (%s); isolating frames", stream, _why(e))
        nvs, dup, inserted, bad = await _isolate(ctx, conn, stream, items, vs, events, now)
        dlq += bad
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
    for rec in dlq:
        ack.xadd(DLQ, {"data": rec.model_dump_json()})
    ack.xack(stream, GROUP, *(eid for eid, _ in entries))
    await ack.execute()
    ctx.dup_or_late[vehicle] += dup
    return nvs, len(inserted)


async def _commit(
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    vehicle: str,
    frames: list[FrameHeader],
    vs: VehicleState,
    events: Sequence[GroundEvent],
) -> tuple[VehicleState, int, list[FrameHeader]]:
    """fold + one transaction: frames (LATE frames still insert), then seq/link/events/state."""
    nvs, out = fold(vs, vehicle, frames, ctx.params)
    out = Outcome(out.new_frames, (*events, *out.events), out.dup_or_late)
    async with conn.transaction():  # COMMIT
        inserted = await insert_frames(conn, frames, ctx.params)
        await write_outcome(conn, vehicle, nvs, out)
    return nvs, out.dup_or_late, inserted


async def _isolate(
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    stream: str,
    items: list[Item],
    vs: VehicleState,
    events: list[GroundEvent],
    now: datetime,
) -> tuple[VehicleState, int, list[FrameHeader], list[DlqRecord]]:
    """Re-run one frame per transaction from the pre-batch state; a frame failing RETRIES times
    goes to the DLQ as poison, the rest commit (D-ING-04). Pending events (`extra`, POISON) ride
    in the next per-frame commit that succeeds. Operational errors propagate."""
    vehicle = stream.removeprefix("tm:")
    dup = 0
    inserted: list[FrameHeader] = []
    bad: list[DlqRecord] = []
    pending = list(events)
    for eid, fields, n, frame in items:
        why = ""
        for _ in range(RETRIES):
            try:
                vs, d, ins = await _commit(ctx, conn, vehicle, [frame], vs, pending)
            except Exception as e:  # noqa: BLE001  D-ING-04: a processing exception is the frame's fault
                if _operational(e):
                    raise
                why = _why(e)
                continue
            dup, inserted, pending = dup + d, inserted + ins, []
            break
        else:
            log.error("frame %s of %s failed %d times (%s); to DLQ", eid, stream, RETRIES, why)
            bad.append(poison(fields, stream, eid, n, why, now))
            pending.append(_poison_event(stream, vehicle, eid, n))
    if pending:  # no later frame committed: write them on their own, still before the XACK
        async with conn.transaction():
            await write_outcome(conn, vehicle, vs, Outcome((), tuple(pending), 0))
    return vs, dup, inserted, bad


def _poison_event(stream: str, vehicle: str, eid: str, n: int) -> GroundEvent:
    details = {"stream": stream, "entry_id": eid, "deliveries": n}
    return GroundEvent(poison_key(stream, eid), vehicle, "POISON", None, details)


def _operational(e: Exception) -> bool:
    return isinstance(e, psycopg.Error) and pg_error_class(e.sqlstate) == "operational"


def _why(e: Exception) -> str:
    code = e.sqlstate if isinstance(e, psycopg.Error) and e.sqlstate else type(e).__name__
    return f"{code}: {e}"


async def _reset_deliveries(ctx: Ctx, stream: str, ids: list[str]) -> None:
    """XCLAIM … 0 <ids> RETRYCOUNT 0 JUSTID (D-ING-24, R15 E1): times_delivered counts frames
    that kill us, not database failures while they were in flight."""
    if not ids:
        return
    try:
        msg_ids: list[Any] = list(ids)  # redis-py's stub wants a list of a union
        await ctx.redis.xclaim(stream, GROUP, ctx.consumer, 0, msg_ids, retrycount=0, justid=True)
    except redis.exceptions.RedisError as e:
        log.warning("could not reset delivery counts of %s (%s)", stream, e)


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
    try:
        await _run_vehicle(ctx, vehicle, dsn, stop)
    except psycopg.Error as e:  # connect, load, silence: no batch in flight, nothing to reset
        raise OperationalDbError(_why(e)) from e


async def _run_vehicle(ctx: Ctx, vehicle: str, dsn: str, stop: asyncio.Event) -> None:
    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True, application_name=ctx.consumer
    ) as conn:
        if ctx.homes.get(vehicle) != ctx.consumer:  # the home worker wins at startup (D-ING-10)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), ctx.timing.start_delay_s)
        while not stop.is_set() and await acquire(conn, vehicle, stop):
            ctx.loop_seen[vehicle] = time.monotonic()  # owned is never without a fresh pass
            ctx.owned.add(vehicle)
            try:
                await _own(ctx, conn, vehicle, stop)  # returns on stop or after a hand-back
            finally:
                ctx.owned.discard(vehicle)


async def _own(ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str, stop: asyncio.Event) -> None:
    """Takeover protocol (D-ING-11): load state, drain every pending entry of the group one at a
    time (XAUTOCLAIM … 0, Review Focus 2), then read new entries behind the pre-read fence."""
    stream = f"tm:{vehicle}"
    home = ctx.homes.get(vehicle)
    vs, extra = await load_state(ctx, conn, vehicle)
    drain, ready, backoff, cursor = True, False, BACKOFF_START_S, "0-0"
    last_new = next_handback = time.monotonic()
    while not stop.is_set():
        ctx.loop_seen[vehicle] = time.monotonic()
        try:
            if not ready:
                await _ensure_group(ctx.redis, stream)
                ready = True
            await fence(conn)
            if drain:
                res: Any = await ctx.redis.xautoclaim(stream, GROUP, ctx.consumer, 0, cursor, 1)
                cursor, claimed = res[0].decode(), res[1]
                drain = cursor != "0-0"
            else:
                res = await ctx.redis.xreadgroup(
                    GROUP, ctx.consumer, {stream: ">"}, count=COUNT, block=BLOCK_MS
                )
                claimed = res[0][1] if res else []
            entries: list[Entry] = [(eid.decode(), f or {}) for eid, f in claimed]
            nvs, _ = await process_batch(ctx, conn, stream, entries, vs, extra)
            if entries:
                extra = ()
            if nvs.seq != vs.seq:  # a new frame advanced the tracker
                last_new = time.monotonic()
            vs = nvs
            if time.monotonic() - last_new > ctx.timing.los_s:
                nvs = await _silence(conn, vehicle, vs, extra)
                if nvs is not vs:
                    vs, extra = nvs, ()
            backoff = BACKOFF_START_S
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            log.warning("redis unavailable for %s (%s); retry in %.1fs", stream, e, backoff)
            drain, ready, cursor = True, False, "0-0"  # D-ING-08: re-read pending after reconnect
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
            last_new = time.monotonic()  # a broker outage is not loss of signal (D-UI-04/15)
        if home not in (None, ctx.consumer) and time.monotonic() >= next_handback:
            next_handback = time.monotonic() + ctx.timing.handback_s
            if await waiting(conn, vehicle, home):  # between batches: the last one is acked
                log.info("handing %s back to %s", vehicle, home)
                await release(conn, vehicle)
                return
