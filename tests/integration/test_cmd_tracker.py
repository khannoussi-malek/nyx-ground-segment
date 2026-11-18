"""Command tracker in the vehicle owner (D-CMD-05) and the tcack rejection rule (D-ING-27).
Rows and stream entries are written by the test; track_pass runs directly or in worker([v])."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import redis.exceptions

import gs.commanding.tracker as tracker_mod
from gs.commanding.decide import Change, CmdRow
from gs.commanding.tracker import LATE_WINDOW_S, TCACK_COUNT, apply_change, track_pass
from gs.common.interfaces.render import schema_sha12
from gs.common.interfaces.streams import Tc, TcAck
from gs.common.uievents import publish_ui
from gs.common.views import command_views
from gs.ingest.events import late_key, mismatch_key
from gs.ingest.liveness import ingest_status
from gs.ingest.worker import Ctx
from tests.support.env import until, vid
from tests.support.ingest import make_ctx, worker
from tests.support.pg import admin_dsn, app_dsn, audit_rows, commands_for, events_for, rows
from tests.support.redisx import (
    client,
    dlq_since,
    produce_values,
    stream_entries,
    stream_values,
    validate_ui_event,
    xadd_data,
)


def now() -> datetime:
    return datetime.now(UTC)


def insert_cmd(v: str, state: str = "SENT", age_s: float = 0.0, **cols: Any) -> UUID:
    """A commands row for `v`; sent_at = now - age_s, queued_at 0.1 s before (overridable)."""
    cmd_id, sent = uuid4(), now() - timedelta(seconds=age_s)
    row: dict[str, Any] = {
        "cmd_id": cmd_id,
        "vehicle": v,
        "name": "isolate_valve",
        "args": json.dumps({"valve": "A"}),
        "args_hash": "h",
        "hazardous": False,
        "issued_by": "fc1",
        "state": state,
        "queued_at": sent - timedelta(seconds=0.1),
        "sent_at": sent,
    } | cols
    names = ", ".join(row)
    marks = ", ".join(f"%({k})s" for k in row)
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        c.execute(f"INSERT INTO commands ({names}) VALUES ({marks})", row)
    return cmd_id


def tcack(v: str, cmd_id: UUID, t: datetime, accepted: bool = True, code: str = "OK") -> str:
    ack = TcAck(cmd_id=cmd_id, accepted=accepted, code=code, reason=None, t_onboard=t)
    return xadd_data(f"tcack:{v}", ack.model_dump_json(), user="sim")


def row_of(v: str, cmd_id: UUID) -> dict[str, Any]:
    [r] = [r for r in commands_for(v) if r["cmd_id"] == cmd_id]
    return r


def pending(v: str) -> int:
    try:
        info: Any = client("admin").xpending(f"tcack:{v}", "ingest")
    except Exception:  # noqa: BLE001  no group yet
        return 0
    return int(info["pending"])


def cmd_audits(cmd_id: UUID) -> list[tuple[str, str | None]]:
    mine = [a for a in audit_rows() if (a["details"] or {}).get("cmd_id") == str(cmd_id)]
    return [(a["action"], a["details"].get("state")) for a in mine]


@asynccontextmanager
async def direct(consumer: str = "trk-test") -> AsyncIterator[tuple[Ctx, psycopg.AsyncConnection]]:
    ctx = make_ctx(consumer)
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
            yield ctx, conn
    finally:
        await ctx.redis.aclose()


async def outcome_of(cmd_id: UUID) -> str | None:
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as conn:
        [view] = await command_views(conn, cmd_ids=[cmd_id], limit=None)
    return view.outcome


# ---- direct track_pass
async def test_received_then_verified_in_order() -> None:
    v = vid()
    cmd_id = insert_cmd(v)
    tcack(v, cmd_id, now())
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
        assert row_of(v, cmd_id)["state"] == "RECEIVED" and pending(v) == 0
        produce_values(v, {"cabin_temp_c": [22.0]}, last_cmd_id=cmd_id, base=now())
        async with worker([v]):  # stores the echo frame (and runs its own tracker passes)
            await until(lambda: row_of(v, cmd_id)["state"] == "VERIFIED", 5)
    assert cmd_audits(cmd_id) == [("CMD_STATE", "RECEIVED"), ("CMD_STATE", "VERIFIED")]


async def test_duplicate_tcack_one_received_one_audit() -> None:
    v = vid()
    cmd_id = insert_cmd(v)
    t = now()
    tcack(v, cmd_id, t)
    tcack(v, cmd_id, t)  # the simulator re-sent after a timed-out XADD (G30)
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
        tcack(v, cmd_id, t)  # and once more, in a later pass
        await track_pass(ctx, conn, v, caught_up=True)
    r = row_of(v, cmd_id)
    assert r["state"] == "RECEIVED" and r["received_at"] == t
    assert cmd_audits(cmd_id) == [("CMD_STATE", "RECEIVED")]
    assert pending(v) == 0


async def test_late_ack_waits_for_caught_up() -> None:  # D-CMD-05: evidence is never dropped
    v = vid()
    cmd_id = insert_cmd(v, age_s=8.0)
    tcack(v, cmd_id, now() - timedelta(seconds=1))  # after the 5 s window
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=False)
        assert row_of(v, cmd_id)["state"] == "SENT" and pending(v) == 1  # kept pending
        await track_pass(ctx, conn, v, caught_up=True)
    r = row_of(v, cmd_id)
    assert (r["state"], r["failure_reason"], r["late_ack_accepted"]) == (
        "FAILED",
        "NOT_RECEIVED",
        True,
    )
    assert pending(v) == 0
    assert cmd_audits(cmd_id) == [("CMD_STATE", "FAILED"), ("CMD_LATE_ACK", "FAILED")]


async def test_tcack_backlog_is_not_caught_up() -> None:
    v = vid()
    late = insert_cmd(v, age_s=8.0)
    for _ in range(TCACK_COUNT):  # a full read of acks for commands this owner never saw
        tcack(v, uuid4(), now())
    tcack(v, late, now())
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)  # read 100: a backlog remains
        assert row_of(v, late)["state"] == "SENT"
        await track_pass(ctx, conn, v, caught_up=True)
    assert row_of(v, late)["late_ack_accepted"] is True  # FAILED, with the ack it did get
    assert pending(v) == 0  # unknown cmd_ids acknowledged too


async def test_late_ack_after_not_received() -> None:
    v = vid()
    failed = now() - timedelta(seconds=1)
    cmd_id = insert_cmd(v, state="FAILED", age_s=6, failure_reason="NOT_RECEIVED", failed_at=failed)
    t = now()
    tcack(v, cmd_id, t)
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
    r = row_of(v, cmd_id)
    assert (r["state"], r["late_ack_at"], r["late_ack_accepted"]) == ("FAILED", t, True)
    assert cmd_audits(cmd_id) == [("CMD_LATE_ACK", "FAILED")]
    assert await outcome_of(cmd_id) == "RECEIVED_LATE"


async def test_tcack_pel_over_count_still_catches_up() -> None:  # review: no stuck SENT
    v = vid()
    late = insert_cmd(v, age_s=8.0)
    held = [insert_cmd(v, age_s=8.0) for _ in range(TCACK_COUNT + 1)]
    for c in [*held, late]:
        tcack(v, c, now() - timedelta(seconds=1))  # all after their 5 s window
    async with direct() as (ctx, conn):
        for _ in range(2):  # owner behind: every late ack is held, the PEL grows past COUNT
            await track_pass(ctx, conn, v, caught_up=False)
        assert pending(v) == TCACK_COUNT + 2
        await track_pass(ctx, conn, v, caught_up=True)  # one pass claims the whole PEL
    for c in [*held, late]:
        r = row_of(v, c)
        assert (r["failure_reason"], r["late_ack_accepted"]) == ("NOT_RECEIVED", True)
    assert pending(v) == 0


async def test_send_error_row_records_vehicle_evidence() -> None:  # P-pf16
    v = vid()
    cmd_id = insert_cmd(v, state="FAILED", failure_reason="SEND_ERROR", failed_at=now())
    t = now()
    tcack(v, cmd_id, t)
    produce_values(v, {"cabin_temp_c": [22.0]}, last_cmd_id=cmd_id, base=t)
    async with worker([v]):
        await until(lambda: row_of(v, cmd_id)["late_verified_at"] is not None, 5)
    r = row_of(v, cmd_id)
    assert (r["state"], r["failure_reason"], r["late_ack_accepted"]) == (
        "FAILED",
        "SEND_ERROR",
        True,
    )
    assert await outcome_of(cmd_id) == "EXECUTED_LATE"  # never "not sent, safe to retry"


async def test_late_window_is_inclusive_at_600_s(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = now()

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return fixed

    monkeypatch.setattr(tracker_mod, "datetime", Clock)
    v = vid()
    edge = insert_cmd(
        v, state="FAILED", age_s=700, failure_reason="NOT_RECEIVED",
        failed_at=fixed - timedelta(seconds=LATE_WINDOW_S),
    )  # fmt: skip
    past = insert_cmd(
        v, state="FAILED", age_s=700, failure_reason="NOT_RECEIVED",
        failed_at=fixed - timedelta(seconds=LATE_WINDOW_S, microseconds=1),
    )  # fmt: skip
    tcack(v, edge, fixed)
    tcack(v, past, fixed)
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
    assert row_of(v, edge)["late_ack_at"] == fixed  # exactly 600 s: still loaded
    assert row_of(v, past)["late_ack_at"] is None


async def test_late_window_edges() -> None:
    v = vid()
    inside = insert_cmd(
        v, state="FAILED", age_s=605, failure_reason="NOT_RECEIVED",
        failed_at=now() - timedelta(seconds=LATE_WINDOW_S - 5),
    )  # fmt: skip
    outside = insert_cmd(
        v, state="FAILED", age_s=610, failure_reason="NOT_RECEIVED",
        failed_at=now() - timedelta(seconds=LATE_WINDOW_S + 1),
    )  # fmt: skip
    t = now()
    tcack(v, inside, t)
    tcack(v, outside, t)
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
    assert row_of(v, inside)["late_ack_at"] == t
    assert row_of(v, outside)["late_ack_at"] is None


async def test_sim_schema_nack_records_tc_mismatch() -> None:
    v = vid()
    cmd_id = insert_cmd(v)
    tcack(v, cmd_id, now(), accepted=False, code="SCHEMA")
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
    assert row_of(v, cmd_id)["failure_reason"] == "REJECTED"
    assert await outcome_of(cmd_id) == "NOT_EXECUTED"
    key = mismatch_key("tc", schema_sha12(Tc))
    assert [e["key"] for e in events_for(None, "INTERFACE_MISMATCH") if e["key"] == key] == [key]


async def test_cmd_messages_published() -> None:
    v = vid()
    cmd_id = insert_cmd(v)
    tcack(v, cmd_id, now())
    async with direct() as (ctx, conn):
        await track_pass(ctx, conn, v, caught_up=True)
    msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
    mine = [m for m in msgs if m["kind"] == "cmd" and m["command"]["cmd_id"] == str(cmd_id)]
    assert [m["command"]["state"] for m in mine] == ["RECEIVED"]  # one per change
    for m in mine:
        validate_ui_event(m)


async def test_unpublished_cmd_change_flushed_next_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Final review F2: a committed change whose publish failed is held and published first on
    the next pass (the frame path's `unsent`); re-reading its tcack changes nothing, so without
    the hold the console never sees it."""
    failed: list[list[str]] = []

    async def once_down(r: Any, msgs: Any) -> None:
        if msgs and not failed:
            failed.append([type(m).__name__ for m in msgs])
            raise redis.exceptions.ConnectionError("broker down")
        await publish_ui(r, msgs)

    monkeypatch.setattr(tracker_mod, "publish_ui", once_down)
    v = vid()
    cmd_id = insert_cmd(v)
    tcack(v, cmd_id, now())

    def mine() -> list[dict[str, Any]]:
        msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
        return [m for m in msgs if m["kind"] == "cmd" and m["command"]["cmd_id"] == str(cmd_id)]

    async with worker([v]):
        await until(lambda: bool(mine()), 5)
    assert failed == [["Cmd"]] and row_of(v, cmd_id)["state"] == "RECEIVED"
    assert [m["command"]["state"] for m in mine()] == ["RECEIVED"]


