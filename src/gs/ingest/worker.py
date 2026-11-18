"""Ingest loop per vehicle: own it (advisory lock), drain pending entries, then read new ones;
classify, COMMIT, publish to ui:events, then XACK (D-ING-01/02/03/10/11/12/22)."""

import asyncio
import contextlib
import functools
import logging
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
import redis.asyncio
import redis.exceptions

from gs.alarms.engine import CompiledSet, compile_set
from gs.alarms.state import load, rebuild
from gs.commanding.tracker import track_pass
from gs.common.hooks import Hooks
from gs.common.interfaces import DlqRecord, FrameHeader, Tm, build_frame_model
from gs.common.interfaces.sse import Alarm, Link
from gs.common.interfaces.ui_event import Resync, UiMessage
from gs.common.missiondb import MissionDb, MissionDbError
from gs.common.uievents import publish_ui
from gs.common.views import alarm_views
from gs.ingest.acks import AckReq, apply_acks, db_now, pending_acks
from gs.ingest.errors import POISON_DELIVERIES, RETRIES, OperationalDbError, pg_error_class
from gs.ingest.events import GroundEvent, poison_key, reset_key, to_link
from gs.ingest.fold import Outcome, VehicleState, fold, load_vehicle, state_version
from gs.ingest.link import STALE_S, on_silence
from gs.ingest.owner import acquire, fence, release, waiting
from gs.ingest.store import (
    insert_frames,
    load_cset,
    load_engine_row,
    load_open_alarms,
    load_seq_state,
    write_outcome,
)
from gs.ingest.validate import FieldStats, classify, poison

log = logging.getLogger(__name__)

GROUP = "ingest"
COUNT = 100
BLOCK_MS = 1000
DLQ = "tm:dlq"
BACKOFF_START_S = 0.5
BACKOFF_MAX_S = 5.0
SOCKET_TIMEOUT_S = 5  # Redis client socket timeout; must exceed the 1 s XREADGROUP block
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
    mission: Callable[[], MissionDb]  # the current set (MissionWatcher.current)
    redis: redis.asyncio.Redis
    stats: FieldStats
    loop_seen: dict[str, float]  # vehicle -> time.monotonic() of the last loop pass
    dup_or_late: Counter[str]  # vehicle -> frames the tracker saw as duplicate or late
    homes: Mapping[str, str]  # vehicle -> home consumer (INGEST_HOMES)
    timing: Timing = Timing()
    vehicles: tuple[str, ...] = ()  # every vehicle this worker may own (steal checks)
    steal_idle_ms: int = 30_000  # STEAL_IDLE_MS (D-ING-12; CI 3 000)
    reclaim_idle_ms: int = 30_000  # RECLAIM_IDLE_MS (D-ING-26; CI 3 000)
    owned: set[str] = field(default_factory=set)  # mutated only by each vehicle's own task
    reconnected_at: list[float | None] = field(default_factory=lambda: [None])  # monotonic
    used_memory: list[int | None] = field(default_factory=lambda: [None])  # Redis INFO
    max_memory: list[int | None] = field(default_factory=lambda: [None])  # Redis maxmemory
    suspects: dict[str, int] = field(default_factory=dict)  # vehicle -> holder pid over idle
    hooks: Hooks = Hooks()  # D-ENG-16 test hooks; all off by default
    # ponytail: never evicted; one entry per set this worker ever saw (a handful per run)
    csets: dict[str, CompiledSet] = field(default_factory=dict)  # set_sha256 -> compiled
    # cmd_tracker_errors, interface_mismatch (D-CMD-05, D-ING-27; /healthz reports both)
    counters: Counter[str] = field(default_factory=Counter)
    # vehicle -> alarm -> when news an ACK must not hide (a RAISE/ESCALATE, or any late state)
    # reached the consoles, database clock (P-pf42b: margins count from the publish); pruned
    # to the open alarms at each stamp
    shown: dict[str, dict[UUID, datetime]] = field(default_factory=dict)
    # vehicle -> DLQ reason counts since its last new frame (named by the next LOS, D-UI-25)
    rejected: dict[str, Counter[str]] = field(default_factory=dict)


