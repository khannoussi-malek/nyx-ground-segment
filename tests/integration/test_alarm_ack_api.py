"""ACK requests applied by the owner (D-ALM-14), requests inserted as the API would."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import sql

from gs.ingest.acks import pending_acks
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
from tests.support.redisx import alarm_msgs, drained, produce_values, read_without_ack

TEMP = "cabin_temp_c"
RED_STEP = {TEMP: [22.0] * 5 + [31.0] * 5}  # RAISE at seq 7, last frame seq 9
NOMINAL5 = {TEMP: [22.0] * 5}


async def red_alarm(v: str, base: datetime) -> uuid.UUID:
    """A RED cabin_temp_c alarm raised and committed by a worker that has stopped again."""
    produce_values(v, RED_STEP, base=base)
    async with worker([v]):
        await until(lambda: open_alarm(v, TEMP) is not None and drained(v), 5)
    a = open_alarm(v, TEMP)
    assert a is not None
    return uuid.UUID(str(a["alarm_id"]))


def request(v: str, alarm_id: uuid.UUID, note: str = "seen, venting", user: str = "fc1") -> str:
    """INSERT the way the API does (D-ALM-14); returns the request_id as text."""
    rid = uuid.uuid4()
    with psycopg.connect(app_dsn("gs")) as c:
        c.execute(
            "insert into alarm_ack_request (request_id, alarm_id, vehicle, by_user, note)"
            " values (%s, %s, %s, %s, %s)",
            (rid, alarm_id, v, user, note),
        )
    return str(rid)


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