async def test_late_verification_publishes_link() -> None:  # P-pf9, Task 27b
    v = vid()
    sent = now() - timedelta(seconds=8)
    cmd_id = insert_cmd(
        v, state="FAILED", sent_at=sent, queued_at=sent - timedelta(seconds=0.1),
        failure_reason="NOT_RECEIVED", failed_at=sent + timedelta(seconds=5),
    )  # fmt: skip
    produce_values(v, {"cabin_temp_c": [22.0] * 5}, last_cmd_id=cmd_id, base=now())  # the echo

    def links() -> list[dict[str, Any]]:
        msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
        return [m for m in msgs if m["kind"] == "link" and m["event"]["key"] == late_key(cmd_id)]

    async with worker([v]):
        await until(lambda: bool(links()), 5)
    [m] = links()
    validate_ui_event(m)
    assert (m["event"]["kind"], m["event"]["vehicle"]) == ("LATE_VERIFICATION", v)
    assert m["event"]["details"]["cmd_id"] == str(cmd_id)
    assert row_of(v, cmd_id)["late_verified_at"] is not None  # committed before the publish


async def test_guard_lost_writes_no_audits() -> None:  # a concurrent change won the row
    v = vid()
    cmd_id = insert_cmd(v, state="FAILED", failure_reason="SEND_ERROR", failed_at=now())
    stale = CmdRow(
        cmd_id, "isolate_valve", "SENT", None, now(), now(), None, None, None, None, 5, 15
    )
    change = Change(
        state="VERIFIED",
        received_at=now(),
        verified_at=now(),
        guard=("QUEUED", "SENT", "RECEIVED"),
        states=("RECEIVED", "VERIFIED"),
        audits=("CMD_STATE", "CMD_STATE"),
    )
    async with direct() as (_, conn):
        assert await apply_change(conn, v, stale, change) is False
    assert row_of(v, cmd_id)["state"] == "FAILED" and cmd_audits(cmd_id) == []


