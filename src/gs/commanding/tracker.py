"""Command tracker: tcack and echo evidence → command states, once per owner pass (D-CMD-05).

Order per pass (D-ING-05: commit, then ack): read tcack:<v> (pending first, then new, no BLOCK);
validate every entry before the transaction (D-ING-27); one transaction on the lock connection
decides every open row with decide(); then MULTI DLQ + XACK; then publish cmd/link messages.
Must not import gs.ingest.worker at runtime (it imports this module).
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import psycopg
import redis.exceptions
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import ValidationError

from gs.commanding.args import readable_cmd_id
from gs.commanding.decide import AckSeen, Change, CmdRow, decide
from gs.common.audit import audit
from gs.common.interfaces.dlq import DlqRecord, schema_reject
from gs.common.interfaces.render import schema_sha12
from gs.common.interfaces.sse import Cmd, EventView, Link
from gs.common.interfaces.streams import ACK_SCHEMA, Tc, TcAck
from gs.common.missiondb import RECEIVE_TIMEOUT_MAX_S, VERIFY_TIMEOUT_MAX_S
from gs.common.uievents import publish_ui
from gs.common.views import command_views
from gs.ingest.errors import OperationalDbError
from gs.ingest.events import GroundEvent, details_json, insert_events, late_key, mismatch_key
from gs.ingest.owner import fence

if TYPE_CHECKING:
    from gs.ingest.worker import Ctx

LATE_WINDOW_S = 600.0  # ruling R-i
TCACK_COUNT = 100
GROUP = "ingest"  # the tcack:<v> group (cross-track contract), same name as tm:<v>'s
DLQ = "tm:dlq"
ECHO_SLACK_S = 1.0  # echoes are searched from min(coalesce(sent_at, queued_at)) - 1 s
log = logging.getLogger(__name__)

_OPEN = """
SELECT cmd_id, name, state, failure_reason, queued_at, sent_at, received_at, failed_at,
       late_verified_at, late_ack_at
FROM commands
WHERE vehicle = %(v)s AND (state IN ('QUEUED', 'SENT', 'RECEIVED')
   OR (state = 'FAILED'
       AND failure_reason IN ('NOT_RECEIVED', 'NOT_VERIFIED', 'ACK_INVALID', 'SEND_ERROR')
       AND failed_at >= %(oldest)s AND (late_verified_at IS NULL OR late_ack_at IS NULL)))
"""
_ECHOES = """
SELECT last_cmd_id, min(t_onboard) FROM telemetry
WHERE vehicle = %s AND t_onboard >= %s AND last_cmd_id = ANY(%s) GROUP BY 1
"""
_SET_FIELDS = (
    "state",
    "failure_reason",
    "received_at",
    "verified_at",
    "failed_at",
    "late_verified_at",
    "late_ack_at",
    "late_ack_accepted",
)


@dataclass
class _Read:
    """One pass's tcack entries, validated before the transaction."""

    entries: list[tuple[str, UUID | None]] = field(default_factory=list)  # (entry id, cmd_id)
    acks: dict[UUID, AckSeen] = field(default_factory=dict)  # first ack per cmd_id (stream order)
    dlq: dict[str, DlqRecord] = field(default_factory=dict)  # entry id -> schema record
    events: list[GroundEvent] = field(default_factory=list)  # INTERFACE_MISMATCH
    caught_up: bool = True  # no tcack backlog left behind this read


def _mismatch(
    kind: Literal["tc", "tcack"], sha12: str, stream: str, consumer: str, detail: str
) -> GroundEvent:
    key = mismatch_key(kind, sha12)
    details = {"stream": stream, "consumer": consumer, "detail": detail}
    return GroundEvent(key, None, "INTERFACE_MISMATCH", None, details)


def _entry_time(entry_id: str) -> datetime:
    return datetime.fromtimestamp(int(entry_id.split("-")[0]) / 1000, UTC)


def _validate(ctx: Ctx, vehicle: str, stream: str, entries: Sequence[Any], into: _Read) -> None:
    now = datetime.now(UTC)
    for eid_raw, fields in entries:
        eid = eid_raw.decode() if isinstance(eid_raw, bytes) else str(eid_raw)
        raw = (fields or {}).get(b"data", b"").decode("utf-8", errors="replace")
        try:
            ack = TcAck.model_validate_json(raw)
        except ValidationError as e:
            detail = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            log.warning("tcack %s rejected (schema): %s", eid, detail, extra={"vehicle": vehicle})
            into.dlq[eid] = schema_reject(raw, stream, eid, detail, now)
            into.events.append(
                _mismatch("tcack", schema_sha12(TcAck), stream, ctx.consumer, detail)
            )
            cid = readable_cmd_id(raw)
            into.entries.append((eid, cid))
            if cid is not None:
                into.acks.setdefault(cid, AckSeen(False, "INVALID", _entry_time(eid), True))
            continue
        into.entries.append((eid, ack.cmd_id))
        if ack.code == ACK_SCHEMA:  # the simulator rejected our tc entry (A-R17-4)
            into.events.append(
                _mismatch("tc", schema_sha12(Tc), f"tc:{vehicle}", "sim", ack.reason or "")
            )
        # ponytail: first ack per cmd_id; a duplicate (at-least-once send) says nothing new
        into.acks.setdefault(ack.cmd_id, AckSeen(ack.accepted, ack.code, ack.t_onboard))


