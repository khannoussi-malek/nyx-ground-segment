"""ACK requests applied by the owner (D-ALM-14), requests inserted as the API would."""

import asyncio
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest
import redis.exceptions
from psycopg import sql

from gs.common.interfaces.sse import Alarm, Tm
from gs.common.uievents import publish_ui
from gs.ingest import worker as worker_mod
from gs.ingest.acks import ACK_PROPAGATION_S, CHANGED, pending_acks
from gs.ingest.worker import Timing, load_state, process_batch
from tests.support.env import now, until, vid
from tests.support.ingest import make_ctx, worker
from tests.support.pg import (
    ack_requests_for,
    alarm_events_for,
    app_dsn,
    audit_rows,
    events_for,
    open_alarm,
    owner_dsn,
    rows,
    stored,
)
from tests.support.redisx import alarm_msgs, drained, pending, produce_values, read_without_ack

TEMP = "cabin_temp_c"
RED_STEP = {TEMP: [22.0] * 5 + [31.0] * 5}  # RAISE at seq 7, last frame seq 9
NOMINAL5 = {TEMP: [22.0] * 5}


async def red_alarm(v: str, base: datetime) -> uuid.UUID:
    """A RED cabin_temp_c alarm raised and committed by a worker that has stopped again, and
    on screen long enough to be acknowledged (P-pf42b)."""
    produce_values(v, RED_STEP, base=base)
    async with worker([v]):
        await until(lambda: open_alarm(v, TEMP) is not None and drained(v), 5)
    a = open_alarm(v, TEMP)
    assert a is not None
    await asyncio.sleep(ACK_PROPAGATION_S)
    return uuid.UUID(str(a["alarm_id"]))


def request(
    v: str,
    alarm_id: uuid.UUID,
    note: str = "seen, venting",
    user: str = "fc1",
    requested_at: datetime | None = None,
) -> str:
    """INSERT the way the API does (D-ALM-14); returns the request_id as text. `requested_at`
    back-dates the request (submitted before something the owner then committed first)."""
    rid = uuid.uuid4()
    with psycopg.connect(app_dsn("gs")) as c:
        c.execute(
            "insert into alarm_ack_request (request_id, alarm_id, vehicle, by_user, note,"
            " requested_at) values (%s, %s, %s, %s, %s, coalesce(%s, clock_timestamp()))",
            (rid, alarm_id, v, user, note, requested_at),
        )
    return str(rid)


def db_now() -> datetime:
    """The database clock: processed_at comes from it, the host clock may differ (VM skew)."""
    (r,) = rows("select clock_timestamp() as t")
    t: datetime = r["t"]
    return t


async def drain(v: str, **kw: Any) -> None:
    """Run a worker until tm:<v> is drained and no request of v is pending."""
    async with worker([v], **kw):
        await until(lambda: drained(v) and "pending" not in {s for s, _ in status(v).values()}, 5)


async def one_batch(v: str) -> None:
    """One owner pass by hand: state as at a takeover, the pending requests, every new entry of
    tm:<v> as ONE batch (so the requests and the frames share one frame transaction)."""
    ctx = make_ctx(f"c-{v}")
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
            vs, _ = await load_state(ctx, conn, v)
            entries = read_without_ack(v, f"c-{v}")
            await process_batch(ctx, conn, f"tm:{v}", entries, vs, await pending_acks(conn, v))
    finally:
        await ctx.redis.aclose()


def status(v: str) -> dict[str, tuple[str, str | None]]:
    return {str(r["request_id"]): (r["status"], r["status_reason"]) for r in ack_requests_for(v)}


def kinds(v: str) -> list[tuple[str, str]]:
    return [(r["kind"], r["by_user"]) for r in alarm_events_for(v) if r["param"] == TEMP]


def applied_audits(ids: set[str]) -> list[dict[str, Any]]:
    return [r for r in audit_rows("ALARM_ACK_APPLIED") if r["details"]["request_id"] in ids]


