"""ACK requests applied by the owner worker, the only writer of alarm state (D-ALM-14).

The API inserts `alarm_ack_request` rows (status pending); the owner reads them every loop pass
and applies them at the start of its next frame transaction, before that batch's frames, each
in a SAVEPOINT, so one bad request can't block frames (R2-01)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import psycopg

from gs.alarms import engine
from gs.alarms.contract import Transition
from gs.alarms.engine import EngineState
from gs.common.audit import audit
from gs.ingest.seq import SeqState

log = logging.getLogger(__name__)
Result = Literal["applied", "superseded"]
# P-pf40: 22 data, 23 integrity, P0 PL/pgSQL RAISE (a trigger) reject one request; a request
# that can never apply must not exit ingest into a restart loop. Every other class propagates.
REJECT_CLASSES = ("22", "23", "P0")
NOT_OPEN = "alarm no longer open"  # ruling R-w
CHANGED = "alarm changed since the request"  # ruling P-pf42
ALREADY_ACKED = "already acked"


@dataclass(frozen=True)
class AckReq:
    request_id: UUID
    alarm_id: UUID
    param: str
    by_user: str
    note: str
    changed: bool = False  # a RAISE/ESCALATE row was committed after the request (P-pf42)


async def pending_acks(conn: psycopg.AsyncConnection, vehicle: str) -> list[AckReq]:
    """Pending requests of `vehicle`, oldest first, each with whether its alarm was raised again
    or escalated after the operator asked (the ACK would cover what they did not see)."""
    cur = await conn.execute(
        "SELECT q.request_id, q.alarm_id, a.param, q.by_user, q.note,"
        " EXISTS (SELECT 1 FROM alarm_events e WHERE e.alarm_id = q.alarm_id"
        "         AND e.kind IN ('RAISE', 'ESCALATE') AND e.processed_at > q.requested_at)"
        " FROM alarm_ack_request q JOIN alarms a USING (alarm_id)"
        " WHERE q.vehicle = %s AND q.status = 'pending'"
        " ORDER BY q.requested_at, q.request_id",
        (vehicle,),
    )
    return [AckReq(*row) for row in await cur.fetchall()]


def apply_ack(
    state: EngineState, req: AckReq, vehicle: str, t: datetime
) -> tuple[EngineState, Transition | None, Result, str | None]:
    """Pure. `t` = the apply time (closed_at when an RTN_UNACK alarm clears)."""
    a = state.alarms.get(req.param)
    if a is None or a.alarm_id != req.alarm_id:
        return state, None, "superseded", NOT_OPEN
    if req.changed:
        return state, None, "superseded", CHANGED
    if a.state == "ACKED":
        return state, None, "superseded", ALREADY_ACKED
    new, tr = engine.ack(state, vehicle, req.param, req.by_user, req.note, str(req.request_id), t)
    if tr is None:  # ponytail: unreachable (open states are UNACK, ACKED, RTN_UNACK)
        return state, None, "superseded", NOT_OPEN
    return new, tr, "applied", None


# the ACK row takes its set and limits hashes from the alarm it acknowledges
_ACK_ROW = """
INSERT INTO alarm_events (key, alarm_id, vehicle, param, kind, severity, after_epoch, after_seq,
                          after_t_onboard, by_user, note, limits_sha256, set_sha256)
SELECT %(key)s, alarm_id, vehicle, param, 'ACK', %(severity)s, %(after_epoch)s, %(after_seq)s,
       %(after_t)s, %(by_user)s, %(note)s, limits_sha256, set_sha256
FROM alarms WHERE alarm_id = %(alarm_id)s
ON CONFLICT (key) DO NOTHING
"""
_ACK_ALARM = """
UPDATE alarms SET state = %(state)s, acked_sev = %(acked_sev)s, closed_at = %(closed_at)s
WHERE alarm_id = %(alarm_id)s
"""


async def _settle(
    conn: psycopg.AsyncConnection, vehicle: str, req: AckReq, result: str, reason: str | None
) -> None:
    await conn.execute(
        "UPDATE alarm_ack_request SET status = %s, status_reason = %s,"
        " applied_at = clock_timestamp() WHERE request_id = %s AND status = 'pending'",
        (result, reason, req.request_id),
    )
    details = {"vehicle": vehicle, "alarm_id": req.alarm_id, "request_id": req.request_id,
               "result": result, "reason": reason}  # fmt: skip
    await audit(conn, "system", "ALARM_ACK_APPLIED", req.note, details)


async def write_ack(
    conn: psycopg.AsyncConnection,
    vehicle: str,
    req: AckReq,
    result: str,
    reason: str | None,
    tr: Transition | None,
    after: SeqState | None,
) -> None:
    """Inside the caller's transaction, in its own SAVEPOINT: the ACK row (positioned after the
    vehicle's last frame before this batch, `after`), the alarm row, the request status and the
    ALARM_ACK_APPLIED audit. A database error rolls the savepoint back and propagates."""
    async with conn.transaction():  # SAVEPOINT
        if tr is not None:
            pos = (None,) * 3 if after is None else (after.epoch, after.last_seq, after.last_t)
            p = {
                "key": tr.key, "alarm_id": tr.alarm_id, "severity": tr.severity.name,
                "after_epoch": pos[0], "after_seq": pos[1], "after_t": pos[2],
                "by_user": tr.by_user, "note": tr.note,
                "state": tr.alarm_state, "closed_at": tr.closed_at,
                "acked_sev": None if tr.acked_sev is None else tr.acked_sev.name,
            }  # fmt: skip
            await conn.execute(_ACK_ROW, p)
            await conn.execute(_ACK_ALARM, p)
        await _settle(conn, vehicle, req, result, reason)


async def apply_acks(
    conn: psycopg.AsyncConnection,
    vehicle: str,
    state: EngineState,
    reqs: Sequence[AckReq],
    after: SeqState | None,
) -> EngineState:
    """Apply `reqs` in order inside the caller's frame transaction. A request whose write fails
    with a REJECT_CLASSES error is `rejected` (text of the error, audited, own savepoint) and the
    engine keeps its pre-ACK state; if even that fails it stays pending (logged) so the frames
    still commit. Any other error propagates (operational, D-ING-24)."""
    for req in reqs:
        new, tr, result, reason = apply_ack(state, req, vehicle, datetime.now(UTC))
        try:
            await write_ack(conn, vehicle, req, result, reason, tr, after)
        except psycopg.Error as e:
            _rejectable(e)
            await _reject(conn, vehicle, req, f"{e.sqlstate}: {e}")
            continue
        state = new
    return state


def _rejectable(e: psycopg.Error) -> None:
    if (e.sqlstate or "")[:2] not in REJECT_CLASSES:
        raise e


async def _reject(conn: psycopg.AsyncConnection, vehicle: str, req: AckReq, why: str) -> None:
    try:
        async with conn.transaction():  # SAVEPOINT
            await _settle(conn, vehicle, req, "rejected", why)
    except psycopg.Error as e:
        _rejectable(e)
        log.error("ACK request %s could not be rejected (%s: %s); left pending",
                  req.request_id, e.sqlstate, e, extra={"vehicle": vehicle})  # fmt: skip