class Unpublished(redis.exceptions.ConnectionError):
    """A Redis error after the batch COMMIT: carries the committed state (an applied ACK is
    never redelivered, so the loop must keep it) and the messages not yet published."""

    def __init__(self, vs: VehicleState, msgs: list[UiMessage]) -> None:
        super().__init__("redis error after the commit")
        self.vs, self.msgs = vs, msgs


# ponytail: one model per parameter list ever seen, never evicted (same handful)
_frame_model = functools.cache(build_frame_model)


@dataclass(frozen=True)
class Batch:
    """The mission-db set a batch is processed with, fixed at the batch start (D-ALM-12)."""

    mdb: MissionDb
    cset: CompiledSet
    old: CompiledSet | None  # the set the vehicle's engine was built with, if another one


def _current(ctx: Ctx) -> tuple[MissionDb, CompiledSet]:
    mdb = ctx.mission()
    if mdb.set_sha256 not in ctx.csets:
        ctx.csets[mdb.set_sha256] = compile_set(mdb)
    return mdb, ctx.csets[mdb.set_sha256]


async def _stored(ctx: Ctx, conn: psycopg.AsyncConnection, set_sha256: str) -> CompiledSet:
    """A set from limits_versions (MissionDbError if it no longer validates)."""
    if set_sha256 not in ctx.csets:
        ctx.csets[set_sha256] = await load_cset(conn, set_sha256)
    return ctx.csets[set_sha256]


async def _batch(ctx: Ctx, conn: psycopg.AsyncConnection, vs: VehicleState) -> Batch:
    mdb, cset = _current(ctx)
    same = vs.set_sha256 in ("", cset.set_sha256)
    return Batch(mdb, cset, None if same else await _stored(ctx, conn, vs.set_sha256))


def to_tm(frame: FrameHeader, params: Sequence[str], frozen: bool = False) -> Tm:
    return Tm(
        vehicle=frame.vehicle,
        seq=frame.seq,
        t_onboard=frame.t_onboard,
        mode=frame.mode,
        last_cmd_id=frame.last_cmd_id,
        values={p: getattr(frame, p) for p in params},
        frozen=frozen,
    )


def _links(events: Sequence[GroundEvent], vs: VehicleState) -> list[Link]:
    """One `link` per committed ground event (clients dedupe by key), each logged with vehicle
    and the tracker's last seq (ENG-05, P-pf9)."""
    seq = None if vs.seq is None else vs.seq.last_seq
    for e in events:
        log.info("%s %s", e.kind, e.key, extra={"vehicle": e.vehicle, "seq": seq})
    return [to_link(e) for e in events]


async def _stamp_shown(
    ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str, ids: set[UUID], vs: VehicleState
) -> None:
    """After a successful publish: these alarms are on screen from now (a slow commit or
    publish moves the ACK margin with it, P-pf42b). Closed alarms are dropped."""
    if ids:
        open_ids = {a.alarm_id for a in vs.engine.alarms.values()}
        t = await db_now(conn)
        kept = {a: at for a, at in ctx.shown.get(vehicle, {}).items() if a in open_ids}
        ctx.shown[vehicle] = {**kept, **dict.fromkeys(ids & open_ids, t)}


def _news(outs: Sequence[Outcome]) -> set[UUID]:
    """Alarms an in-time publish shows a RAISE/ESCALATE for; an RTN or ACK is no news an ACK
    could hide, and stamping it would supersede an ACK right after it."""
    return {t.alarm_id for o in outs for t in o.transitions if t.kind in ("RAISE", "ESCALATE")}


def _shown_now(msgs: Sequence[UiMessage], vs: VehicleState) -> set[UUID]:
    """Alarms whose state reaches the consoles only with this (late) publish: every alarm in a
    held message, whatever its last transition (an ESCALATE then RTN is still news), and after
    a start or takeover (a Resync) every open alarm, since a new owner can't know what an
    earlier one published (P-pf42b)."""
    ids = {m.alarm.alarm_id for m in msgs if isinstance(m, Alarm)}
    if any(isinstance(m, Resync) for m in msgs):
        ids |= {a.alarm_id for a in vs.engine.alarms.values()}
    return ids