async def test_ack_after_seq_is_owner_seq() -> None:  # D-ALM-14, R2-10
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    rid = request(v, aid)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    await one_batch(v)
    assert status(v) == {rid: ("applied", None)}
    evs = [r for r in alarm_events_for(v) if r["param"] == TEMP]
    (ack,) = [r for r in evs if r["kind"] == "ACK"]
    assert (ack["key"], ack["by_user"], ack["note"]) == (rid, "fc1", "seen, venting")
    after = (ack["after_epoch"], ack["after_seq"], ack["after_t_onboard"])
    assert after == (base, 9, base + timedelta(milliseconds=900))  # seq_state before the batch
    (clear,) = [r for r in evs if r["kind"] == "CLEAR"]  # from this batch's frames
    assert ack["id"] < clear["id"] and stored(v) == 15


async def test_ack_then_rtn_is_cleared() -> None:  # ACK then RTN gives CLEARED, not RTN_UNACK
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    request(v, aid)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    async with worker([v]):
        await until(lambda: drained(v) and ("CLEAR", "system") in kinds(v), 5)
    assert kinds(v) == [("RAISE", "system"), ("ACK", "fc1"), ("CLEAR", "system")]
    (a,) = rows("select state, closed_at from alarms where alarm_id = %s", aid)
    assert a["state"] == "CLEARED" and a["closed_at"] is not None


async def test_second_ack_superseded() -> None:  # two operators
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    first, second = request(v, aid, user="fc1"), request(v, aid, user="fc2")
    async with worker([v]):
        await until(lambda: "pending" not in {s for s, _ in status(v).values()}, 5)
    assert status(v) == {first: ("applied", None), second: ("superseded", "already acked")}
    assert [k for k in kinds(v) if k[0] == "ACK"] == [("ACK", "fc1")]
    audits = applied_audits({first, second})
    assert [(r["actor"], r["details"]["result"]) for r in audits] == [
        ("system", "applied"),
        ("system", "superseded"),
    ]
    assert audits[0]["details"]["alarm_id"] == str(aid) and audits[0]["reason"] == "seen, venting"


@pytest.fixture
def boom_trigger() -> Iterator[str]:
    """BEFORE INSERT trigger on alarm_events: SQLSTATE 22023 for note 'BOOM' of one vehicle."""
    with boom("22023") as v:
        yield v


@contextmanager
def boom(errcode: str) -> Iterator[str]:
    """A fresh vehicle whose alarm_events INSERT with note 'BOOM' raises SQLSTATE `errcode`."""
    v = vid()
    name = "boom_" + v.lower().replace("-", "_")
    fn, trg = sql.Identifier(name), sql.Identifier(name + "_trg")
    with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
        c.execute(
            sql.SQL(
                "CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
                " IF NEW.vehicle = {} AND NEW.note = 'BOOM' THEN"
                " RAISE EXCEPTION 'test boom' USING ERRCODE = {}; END IF;"
                " RETURN NEW; END $$"
            ).format(fn, sql.Literal(v), sql.Literal(errcode))
        )
        c.execute(
            sql.SQL(
                "CREATE TRIGGER {} BEFORE INSERT ON alarm_events FOR EACH ROW EXECUTE FUNCTION {}()"
            ).format(trg, fn)
        )
    try:
        yield v
    finally:
        with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
            c.execute(sql.SQL("DROP TRIGGER {} ON alarm_events").format(trg))
            c.execute(sql.SQL("DROP FUNCTION {}()").format(fn))


async def test_bad_ack_request_never_blocks_frames(boom_trigger: str) -> None:  # R2-01
    v, base = boom_trigger, now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    bad, good = request(v, aid, note="BOOM"), request(v, aid)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    await one_batch(v)
    st = status(v)
    assert st[good] == ("applied", None) and st[bad][0] == "rejected"
    assert "test boom" in (st[bad][1] or "")
    assert stored(v) == 15  # the batch's frames committed with the requests
    assert kinds(v) == [("RAISE", "system"), ("ACK", "fc1"), ("CLEAR", "system")]
    audits = {r["details"]["request_id"]: r["details"] for r in applied_audits({bad, good})}
    assert audits[bad]["result"] == "rejected" and "test boom" in audits[bad]["reason"]
    assert audits[good]["result"] == "applied"


