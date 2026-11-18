"""Recorded rows -> replay start state (Review Focus 4): engine transitions and an ACK written by
the ingest writers, read back by the replay on a session in a non-UTC TimeZone."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from gs.alarms.contract import Transition
from gs.alarms.engine import EngineState, ack, compile_set, initial_state, step
from gs.common.interfaces import ReplayAlarm, ReplayStart
from gs.common.missiondb import load_dir
from gs.common.views import alarm_views
from gs.ingest.acks import AckReq, write_ack
from gs.ingest.seq import SeqState
from gs.ingest.store import write_transitions
from gs.replay.rows import los_open_at
from gs.replay.run import replay_messages
from tests.support.alarms import PERIOD_S
from tests.support.archive import insert_late
from tests.support.env import now, vid
from tests.support.ingest import ensure_set
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn
from tests.support.s3 import s3_client

MDB = load_dir(MISSION_DIR)
CSET = compile_set(MDB)
P = "cabin_temp_c"
RED_T, YELLOW_T = 31.0, 28.0


@pytest.fixture
async def conn() -> AsyncIterator[psycopg.AsyncConnection]:
    await ensure_set()
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        await c.execute("SET TIME ZONE 'Asia/Tokyo'")  # hours and positions are UTC instants
        yield c


class Recorder:
    """Frames into telemetry and their transitions into alarms/alarm_events, as ingest does."""

    def __init__(self, conn: psycopg.AsyncConnection, v: str) -> None:
        self.conn, self.v = conn, v
        self.epoch = now().replace(microsecond=0) - timedelta(minutes=2)  # never archivable
        self.state: EngineState = initial_state(CSET)
        self.seq = 0

    def t(self, seq: int) -> datetime:
        return self.epoch + timedelta(seconds=seq * PERIOD_S)

    async def frames(self, value: float, n: int = 3) -> list[Transition]:
        out: list[Transition] = []
        for s in range(self.seq, self.seq + n):
            insert_late(self.v, [{"t_onboard": self.t(s), "seq": s, P: value}])
            self.state, trs = step(
                self.state, CSET, self.v, self.epoch, s, self.t(s), PERIOD_S, {P: value}, False
            )
            await write_transitions(self.conn, MDB.set_sha256, trs)
            out.extend(trs)
        self.seq += n
        return out

    async def ack(self) -> None:
        a = self.state.alarms[P]
        req = AckReq(uuid4(), a.alarm_id, P, "fc1", "seen")
        self.state, tr = ack(self.state, self.v, P, "fc1", "seen", str(req.request_id), now())
        after = SeqState(self.epoch, self.seq - 1, self.t(self.seq - 1), PERIOD_S)
        await write_ack(self.conn, self.v, req, "applied", None, tr, after)


async def no_sleep(_: float) -> None:
    pass


async def test_initial_state_acked_then_escalate(conn: psycopg.AsyncConnection) -> None:
    v = vid()
    r = Recorder(conn, v)
    (raised,) = await r.frames(YELLOW_T)
    await r.ack()
    await r.frames(YELLOW_T, 2)
    (live_acked,) = await alarm_views(conn, vehicle=v)
    assert live_acked.state == "ACKED"
    t_from = r.t(r.seq)
    (esc,) = await r.frames(RED_T)
    assert esc.kind == "ESCALATE"
    (live_esc,) = await alarm_views(conn, vehicle=v)

    msgs: list[Any] = [
        m
        async for m in replay_messages(
            conn, s3_client("replay"), "archive", MDB, uuid4(), v, t_from, r.t(r.seq), 10,
            sleep=no_sleep,
        )
    ]  # fmt: skip
    start = msgs[0]
    assert isinstance(start, ReplayStart)
    assert start.alarms == [live_acked]  # the live view at t_from (no ack_request row exists)
    (alarm,) = [m.alarm for m in msgs if isinstance(m, ReplayAlarm)]
    assert (alarm.state, alarm.severity, alarm.last.kind) == ("UNACK", "RED", "ESCALATE")
    assert alarm == live_esc
    assert alarm.alarm_id == raised.alarm_id


async def _event(conn: psycopg.AsyncConnection, v: str, kind: str, t: datetime) -> None:
    await conn.execute(
        "INSERT INTO events (key, vehicle, kind, t_onboard) VALUES (%s, %s, %s, %s)",
        (f"{kind}/{v}/{t.isoformat()}", v, kind, t),
    )


async def test_los_open_at_window_start(conn: psycopg.AsyncConnection) -> None:
    v, other = vid(), vid()
    t = now().replace(microsecond=0)
    await _event(conn, v, "LOS", t)
    await _event(conn, v, "GAP", t + timedelta(seconds=1))
    await _event(conn, v, "AOS", t + timedelta(seconds=10))
    await _event(conn, other, "LOS", t + timedelta(seconds=15))
    await _event(conn, v, "LOS", t + timedelta(seconds=20))
    got = [
        await los_open_at(conn, v, t + timedelta(seconds=s)) for s in (0, 0.001, 5, 10, 11, 20, 21)
    ]
    assert got == [False, True, True, True, False, False, True]


async def test_initial_state_excludes_closed_and_keeps_returned_reading(
    conn: psycopg.AsyncConnection,
) -> None:
    from gs.replay.rows import initial_alarms, recorded_alarm_rows

    v = vid()
    r = Recorder(conn, v)
    await r.frames(YELLOW_T)
    await r.frames(22.0)
    before_ack = r.t(r.seq - 1) + timedelta(microseconds=1)
    (returned,) = await initial_alarms(conn, v, before_ack)
    assert (returned.state, returned.level, returned.value, returned.severity) == (
        "RTN_UNACK",
        "YELLOW_HIGH",
        YELLOW_T,
        "YELLOW",
    )
    await r.ack()
    assert await initial_alarms(conn, v, before_ack) == []
    rows = await recorded_alarm_rows(conn, v, r.epoch, before_ack)
    assert [row["closes"] for row in rows] == [False, False, True]
    assert rows[-1]["after_t_onboard"] == r.t(r.seq - 1)
    assert rows[-1]["trigger_t_onboard"] is None
    msgs = [
        m
        async for m in replay_messages(
            conn,
            s3_client("replay"),
            "archive",
            MDB,
            uuid4(),
            v,
            r.epoch,
            before_ack,
            10,
            sleep=no_sleep,
        )
    ]
    alarms = [m.alarm for m in msgs if isinstance(m, ReplayAlarm)]
    assert [a.state for a in alarms] == ["UNACK", "RTN_UNACK", "CLEARED"]
    (live,) = await alarm_views(conn, vehicle=v, open_only=False)
    assert alarms[-1] == live


async def test_initial_state_excludes_clear_and_future_alarms(
    conn: psycopg.AsyncConnection,
) -> None:
    from gs.replay.rows import initial_alarms

    v = vid()
    r = Recorder(conn, v)
    await r.frames(YELLOW_T)
    await r.ack()
    await r.frames(22.0)
    cutoff = r.t(r.seq)
    await r.frames(RED_T)
    assert await initial_alarms(conn, v, cutoff) == []


async def test_recorded_events_use_vehicle_time_and_exclude_ground(
    conn: psycopg.AsyncConnection,
) -> None:
    from gs.replay.rows import recorded_event_rows

    v, other, t = vid(), vid(), now().replace(microsecond=0)
    for vehicle, kind, seconds in [
        (v, "LOS", -1),
        (v, "GAP", 0),
        (v, "AOS", 1),
        (v, "LOS", 2),
        (other, "LOS", 0),
    ]:
        await _event(conn, vehicle, kind, t + timedelta(seconds=seconds))
    await conn.execute(
        "INSERT INTO events (key, kind, t_onboard) VALUES (%s, 'LIMITS_REJECTED', %s)",
        (str(uuid4()), t),
    )
    got = await recorded_event_rows(conn, v, t, t + timedelta(seconds=2))
    assert [e.kind for e in got] == ["GAP", "AOS"]
    assert got[0].t_onboard == t


async def test_historical_trigger_check_and_hashes_ignore_future_transition(
    conn: psycopg.AsyncConnection,
) -> None:
    from dataclasses import replace

    from gs.alarms.contract import Level
    from gs.ingest.events import insert_set
    from gs.replay.rows import initial_alarms

    v = vid()
    r = Recorder(conn, v)
    (raised,) = await r.frames(YELLOW_T)
    await r.ack()
    after_ack = r.t(r.seq)  # ACK is before this cutoff; ROC is already stored but later.
    future = r.t(r.seq + 5)
    new_set_hash, new_limits_hash = uuid4().hex * 2, uuid4().hex * 2
    await insert_set(conn, replace(MDB, set_sha256=new_set_hash, limits_sha256=new_limits_hash))
    await write_transitions(
        conn,
        new_set_hash,
        [
            replace(
                raised,
                key=str(uuid4()),
                kind="ESCALATE",
                check_kind="roc",
                trigger_check="roc",
                level="RED_RISING",
                severity=Level.RED,
                alarm_state="UNACK",
                alert=True,
                trigger_seq=r.seq + 5,
                trigger_t=future,
            )
        ],
    )
    (current,) = await alarm_views(conn, vehicle=v)
    assert (current.trigger_check, current.set_sha256) == ("roc", new_set_hash)
    (historic,) = await initial_alarms(conn, v, after_ack)
    assert (
        historic.trigger_check,
        historic.state,
        historic.limits_sha256,
        historic.set_sha256,
    ) == (
        "limit",
        "ACKED",
        MDB.limits_sha256,
        MDB.set_sha256,
    )
    for start in (r.epoch, after_ack):
        msgs = [
            m
            async for m in replay_messages(
                conn,
                s3_client("replay"),
                "archive",
                MDB,
                uuid4(),
                v,
                start,
                future,
                10,
                sleep=no_sleep,
            )
        ]
        views = [a for m in msgs if isinstance(m, ReplayStart) for a in m.alarms]
        views += [m.alarm for m in msgs if isinstance(m, ReplayAlarm)]
        assert views
        assert all(
            (a.trigger_check, a.limits_sha256, a.set_sha256)
            == (
                "limit",
                MDB.limits_sha256,
                MDB.set_sha256,
            )
            for a in views
        )
    (later,) = await initial_alarms(conn, v, future + timedelta(microseconds=1))
    assert (later.trigger_check, later.limits_sha256, later.set_sha256) == (
        "roc",
        new_limits_hash,
        new_set_hash,
    )