async def _read(ctx: Ctx, vehicle: str) -> _Read:
    """Pending entries (a previous owner's or a failed pass's) first, then new ones."""
    stream, out, cursor = f"tcack:{vehicle}", _Read(), "0-0"
    while True:  # the whole PEL every pass: held acks must never hide the ones behind them
        try:
            res: Any = await ctx.redis.xautoclaim(
                stream, GROUP, ctx.consumer, 0, cursor, count=TCACK_COUNT
            )
        except redis.exceptions.ResponseError as e:
            if "NOGROUP" not in str(e):
                raise
            await ctx.redis.xgroup_create(stream, GROUP, id="0", mkstream=True)
            break
        # ponytail: bounded by the PEL, which holds only acks not yet stored (a handful)
        _validate(ctx, vehicle, stream, res[1], out)
        cursor = res[0].decode() if isinstance(res[0], bytes) else str(res[0])
        if cursor == "0-0":
            break
    new: Any = await ctx.redis.xreadgroup(GROUP, ctx.consumer, {stream: ">"}, count=TCACK_COUNT)
    fresh = new[0][1] if new else []
    _validate(ctx, vehicle, stream, fresh, out)
    out.caught_up = len(fresh) < TCACK_COUNT  # a full read: more acks wait behind it
    return out


def _row(r: dict[str, Any], ctx: Ctx) -> CmdRow:
    cmd = ctx.mission().commands.get(r["name"])
    receive_s = cmd.receive_timeout_s if cmd else RECEIVE_TIMEOUT_MAX_S
    verify_s = cmd.verify_timeout_s if cmd else VERIFY_TIMEOUT_MAX_S
    return CmdRow(**r, receive_s=receive_s, verify_s=verify_s)


async def _echoes(
    conn: psycopg.AsyncConnection, vehicle: str, rows: Sequence[CmdRow]
) -> dict[UUID, datetime]:
    wanted = [r for r in rows if r.state != "FAILED" or r.late_verified_at is None]
    if not wanted:
        return {}
    # ponytail: scans up to LATE_WINDOW_S of this vehicle's frames (indexed); fine at 10 Hz
    since = min(r.sent_at or r.queued_at for r in wanted) - timedelta(seconds=ECHO_SLACK_S)
    cur = await conn.execute(_ECHOES, (vehicle, since, [r.cmd_id for r in wanted]))
    return {cid: t for cid, t in await cur.fetchall()}


async def apply_change(conn: psycopg.AsyncConnection, vehicle: str, r: CmdRow, c: Change) -> bool:
    """One guarded UPDATE with the change's single guard; audits only when it matched."""
    sets = {f: getattr(c, f) for f in _SET_FIELDS if getattr(c, f) is not None}
    where = [sql.SQL("cmd_id = %(cmd_id)s"), sql.SQL("state = ANY(%(guard)s)")]
    if c.late_verified_at is not None:
        where.append(sql.SQL("late_verified_at IS NULL"))
    if c.late_ack_at is not None:
        where.append(sql.SQL("late_ack_at IS NULL"))
    q = sql.SQL("UPDATE commands SET {} WHERE {}").format(
        sql.SQL(", ").join(
            sql.SQL("{} = %({})s").format(sql.Identifier(f), sql.SQL(f)) for f in sets
        ),
        sql.SQL(" AND ").join(where),
    )
    cur = await conn.execute(q, {**sets, "cmd_id": r.cmd_id, "guard": list(c.guard)})
    if cur.rowcount != 1:  # a concurrent change won (the API's SEND_ERROR, another pass)
        return False
    reason = c.failure_reason or r.failure_reason
    base = {"vehicle": vehicle, "cmd_id": r.cmd_id, "name": r.name}
    for state in c.states:  # P-pf37: every state passed through, in order
        why = reason if state == "FAILED" else None
        await audit(
            conn, "system", "CMD_STATE", why, {**base, "state": state, "failure_reason": why}
        )
    for action in c.audits[len(c.states) :]:  # CMD_LATE_ACK / CMD_LATE_VERIFICATION
        details = {**base, "state": c.state or r.state, "failure_reason": reason}
        if action == "CMD_LATE_ACK":
            details |= {"late_ack_at": c.late_ack_at, "accepted": c.late_ack_accepted}
        else:
            details |= {"late_verified_at": c.late_verified_at}
        await audit(conn, "system", action, reason, details)
    return True