async def test_plpgsql_raise_rejects_and_ingest_keeps_running() -> None:  # P-pf40
    with boom("P0001") as v:  # a trigger's plain RAISE EXCEPTION
        base = now() - timedelta(minutes=1)
        aid = await red_alarm(v, base)
        bad, good = request(v, aid, note="BOOM"), request(v, aid)
        produce_values(v, NOMINAL5, base=base, start_seq=10)
        async with worker([v]) as w:
            await until(
                lambda: drained(v) and "pending" not in {s for s, _ in status(v).values()}, 5
            )
            assert not w.done()  # no exit, no restart loop
        st = status(v)
        assert st[good] == ("applied", None) and st[bad][0] == "rejected"
        assert (st[bad][1] or "").startswith("P0001: test boom") and stored(v) == 15
        audits = {r["details"]["request_id"]: r["details"] for r in applied_audits({bad, good})}
        assert audits[bad]["result"] == "rejected" and audits[good]["result"] == "applied"


async def test_ack_applies_during_los() -> None:  # Review Focus 2: no frame to start a batch
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, RED_STEP, base=base)
    async with worker([v], timing=Timing(los_s=0.5)):
        await until(lambda: open_alarm(v, TEMP) is not None and bool(events_for(v, "LOS")), 5)
        a = open_alarm(v, TEMP)
        assert a is not None
        await asyncio.sleep(ACK_PROPAGATION_S)  # seen on screen first (P-pf42b)
        rid = request(v, uuid.UUID(str(a["alarm_id"])))
        await until(lambda: status(v).get(rid, ("",))[0] == "applied", 1.5)
    (ack,) = [r for r in alarm_events_for(v) if r["kind"] == "ACK"]
    assert ack["after_seq"] == 9  # the last stored frame


async def test_ack_published_as_alarm_message() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, RED_STEP, base=base)
    async with worker([v]):
        await until(lambda: open_alarm(v, TEMP) is not None and drained(v), 5)
        a = open_alarm(v, TEMP)
        assert a is not None
        await asyncio.sleep(ACK_PROPAGATION_S)  # seen on screen first (P-pf42b)
        rid = request(v, uuid.UUID(str(a["alarm_id"])))
        await until(lambda: any(m["alarm"]["state"] == "ACKED" for m in alarm_msgs(v)), 3)
    (m,) = [m for m in alarm_msgs(v) if m["alarm"]["state"] == "ACKED"]
    assert m["alarm"]["ack_request"] == {
        "request_id": rid,
        "by_user": "fc1",
        "status": "applied",
        "status_reason": None,
    }
    assert (m["alarm"]["last"]["kind"], m["alarm"]["value"]) == ("ACK", 31.0)  # P-pf4


async def test_escalated_after_request_superseded() -> None:  # P-pf42
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {TEMP: [22.0] * 5 + [28.0] * 5}, base=base)
    await drain(v)  # YELLOW UNACK
    a = open_alarm(v, TEMP)
    assert a is not None and a["severity"] == "YELLOW"
    asked = db_now()  # the operator saw YELLOW and sent the ACK ...
    produce_values(v, {TEMP: [31.0] * 5}, base=base, start_seq=10)
    await drain(v)  # ... the owner escalated to RED before applying it
    rid = request(v, uuid.UUID(str(a["alarm_id"])), requested_at=asked)
    await drain(v)
    assert status(v) == {rid: ("superseded", "alarm changed since the request")}
    b = open_alarm(v, TEMP)
    assert b is not None and (b["state"], b["severity"]) == ("UNACK", "RED")
    assert ("ACK", "fc1") not in kinds(v)