async def test_late_fields_written_once() -> None:  # late_* IS NULL guards
    v = vid()
    first = now() - timedelta(seconds=2)
    cmd_id = insert_cmd(
        v, state="FAILED", failure_reason="NOT_RECEIVED", failed_at=now(), late_ack_at=first,
        late_ack_accepted=True,
    )  # fmt: skip
    stale = CmdRow(
        cmd_id,
        "isolate_valve",
        "FAILED",
        "NOT_RECEIVED",
        now(),
        now(),
        None,
        now(),
        None,
        None,
        5,
        15,
    )
    late = Change(
        late_ack_at=now(), late_ack_accepted=False, guard=("FAILED",), audits=("CMD_LATE_ACK",)
    )
    async with direct() as (_, conn):
        assert await apply_change(conn, v, stale, late) is False
    r = row_of(v, cmd_id)
    assert (r["late_ack_at"], r["late_ack_accepted"]) == (first, True)


# ---- in the worker
async def test_invalid_tcack_dlq_not_retried() -> None:
    v, t0 = vid(), now()
    cmd_id = insert_cmd(v)
    xadd_data(f"tcack:{v}", json.dumps({"cmd_id": str(cmd_id), "accepted": "yes"}), user="sim")
    async with worker([v]) as run:
        await until(lambda: row_of(v, cmd_id)["state"] == "FAILED", 5)
        await asyncio.sleep(3.5)  # three more passes
        counters = run.ctx.counters
    assert row_of(v, cmd_id)["failure_reason"] == "ACK_INVALID"  # the vehicle did answer
    [rec] = dlq_since(f"tcack:{v}", t0)
    assert json.loads(rec)["reason"] == "schema" and json.loads(rec)["stream"] == f"tcack:{v}"
    assert pending(v) == 0
    key = mismatch_key("tcack", schema_sha12(TcAck))
    assert len(rows("select key from events where key=%s", key)) == 1
    assert counters["interface_mismatch"] == 1
    assert ingest_status({}, set(), 0.0, 1, counters)[0] == "degraded"
    assert await outcome_of(cmd_id) == "UNKNOWN"