async def _ui(
    conn: psycopg.AsyncConnection,
    vs: VehicleState,
    stored: Sequence[FrameHeader],
    outs: Sequence[Outcome],
    params: Sequence[str],
) -> list[UiMessage]:
    """After COMMIT (D-ING-22): `tm` per stored frame, `link` per committed event, `alarm` per
    alarm the commits touched (its view after them)."""
    tm = [to_tm(f, params, vs.frozen.reported) for f in stored]
    links = _links([e for o in outs for e in o.events], vs)
    ids = list(dict.fromkeys([*(t.alarm_id for o in outs for t in o.transitions),
                              *(a for o in outs for a in o.acked)]))  # fmt: skip
    views = await alarm_views(conn, alarm_ids=ids, open_only=False) if ids else []
    return [*tm, *links, *(Alarm(alarm=a) for a in views)]


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
    acks: Sequence[AckReq] = (),
) -> tuple[VehicleState, int]:
    """One frame transaction (D-ING-22); returns the new state and the frames newly stored.
    Pending ACK requests are applied first in it (D-ALM-14); with no entries they commit on
    their own (ruling R-x). Data errors are isolated per frame (D-ING-04); an operational
    Postgres error rolls back, resets the batch's delivery counts and raises
    OperationalDbError (D-ING-24). Nothing is acked on any error."""
    if not entries and not acks:
        return vs, 0
    deliveries = await _deliveries(ctx, stream, entries)  # before classify (R8)
    # an entry already judged poison keeps its count; resetting it would start a new crash loop
    ids = [eid for (eid, _), n in zip(entries, deliveries, strict=True) if n <= POISON_DELIVERIES]
    try:
        return await _process(ctx, conn, stream, entries, deliveries, vs, acks)
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
    acks: Sequence[AckReq],
) -> tuple[VehicleState, int]:
    vehicle = stream.removeprefix("tm:")
    now = datetime.now(UTC)
    b = await _batch(ctx, conn, vs)
    params = b.mdb.param_names
    model = _frame_model(params)
    dlq: list[DlqRecord] = []
    events: list[GroundEvent] = []
    items: list[Item] = []
    for (eid, fields), n in zip(entries, deliveries, strict=True):
        if n > POISON_DELIVERIES:  # D-ING-04: not processed again
            dlq.append(poison(fields, stream, eid, n, f"delivered {n} times", now))
            events.append(_poison_event(stream, vehicle, eid, n))
            continue
        r = classify(fields, stream, eid, n, model, now)
        if isinstance(r, DlqRecord):
            dlq.append(r)
        else:
            ctx.hooks.frame(vehicle, r.seq)  # every delivery, before the transaction
            ctx.stats.observe(r, params)
            items.append((eid, fields, n, r))
    try:
        frames = [i[3] for i in items]
        nvs, out, inserted = await _commit(ctx, conn, b, vehicle, frames, vs, events, acks)
        outs = [out]
    except Exception as e:  # noqa: BLE001  D-ING-04: a processing exception is the frame's fault
        if _operational(e):
            raise
        log.warning("data error in a batch of %s (%s); isolating frames", stream, _why(e))
        nvs, inserted, bad, outs = await _isolate(
            ctx, conn, b, stream, items, vs, events, acks, now
        )
        dlq += bad
    ctx.dup_or_late[vehicle] += sum(o.dup_or_late for o in outs)
    ctx.hooks.fire("after_commit_before_publish")
    # tm only for what this commit stored (a redelivery publishes none); links and alarms may
    # repeat on a redelivery
    msgs = await _ui(conn, nvs, inserted, outs, params)
    try:
        await publish_ui(ctx.redis, msgs)
        await _stamp_shown(ctx, conn, vehicle, _news(outs), nvs)
        msgs = []
        ctx.hooks.fire("after_publish_before_ack")
        if entries:  # an ACK-only pass has nothing to ack
            ack = ctx.redis.pipeline(transaction=True)  # one MULTI: DLQ records + XACK
            for rec in dlq:
                ack.xadd(DLQ, {"data": rec.model_dump_json()})
            ack.xack(stream, GROUP, *(eid for eid, _ in entries))
            await ack.execute()
            late = dlq  # counted once the DLQ write is done (a redelivery would count twice)
            if nvs.seq != vs.seq:  # a new frame ends the earlier rejections: only the entries
                ctx.rejected.pop(vehicle, None)  # after the batch's last stored frame still count
                dlq_ids = {r.entry_id for r in dlq}
                last = max((_eid(i[0]) for i in items if i[0] not in dlq_ids), default=None)
                late = [r for r in dlq if last is None or _eid(r.entry_id) > last]
            if late:
                ctx.rejected.setdefault(vehicle, Counter()).update(r.reason for r in late)
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
        raise Unpublished(nvs, msgs) from e  # the entries stay pending and are re-read
    ctx.hooks.fire("after_xack")
    return nvs, len(inserted)