async def test_reraised_after_request_superseded() -> None:  # P-pf42
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {TEMP: [22.0] * 5 + [31.0] * 5 + [22.0] * 5}, base=base)
    await drain(v)  # RAISE, RTN: RTN_UNACK
    a = open_alarm(v, TEMP)
    assert a is not None and a["state"] == "RTN_UNACK"
    asked = db_now()
    produce_values(v, {TEMP: [31.0] * 5}, base=base, start_seq=15)
    await drain(v)  # raised again on the same alarm
    rid = request(v, uuid.UUID(str(a["alarm_id"])), requested_at=asked)
    await drain(v)
    assert status(v) == {rid: ("superseded", "alarm changed since the request")}
    b = open_alarm(v, TEMP)
    assert b is not None and b["state"] == "UNACK"
    assert [k for k, _ in kinds(v)] == ["RAISE", "RTN", "RAISE"]


@contextmanager
def raising_on(table: str, column: str, value: str) -> Iterator[None]:
    """BEFORE INSERT trigger on `table`: SQLSTATE 22023 when NEW.<column> = value."""
    name = "t_" + uuid.uuid4().hex[:12]
    fn, trg = sql.Identifier(name), sql.Identifier(name + "_trg")
    with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
        c.execute(
            sql.SQL(
                "CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
                " IF NEW.{} = {} THEN RAISE EXCEPTION 'test boom' USING ERRCODE = '22023';"
                " END IF; RETURN NEW; END $$"
            ).format(fn, sql.Identifier(column), sql.Literal(value))
        )
        c.execute(
            sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON {} FOR EACH ROW EXECUTE FUNCTION {}()")
            .format(trg, sql.Identifier(table), fn)
        )  # fmt: skip
    try:
        yield
    finally:
        with psycopg.connect(owner_dsn("gs"), autocommit=True) as c:
            c.execute(sql.SQL("DROP TRIGGER {} ON {}").format(trg, sql.Identifier(table)))
            c.execute(sql.SQL("DROP FUNCTION {}()").format(fn))


async def test_failed_reject_leaves_request_pending() -> None:  # fix round 1, item 3
    v, base = vid(), now() - timedelta(minutes=1)
    note = f"double boom {v}"
    aid = await red_alarm(v, base)
    rid = request(v, aid, note=note)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    with raising_on("alarm_events", "note", note), raising_on("audit_log", "reason", note):
        await one_batch(v)  # the ACK fails, and so does writing its rejection
    assert status(v) == {rid: ("pending", None)} and not applied_audits({rid})
    assert stored(v) == 15  # the frames still committed


async def test_acked_state_kept_after_publish_error(monkeypatch: pytest.MonkeyPatch) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # fix round 1, item 1
    aid = await red_alarm(v, base)
    request(v, aid)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    failed: list[bool] = []

    async def lose_acked(r: Any, msgs: Any) -> None:
        if not failed and any(isinstance(m, Alarm) and m.alarm.state == "ACKED" for m in msgs):
            failed.append(True)
            raise redis.exceptions.ConnectionError("test: publish lost after the ACK commit")
        await publish_ui(r, msgs)

    monkeypatch.setattr(worker_mod, "publish_ui", lose_acked)
    async with worker([v]):
        await until(lambda: drained(v) and ("CLEAR", "system") in kinds(v), 5)
        await until(lambda: any(m["alarm"]["state"] == "ACKED" for m in alarm_msgs(v)), 3)
    assert failed and kinds(v) == [("RAISE", "system"), ("ACK", "fc1"), ("CLEAR", "system")]


async def yellow_alarm(v: str, base: datetime) -> uuid.UUID:
    produce_values(v, {TEMP: [22.0] * 5 + [28.0] * 5}, base=base)
    await drain(v)
    a = open_alarm(v, TEMP)
    assert a is not None and a["severity"] == "YELLOW"
    return uuid.UUID(str(a["alarm_id"]))


async def test_escalation_just_before_request_superseded() -> None:  # P-pf42b rule 1
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await yellow_alarm(v, base)
    produce_values(v, {TEMP: [31.0] * 5}, base=base, start_seq=10)
    await drain(v)
    (esc,) = rows(
        "select processed_at from alarm_events where alarm_id=%s and kind='ESCALATE'", aid
    )
    rid = request(v, aid, requested_at=esc["processed_at"] + timedelta(seconds=1))
    await drain(v)
    assert status(v) == {rid: ("superseded", CHANGED)}


