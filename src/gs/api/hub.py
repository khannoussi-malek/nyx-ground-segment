"""The API's one `ui:events` reader (R9; D-UI-01/02/04/15/18, D-ING-27).

One XREAD loop from the stream tail fans every entry out to bounded per-client queues. A
client too slow to keep its queue below CLIENT_QUEUE is closed: its stream ends, the console
watchdog reconnects and the reconnect's snapshot restores it. An entry that fails the ui-event
model goes to tm:dlq and is skipped (D-ING-27). A Redis error marks the broker down
(`ground{down}`) until the next XREAD works (`ground{up}` + a snapshot to everyone)."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import psycopg
import redis.asyncio
import redis.exceptions
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import ValidationError

from gs.common.interfaces import Ground, Snapshot, Tm, UiEvent
from gs.common.interfaces.base import StrictModel
from gs.common.interfaces.dlq import schema_reject
from gs.common.interfaces.render import schema_sha12
from gs.common.interfaces.sse import Alarm, AlarmView, Cmd, EventView, Link
from gs.common.interfaces.ui_event import Resync, UiMessage
from gs.common.uievents import UI_EVENTS
from gs.common.views import alarm_views, command_views, event_views
from gs.ingest.events import GroundEvent, insert_events, mismatch_key, to_link

CLIENT_QUEUE = 1000  # SSE frames buffered per client before it is closed
RETRY_S = 1.0  # between XREAD attempts while the broker is down
HB_S = 1.0  # hb:veh:<v> poll period (the SSE hb carries the result)
SNAPSHOT_COMMANDS = 50  # per vehicle (ruling R-r)
SNAPSHOT_EVENTS = 50  # newest overall, plus each vehicle's newest LOS/AOS and FROZEN
CARRIED_KINDS = (("LOS", "AOS"), ("FROZEN",))  # build_snapshot: one newest event per vehicle each
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
BLOCK_MS = 1000
STALE_S = 3.0  # no reader pass for this long: it hangs; consoles get ground{down}
DB_WAIT_S = 2.0  # pool wait in the reader, so a database stall can't hold up the deltas
DLQ = "tm:dlq"
CONSUMER = "api"
log = logging.getLogger(__name__)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def format_sse(event: str, model: StrictModel) -> bytes:
    return f"event: {event}\ndata: {model.model_dump_json()}\n\n".encode()


async def _last_values(
    conn: psycopg.AsyncConnection, vehicles: Sequence[str], params: Sequence[str], now: datetime
) -> dict[str, Tm]:
    cols = ["vehicle", "t_onboard", "seq", "mode", "last_cmd_id", *params]
    query = sql.SQL(
        "SELECT DISTINCT ON (vehicle) {} FROM telemetry WHERE vehicle = ANY(%s)"
        " ORDER BY vehicle, t_onboard DESC"
    ).format(sql.SQL(", ").join(map(sql.Identifier, cols)))
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, (list(vehicles),))
        rows = await cur.fetchall()
        await cur.execute(  # FROZEN is shown from the owner's state (ruling R-e)
            "SELECT vehicle, coalesce((state #>> '{frozen,reported}')::boolean, false) AS frozen"
            " FROM engine_state WHERE vehicle = ANY(%s)",
            (list(vehicles),),
        )
        frozen = {r["vehicle"]: r["frozen"] for r in await cur.fetchall()}
    out: dict[str, Tm] = {}
    for r in rows:
        t = r["t_onboard"].astimezone(UTC)
        out[r["vehicle"]] = Tm(
            vehicle=r["vehicle"], seq=r["seq"], t_onboard=t, mode=r["mode"],
            last_cmd_id=r["last_cmd_id"], values={p: r[p] for p in params},
            age_ms=ms(now) - ms(t), frozen=frozen.get(r["vehicle"], False),
        )  # fmt: skip
    return out


async def build_snapshot(
    conn: psycopg.AsyncConnection, vehicles: Sequence[str], params: Sequence[str], now: datetime
) -> Snapshot:
    """Last values (with FROZEN), the open alarms of each vehicle, and the newest
    SNAPSHOT_COMMANDS commands and SNAPSHOT_EVENTS events overall (+ each vehicle's newest
    LOS/AOS and FROZEN), newest first (ruling R-r;
    the console keeps the order). The one snapshot function for connect, reconnect, resync,
    broker up and overflow (D-UI-02)."""
    alarms: list[AlarmView] = []
    for v in vehicles:
        alarms += await alarm_views(conn, vehicle=v)
    events = await event_views(conn, limit=SNAPSHOT_EVENTS)
    # + each vehicle's newest LOS/AOS and FROZEN when they fell out of the newest (an open LOS
    # carries its last frame's time): the console's LOS banner and its LOS / FROZEN tones read
    # them. Older than every event above, so appending keeps newest first.
    keys, links = {e.key for e in events}, list[EventView]()
    for v in vehicles:
        for kinds in CARRIED_KINDS:
            links += await event_views(conn, vehicle=v, kinds=kinds, limit=1)
    old = [e for e in links if e.key not in keys]
    events += sorted(old, key=lambda e: e.t_onboard or EPOCH, reverse=True)
    return Snapshot(
        vehicles=await _last_values(conn, vehicles, params, now),
        alarms=alarms,
        commands=await command_views(conn, limit=SNAPSHOT_COMMANDS),
        events=events,
    )


def _vehicle(msg: UiMessage) -> str | None:
    match msg:
        case Tm() | Resync():
            return msg.vehicle
        case Alarm():
            return msg.alarm.vehicle
        case Cmd():
            return msg.command.vehicle
        case Link():
            return msg.event.vehicle


@dataclass(eq=False)
class Client:
    queue: asyncio.Queue[bytes]
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class Hub:
    def __init__(
        self,
        r: redis.asyncio.Redis,
        pool: AsyncConnectionPool,
        vehicles: Sequence[str],
        params: Callable[[], Sequence[str]],
        queue_size: int = CLIENT_QUEUE,
    ) -> None:
        self.redis, self.pool, self.vehicles, self.params = r, pool, tuple(vehicles), params
        self.queue_size = queue_size
        self.clients: set[Client] = set()
        self.broker_up = True
        self.processing: dict[str, bool] = {v: False for v in self.vehicles}
        self.degraded = False  # an entry failed the ui-event model; until restart (D-ING-27)
        self.alive_at = time.monotonic()  # the reader's last pass
        self._resnap = False  # a rejected entry may have carried an alarm: snapshot next tick

    def up(self) -> bool:
        """Deltas flow: the broker answers and the reader is not hung."""
        return self.broker_up and time.monotonic() - self.alive_at < STALE_S

    def subscribe(self) -> Client:
        c = Client(asyncio.Queue(self.queue_size))
        self.clients.add(c)
        return c

    def unsubscribe(self, c: Client) -> None:
        self.clients.discard(c)

    def broadcast(self, frame: bytes) -> None:
        for c in list(self.clients):
            try:
                c.queue.put_nowait(frame)
            except asyncio.QueueFull:
                log.warning("SSE client %d frames behind; closing it", self.queue_size)
                c.closed.set()
                self.clients.discard(c)

    async def snapshot(self) -> Snapshot:
        async with self.pool.connection(timeout=DB_WAIT_S) as conn:
            return await build_snapshot(conn, self.vehicles, self.params(), datetime.now(UTC))

    async def _snapshot_frame(self) -> bytes | None:
        """None when the database is unreachable (the clients keep their deltas); any other
        error propagates to run()'s catch-all."""
        try:
            return format_sse("snapshot", await self.snapshot())
        except (psycopg.Error, PoolTimeout) as e:
            log.warning("snapshot for broadcast failed: %s: %s", type(e).__name__, e)
            return None

    async def _broadcast_snapshot(self) -> None:
        if (frame := await self._snapshot_frame()) is not None:
            self.broadcast(frame)

    def _down(self, e: Exception) -> None:
        if not self.broker_up:
            return
        self.broker_up = False
        if isinstance(e, redis.exceptions.RedisError):
            log.warning("ui:events unreachable (%s: %s)", type(e).__name__, e)
        else:  # a bug in the reader: the consoles must see ground down, not silence (R2-12)
            log.exception("ui:events reader failed; retrying")
        self.broadcast(format_sse("ground", Ground(broker="down")))

    async def run(self, stop: asyncio.Event) -> None:
        last_id: str | None = None  # None: take the tail first
        next_poll = 0.0
        while not stop.is_set():
            self.alive_at = time.monotonic()
            try:
                if last_id is None:
                    tail: Any = await self.redis.xrevrange(UI_EVENTS, count=1)
                    last_id = str(tail[0][0]) if tail else "0-0"
                if time.monotonic() >= next_poll:  # also before the first XREAD
                    next_poll = time.monotonic() + HB_S
                    await self._poll_processing()
                    # at most one per HB_S, however many were rejected; kept until one is built
                    if self._resnap and (frame := await self._snapshot_frame()) is not None:
                        self._resnap = False
                        self.broadcast(frame)
                read = await self._xread(last_id, stop)
                if read is None:
                    return
                if not self.broker_up:
                    await self._recovered()
                for _, entries in read:
                    for entry_id, fields in entries:
                        last_id = entry_id
                        await self._handle(entry_id, fields.get("data", ""))
            except Exception as e:  # noqa: BLE001  never a silent reader (Task 11 review)
                self._down(e)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), RETRY_S)

    async def _recovered(self) -> None:
        """ground{up} with fresh processing, then a snapshot (deltas may have been missed); the
        snapshot is built first, so one that keeps failing leaves the broker down, not flapping."""
        frame = await self._snapshot_frame()
        await self._poll_processing()
        log.info("ui:events reachable again")
        self.broker_up = True
        self.broadcast(format_sse("ground", Ground(broker="up")))
        if frame is not None:
            self.broadcast(frame)

    async def _poll_processing(self) -> None:
        """hb:veh:<v> set by the owner's read loop (D-ING-25): a healthy owner exists."""
        vals = await self.redis.mget([f"hb:veh:{v}" for v in self.vehicles])
        self.processing = {v: x is not None for v, x in zip(self.vehicles, vals, strict=True)}

    async def _xread(self, last_id: str, stop: asyncio.Event) -> list[Any] | None:
        """One XREAD BLOCK; None when `stop` was set first (shutdown does not sit out the block)."""
        read = asyncio.ensure_future(self.redis.xread({UI_EVENTS: last_id}, block=BLOCK_MS))
        halt = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({read, halt}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            halt.cancel()
        if not read.done():
            read.cancel()
            return None
        return cast(list[Any], read.result() or [])  # [(stream, [(id, {"data": …})])]

    async def _handle(self, entry_id: str, data: str) -> None:
        try:
            msg = UiEvent.model_validate_json(data).root
        except ValidationError as e:
            await self._reject(entry_id, data, str(e))
            return
        if (v := _vehicle(msg)) is not None and v not in self.vehicles:
            return  # recorded-only vehicles (NYX-TA9) never reach the live console
        if isinstance(msg, Resync):  # a worker (re)started: everyone reloads (D-UI-18)
            await self._broadcast_snapshot()
            return
        if isinstance(msg, Tm):
            msg = msg.model_copy(update={"age_ms": ms(datetime.now(UTC)) - ms(msg.t_onboard)})
        self.broadcast(format_sse(msg.kind, msg))

    async def _reject(self, entry_id: str, raw: str, detail: str) -> None:
        """D-ING-27 for ui:events: DLQ record, one INTERFACE_MISMATCH event per schema version,
        /healthz degraded; the stream carries on. XREAD has no ack: the entry is skipped."""
        log.warning("ui:events entry %s fails the ui-event model; to %s: %s", entry_id, DLQ, detail)
        self.degraded = True  # first: a DLQ failure below still leaves health and the event
        key = mismatch_key("ui-event", schema_sha12(UiEvent))
        details = {"stream": UI_EVENTS, "consumer": CONSUMER, "detail": detail}
        ev = GroundEvent(key, None, "INTERFACE_MISMATCH", None, details)
        inserted = 0
        try:
            async with self.pool.connection(timeout=DB_WAIT_S) as conn, conn.transaction():
                inserted = await insert_events(conn, [ev])
        except (psycopg.Error, PoolTimeout) as e:
            log.warning("INTERFACE_MISMATCH not recorded: %s: %s", type(e).__name__, e)
        if inserted:  # one link per committed ground event (P-pf9)
            self.broadcast(format_sse("link", to_link(ev)))
        self._resnap = True  # every rejected entry: restore what it may have dropped
        rec = schema_reject(raw, UI_EVENTS, entry_id, detail, datetime.now(UTC))
        await self.redis.xadd(DLQ, {"data": rec.model_dump_json()})