def drop_trigger(fn: str) -> None:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        c.execute("SET lock_timeout = '5s'")  # a lock wait fails, never hangs the run
        c.execute(f"DROP TRIGGER {fn} ON commands")


@pytest.mark.parametrize("slow_txn", [False, True])
async def test_tracker_error_never_touches_frames(
    monkeypatch: pytest.MonkeyPatch, slow_txn: bool
) -> None:
    if slow_txn:  # the tracker is mid-transaction most of the time: the DROP below lands in it
        echoes = tracker_mod._echoes

        async def slow(*a: Any) -> dict[UUID, datetime]:
            out = await echoes(*a)
            await asyncio.sleep(0.3)
            return out

        monkeypatch.setattr(tracker_mod, "_echoes", slow)
    v = vid()
    cmd_id = insert_cmd(v)
    tcack(v, cmd_id, now())
    fn = f"fail_{v.replace('-', '_').lower()}"
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        c.execute(
            f"CREATE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
            f" IF NEW.vehicle = '{v}' THEN RAISE EXCEPTION 'tracker test'; END IF;"
            f" RETURN NEW; END $$"
        )
        c.execute(
            f"CREATE TRIGGER {fn} BEFORE UPDATE ON commands FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )
    try:
        async with stream_values(v, lambda _: {}), worker([v]) as run:
            await until(lambda: run.ctx.counters["cmd_tracker_errors"] >= 2, 10)
            n = len(rows("select seq from telemetry where vehicle=%s", v))
            await asyncio.sleep(0.5)
            assert len(rows("select seq from telemetry where vehicle=%s", v)) > n  # frames commit
            assert not run.done() and row_of(v, cmd_id)["state"] == "SENT" and pending(v) == 1
            # off the event loop: DROP TRIGGER waits for the worker's open tracker transaction,
            # which only this loop can finish (a sync call here deadlocked the full run for 2 h)
            await asyncio.to_thread(drop_trigger, fn)
            await until(lambda: row_of(v, cmd_id)["state"] == "RECEIVED", 5)
            await until(lambda: pending(v) == 0, 5)  # commit, then ack: the XACK follows the state
    finally:
        with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
            c.execute(f"DROP TRIGGER IF EXISTS {fn} ON commands")
            c.execute(f"DROP FUNCTION IF EXISTS {fn}()")


async def test_no_false_failed_after_takeover() -> None:  # Review Focus 1
    v = vid()
    sent = now() - timedelta(seconds=8)
    cmd_id = insert_cmd(v, sent_at=sent, queued_at=sent - timedelta(seconds=0.1))
    tcack(v, cmd_id, sent + timedelta(seconds=0.3))
    produce_values(
        v, {"cabin_temp_c": [22.0] * 5}, last_cmd_id=cmd_id, base=sent + timedelta(seconds=0.4)
    )
    async with worker([v]):
        await until(lambda: row_of(v, cmd_id)["state"] == "VERIFIED", 5)
    r = row_of(v, cmd_id)
    assert r["received_at"] == sent + timedelta(seconds=0.3)
    assert r["verified_at"] == sent + timedelta(seconds=0.4)
    assert r["failure_reason"] is None
    assert [s for a, s in cmd_audits(cmd_id) if a == "CMD_STATE"] == ["RECEIVED", "VERIFIED"]


async def test_no_false_failed_with_echo_in_tm_backlog() -> None:  # no ack, both windows past
    v = vid()
    sent = now() - timedelta(seconds=20)
    cmd_id = insert_cmd(v, sent_at=sent, queued_at=sent - timedelta(seconds=0.1))
    base = sent - timedelta(seconds=10)  # frames 0..119 before the echo: a full first read
    produce_values(v, {"cabin_temp_c": [22.0] * 120}, base=base)
    produce_values(v, {"cabin_temp_c": [22.0] * 30}, last_cmd_id=cmd_id, base=base, start_seq=120)
    async with worker([v]):
        await until(lambda: row_of(v, cmd_id)["state"] != "SENT", 8)
        await asyncio.sleep(1.0)
    r = row_of(v, cmd_id)
    assert (r["state"], r["failure_reason"]) == ("VERIFIED", None)
    assert r["verified_at"] == base + timedelta(seconds=12.0)  # sent + 2 s: inside both windows
    assert r["received_at"] is None


async def test_fenced_tracker_reads_no_tcack() -> None:  # D-ING-12, R2-06
    v = vid()
    tcack(v, insert_cmd(v), now())
    async with direct() as (ctx, conn):
        await conn.close()  # the lock session is gone
        await track_pass(ctx, conn, v, caught_up=True)
    assert stream_entries(f"tcack:{v}") and pending(v) == 0  # nothing entered our PEL