async def test_ack_during_held_escalation_superseded(monkeypatch: pytest.MonkeyPatch) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # P-pf42b rule 2
    produce_values(v, {TEMP: [22.0] * 5 + [28.0] * 5}, base=base)
    held: list[float] = []  # monotonic end of the outage, from the ESCALATE's publish on

    async def hold_escalation(r: Any, msgs: Any) -> None:
        esc = any(isinstance(m, Alarm) and m.alarm.last.kind == "ESCALATE" for m in msgs)
        if esc and not held:
            held.append(time.monotonic() + 3.0)
        if held and time.monotonic() < held[0]:
            raise redis.exceptions.ConnectionError("test: broker down")
        await publish_ui(r, msgs)

    monkeypatch.setattr(worker_mod, "publish_ui", hold_escalation)
    async with worker([v]):
        await until(lambda: open_alarm(v, TEMP) is not None and drained(v), 5)
        a = open_alarm(v, TEMP)
        assert a is not None
        produce_values(v, {TEMP: [31.0] * 5}, base=base, start_seq=10)
        await until(lambda: bool(held), 5)  # ESCALATE committed, nobody has seen it
        await asyncio.sleep(2.5)  # past rule 1's margin from its processed_at
        rid = request(v, uuid.UUID(str(a["alarm_id"])))
        await until(lambda: status(v)[rid][0] != "pending", 10)
    assert status(v) == {rid: ("superseded", CHANGED)}  # measured from the late publish


async def test_whitespace_note_rejected_not_isolated() -> None:  # P-pf42b item 4
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    rid = request(v, aid, note="\t\n")  # trim() keeps it, Python's strip() does not
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    await one_batch(v)
    st, why = status(v)[rid]
    assert st == "rejected" and (why or "").startswith("ValueError: an ACK needs a note")
    assert stored(v) == 15 and [k for k, _ in kinds(v)] == ["RAISE", "RTN"]


async def test_stuck_rejection_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # P-pf42b item 3
    note = f"stuck {v}"
    aid = await red_alarm(v, base)
    request(v, aid, note=note)
    with (
        raising_on("alarm_events", "note", note),
        raising_on("audit_log", "reason", note),
        caplog.at_level(logging.ERROR, logger="gs.ingest.acks"),
    ):
        async with worker([v]):
            await asyncio.sleep(3.5)  # several loop passes, each retrying the request
    stuck = [r for r in caplog.records if "could not be rejected" in r.getMessage()]
    assert len(stuck) == 1


async def test_frame_batch_with_ack_survives_publish_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # P-pf42b item 5: the frame path of Unpublished
    v, base = vid(), now() - timedelta(minutes=1)
    aid = await red_alarm(v, base)
    request(v, aid)
    produce_values(v, NOMINAL5, base=base, start_seq=10)
    consumer = f"c-{v}"
    assert len(read_without_ack(v, consumer)) == 5  # in its PEL: the first pass claims one
    failed: list[bool] = []

    async def lose_first_batch(r: Any, msgs: Any) -> None:
        tm = any(isinstance(m, Tm) for m in msgs)
        if not failed and tm and any(isinstance(m, Alarm) for m in msgs):
            failed.append(True)  # one frame and the ACK, committed together
            raise redis.exceptions.ConnectionError("test: publish lost after the commit")
        await publish_ui(r, msgs)

    monkeypatch.setattr(worker_mod, "publish_ui", lose_first_batch)
    async with worker([v], consumer=consumer):
        await until(lambda: drained(v) and ("CLEAR", "system") in kinds(v), 8)
    assert failed and pending(v) == 0
    assert kinds(v) == [("RAISE", "system"), ("ACK", "fc1"), ("CLEAR", "system")]
    assert len([r for r in alarm_events_for(v) if r["kind"] == "ACK"]) == 1