def _eid(entry_id: str) -> tuple[int, int]:
    ms, _, n = entry_id.partition("-")
    return int(ms), int(n or 0)


async def _commit(
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    b: Batch,
    vehicle: str,
    frames: list[FrameHeader],
    vs: VehicleState,
    events: Sequence[GroundEvent],
    acks: Sequence[AckReq] = (),
) -> tuple[VehicleState, Outcome, list[FrameHeader]]:
    """One transaction: the ACK requests (each in a savepoint, positioned after the last frame
    before this batch, D-ALM-14), then fold, frames (LATE frames still insert), seq/link/events
    (incl. LIMITS_APPLIED), alarm transitions and the engine snapshot; returns what it
    committed."""
    async with conn.transaction():  # COMMIT
        es = await apply_acks(conn, vehicle, vs.engine, acks, vs.seq)
        nvs, out = fold(replace(vs, engine=es), vehicle, frames, b.mdb, b.cset, b.old)
        acked = tuple(r.alarm_id for r in acks)
        out = replace(out, events=(*events, *out.events), acked=acked)
        inserted = await insert_frames(conn, frames, b.mdb.param_names)
        await write_outcome(conn, vehicle, nvs, out)
        ctx.hooks.fire("before_commit")
    return nvs, out, inserted


async def _isolate(
    ctx: Ctx,
    conn: psycopg.AsyncConnection,
    b: Batch,
    stream: str,
    items: list[Item],
    vs: VehicleState,
    events: list[GroundEvent],
    acks: Sequence[AckReq],
    now: datetime,
) -> tuple[VehicleState, list[FrameHeader], list[DlqRecord], list[Outcome]]:
    """Re-run one frame per transaction from the pre-batch state; a frame failing RETRIES times
    goes to the DLQ as poison, the rest commit (D-ING-04). Pending events (POISON) and the ACK
    requests ride in the first per-frame commit that succeeds (P-pf15; a rolled-back try
    leaves the requests pending). Operational errors propagate. Returns the committed
    outcomes too."""
    vehicle = stream.removeprefix("tm:")
    outs: list[Outcome] = []
    inserted: list[FrameHeader] = []
    bad: list[DlqRecord] = []
    pending, unapplied = list(events), list(acks)
    for eid, fields, n, frame in items:
        why = ""
        for _ in range(RETRIES):
            try:
                vs, out, ins = await _commit(ctx, conn, b, vehicle, [frame], vs, pending, unapplied)
            except Exception as e:  # noqa: BLE001  D-ING-04: a processing exception is the frame's fault
                if _operational(e):
                    raise
                why = _why(e)
                continue
            outs, inserted, pending, unapplied = [*outs, out], inserted + ins, [], []
            break
        else:
            log.error("frame %s of %s failed %d times (%s); to DLQ", eid, stream, RETRIES, why)
            bad.append(poison(fields, stream, eid, n, why, now))
            pending.append(_poison_event(stream, vehicle, eid, n))
    if pending or unapplied:  # no later frame committed: on their own, still before the XACK
        vs, rest, _ = await _commit(ctx, conn, b, vehicle, [], vs, pending, unapplied)
        outs.append(rest)
    return vs, inserted, bad, outs


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
    """State at start (D-ING-11, D-ALM-11). The snapshot is loaded with the set it was built
    with (G18); the first new frame then applies the current set (fold). Another state
    version, or a snapshot that no longer loads, is rebuilt from the open `alarms` rows +
    ENGINE_STATE_RESET (A-E1-5), never a crash loop; the reset commits at once with the
    rebuilt snapshot. A stored set that no longer validates is replaced by the current one,
    marked by the same ENGINE_STATE_RESET (the replay timeline sees the move). Returns the
    committed reset event, if any, for the caller to publish."""
    _, cset = _current(ctx)
    s = await load_seq_state(conn, vehicle)
    row = await load_engine_row(conn, vehicle)
    if row is None or s is None:  # no snapshot; "" → LIMITS_APPLIED on the first frame
        # = initial() without open rows; a stray open alarm is kept, not raised twice
        es = rebuild(await load_open_alarms(conn, vehicle), cset)
        return load_vehicle(s, None, "", es), ()
    version, set_sha, obj = row
    old, reasons = cset, list[str]()
    if set_sha != cset.set_sha256:
        try:
            old = await _stored(ctx, conn, set_sha)
        except MissionDbError as e:  # the snapshot is tried with cset (rebuilt if it can't)
            reasons.append(f"stored set {set_sha} no longer loads ({e}); now {cset.set_sha256}")
    try:
        if version != state_version():
            raise ValueError(f"state version {version} != {state_version()}")
        vs = load_vehicle(s, obj, old.set_sha256, load(obj["engine"], old))
    except (ValueError, KeyError, TypeError) as e:
        reasons.append(f"rebuilt from open alarms: {e!r}")
        vs = load_vehicle(
            s, None, old.set_sha256, rebuild(await load_open_alarms(conn, vehicle), old)
        )
    if not reasons:
        return vs, ()
    log.warning("engine state of %s: %s", vehicle, "; ".join(reasons))
    details = {"found_version": version, "version": state_version(), "epoch": s.epoch,
               "reason": "; ".join(reasons)}  # fmt: skip
    key = reset_key(vehicle, s.epoch, s.last_seq)
    ev = GroundEvent(key, vehicle, "ENGINE_STATE_RESET", s.last_t, details)
    async with conn.transaction():
        await write_outcome(conn, vehicle, vs, Outcome((), (ev,), 0))
    return vs, (ev,)