async def _decide_all(
    ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str, read: _Read, caught_up: bool
) -> tuple[list[UUID], set[UUID], list[GroundEvent]]:
    """The tracker transaction: returns (changed cmd_ids, cmd_ids whose ack must stay pending,
    events actually inserted). An ack is held when it was not stored: a late ack on a row
    that may not time out yet (not caught up), or a guard a concurrent change won."""
    now = datetime.now(UTC)
    changed: list[UUID] = []
    held: set[UUID] = set()
    events = list(read.events)
    async with conn.transaction():
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(_OPEN, {"v": vehicle, "oldest": now - timedelta(seconds=LATE_WINDOW_S)})
        rows = [_row(r, ctx) for r in await cur.fetchall()]
        echoes = await _echoes(conn, vehicle, rows)
        for r in rows:
            ack = read.acks.get(r.cmd_id)
            c = decide(r, ack, echoes.get(r.cmd_id), now, caught_up, LATE_WINDOW_S)
            if c is None:
                if ack is not None and r.state in ("QUEUED", "SENT"):
                    held.add(r.cmd_id)  # decided once the owner has caught up
                continue
            if not await apply_change(conn, vehicle, r, c):
                if ack is not None:
                    held.add(r.cmd_id)  # judged again next pass against the new row
                continue
            changed.append(r.cmd_id)
            if c.late_verification:
                details = {
                    "cmd_id": r.cmd_id,
                    "name": r.name,
                    "failure_reason": c.failure_reason or r.failure_reason,
                }
                events.append(
                    GroundEvent(
                        late_key(r.cmd_id),
                        vehicle,
                        "LATE_VERIFICATION",
                        c.late_verified_at,
                        details,
                    )
                )
        known = {r.cmd_id for r in rows}
        for cid in read.acks.keys() - known:
            log.info(
                "tcack for %s matches no open command; acknowledged",
                cid,
                extra={"vehicle": vehicle},
            )
        inserted = [e for e in events if await insert_events(conn, [e])]
    return changed, held, inserted


async def track_pass(
    ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str, caught_up: bool
) -> None:
    """`caught_up`: this pass did the `>` read and got fewer than COUNT entries (P-pf2); only
    then, and with no tcack backlog left, may timeouts be judged. Never raises: a tracker error
    rolls back, is logged and counted, and its entries are read again next pass (D-ING-24)."""
    stream = f"tcack:{vehicle}"
    try:  # D-ING-12: a stolen owner must not pull tcack entries into its PEL either
        await fence(conn)
    except OperationalDbError as e:
        log.warning("tracker skipped for %s: %s", vehicle, e, extra={"vehicle": vehicle})
        return
    try:
        read = await _read(ctx, vehicle)
    except redis.exceptions.RedisError as e:
        log.warning("tcack read failed for %s: %s", vehicle, e, extra={"vehicle": vehicle})
        return
    try:
        changed, held, inserted = await _decide_all(
            ctx, conn, vehicle, read, caught_up and read.caught_up
        )
    except Exception:  # noqa: BLE001  D-CMD-05: any SQLSTATE is rolled back, never re-raised
        log.exception("command tracker pass failed for %s", vehicle, extra={"vehicle": vehicle})
        ctx.counters["cmd_tracker_errors"] += 1
        return
    done = [eid for eid, cid in read.entries if cid not in held]
    rejected = [read.dlq[eid] for eid in done if eid in read.dlq]
    try:  # publish, then DLQ + ack in one MULTI: the frame path's order (D-ING-05)
        views = await command_views(conn, cmd_ids=changed, limit=None) if changed else []
        links = [
            Link(
                event=EventView(
                    key=e.key,
                    vehicle=e.vehicle,
                    kind=e.kind,
                    t_onboard=e.t_onboard,
                    details=json.loads(details_json(e.details)),  # UUIDs and times as JSON
                )
            )
            for e in inserted
        ]
        await publish_ui(ctx.redis, [*(Cmd(command=v) for v in views), *links])
        if done:
            pipe = ctx.redis.pipeline(transaction=True)
            for rec in rejected:
                pipe.xadd(DLQ, {"data": rec.model_dump_json()})
            pipe.xack(stream, GROUP, *done)
            await pipe.execute()
            ctx.counters["interface_mismatch"] += len(rejected)  # /healthz degraded (D-ING-27)
    except (redis.exceptions.RedisError, psycopg.Error) as e:  # re-read, re-judged idempotently
        log.warning("tcack publish/ack failed for %s: %s", vehicle, e, extra={"vehicle": vehicle})