async def _silence(
    conn: psycopg.AsyncConnection,
    vehicle: str,
    vs: VehicleState,
    rejected: Mapping[str, int] | None = None,
) -> tuple[VehicleState, list[Link]]:
    """LOS after STALE_S without a new frame: LOS and engine_state in one small transaction.
    Returns the committed state (LOS open) and its `link` for the caller to publish; `vs`
    unchanged and no link when no LOS was due."""
    ls, ev = on_silence(vs.link, vehicle, vs.seq, rejected)
    if not ev:
        return vs, []
    nvs = replace(vs, link=ls)
    async with conn.transaction():
        await write_outcome(conn, vehicle, nvs, Outcome((), ev, 0))
    return nvs, _links(ev, nvs)


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
    time (XAUTOCLAIM … 0, Review Focus 2), then read new entries behind the pre-read fence.
    Every pass sets the heartbeat; every reclaim_s the loop reclaims entries idle for
    RECLAIM_IDLE_MS under any consumer, and a non-home holder checks for hand-back (D-ING-26)."""
    stream = f"tm:{vehicle}"
    home = ctx.homes.get(vehicle)
    vs, reset = await load_state(ctx, conn, vehicle)
    # committed but not yet published; kept across a Redis error (P-pf9). Starts with D-UI-18
    unsent: list[UiMessage] = [Resync(vehicle=vehicle), *_links(reset, vs)]
    ready, backoff, was_down = False, BACKOFF_START_S, False
    claim_idle: int | None = 0  # XAUTOCLAIM min-idle while draining; None = read new entries
    cursor = "0-0"
    last_new = next_handback = time.monotonic()
    next_reclaim = last_new + ctx.timing.reclaim_s
    while not stop.is_set():
        await ctx.hooks.hang("in_loop")
        now = ctx.loop_seen[vehicle] = time.monotonic()
        caught_up = False  # P-pf2: only a `>` read that returned fewer than COUNT entries
        if claim_idle is None and now >= next_reclaim:
            claim_idle, cursor, next_reclaim = (
                ctx.reclaim_idle_ms,
                "0-0",
                now + ctx.timing.reclaim_s,
            )
        try:
            if unsent:  # the first pass after load_state, or a pass after a Redis error
                await publish_ui(ctx.redis, unsent)
                await _stamp_shown(ctx, conn, vehicle, _shown_now(unsent, vs), vs)
                unsent = []
            if not ready:
                await _ensure_group(ctx.redis, stream)
                ready = True
            await ctx.redis.set(f"hb:veh:{vehicle}", 1, ex=ctx.timing.hb_ttl_s)  # D-ING-25
            await fence(conn)
            if claim_idle is not None:
                res: Any = await ctx.redis.xautoclaim(
                    stream, GROUP, ctx.consumer, claim_idle, cursor, 1
                )
                cursor, claimed = res[0].decode(), res[1]
                if cursor == "0-0":
                    claim_idle = None
            else:
                ctx.hooks.fire("after_fence_before_read")  # R14 E-5's window: before the `>` read
                res = await ctx.redis.xreadgroup(
                    GROUP, ctx.consumer, {stream: ">"}, count=COUNT, block=BLOCK_MS
                )
                claimed = res[0][1] if res else []
                caught_up = len(claimed) < COUNT
            entries: list[Entry] = [(eid.decode(), f or {}) for eid, f in claimed]
            # after the read: a request waits at most one BLOCK_MS (D-ALM-14: <= 1 s); only
            # after the flush above succeeded (a held message blocks this pass, P-pf42b)
            acks = await pending_acks(conn, vehicle, ctx.shown.get(vehicle))
            nvs, _ = await process_batch(ctx, conn, stream, entries, vs, acks)
            if nvs.seq != vs.seq:  # a new frame advanced the tracker
                last_new = time.monotonic()
            vs = nvs
            if time.monotonic() - last_new > ctx.timing.los_s:
                vs, links = await _silence(conn, vehicle, vs, ctx.rejected.get(vehicle))
                unsent = [*links]
                await publish_ui(ctx.redis, unsent)
                unsent = []
            backoff = BACKOFF_START_S
            if was_down:  # R2-23: start the steal hold-off
                ctx.reconnected_at[0], was_down = time.monotonic(), False
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            log.warning("redis unavailable for %s (%s); retry in %.1fs", stream, e, backoff)
            if isinstance(e, Unpublished):  # committed: keep that state, publish next pass
                vs, unsent = e.vs, [*unsent, *e.msgs]
            # D-ING-08: re-read every pending entry after reconnect
            claim_idle, ready, cursor, was_down, caught_up = 0, False, "0-0", True, False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
            last_new = time.monotonic()  # a broker outage is not loss of signal (D-UI-04/15)
        try:  # after the frame COMMIT and _silence, outside the Redis except
            unsent = [*unsent, *await track_pass(ctx, conn, vehicle, caught_up)]
        except Exception:  # noqa: BLE001  D-CMD-05/D-ING-24: the tracker never stops ingest
            log.exception("command tracker failed for %s", vehicle, extra={"vehicle": vehicle})
            ctx.counters["cmd_tracker_errors"] += 1
        if home not in (None, ctx.consumer) and time.monotonic() >= next_handback:
            next_handback = time.monotonic() + ctx.timing.handback_s
            if await waiting(conn, vehicle, home):  # between batches: the last one is acked
                log.info("handing %s back to %s", vehicle, home)
                await release(conn, vehicle)
                return
