"""Replay equals what was recorded (D-REP-05) and step() reproduces the recorded keys (D-REP-06).
One recording per module, made through the real paths: a vid() vehicle on an in-process worker,
a 70 min backfill with a leak and a 10 s dropout, an ACK through the API (ack-then-escalate), a
limits edit, then the archiver run with retention 0. The "hot" test must run before anything
archives; the recording is archived on first need."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pyarrow as pa
import pytest

from gs.alarms.contract import TransitionKind
from gs.api.app import create_app
from gs.archiver.hours import HOUR, hour_of
from gs.archiver.main import run_pass
from gs.archiver.window import window_hours
from gs.common.interfaces import ReplayAlarm, ReplayLink, ReplayTm
from gs.common.missiondb import MissionDb, load, load_dir
from gs.replay.rows import next_state
from gs.replay.run import replay_messages
from gs.simulator.backfill import backfill, parse_fault
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archive_db,
    archiver_ctx,
    archiver_paused,
    insert_late,
)
from tests.support.env import until, vid
from tests.support.http import api_settings, authed
from tests.support.ingest import worker
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy, set_formula
from tests.support.pg import admin_dsn, app_dsn, rows
from tests.support.recompute import recompute_keys
from tests.support.redisx import aclient, client, drained
from tests.support.s3 import s3_client

pytestmark = pytest.mark.usefixtures("archiver_paused")
START = hour_of(datetime.now(UTC)) - timedelta(days=3)
END = START + timedelta(minutes=70)
ACK_AT, EDIT_AT = 6050, 30_000  # ROC alarm YELLOW (raised at 6006, RED at 6160); after the dropout
ROC = "cabin_pressure_kpa"
SHIPPED = load_dir(MISSION_DIR)


@dataclass
class Recording:
    db: str
    vehicle: str
    ack_key: str
    archived: bool = False


async def post_ack(users_file: Path, db: str, v: str) -> str:
    (alarm,) = rows("select alarm_id from alarms where vehicle=%s and param=%s", v, ROC, db=db)
    rid = uuid4()
    app = create_app(api_settings(users_file, database_url=app_dsn(db)))
    async with authed(app) as c:
        # P-pf42b: an ACK within 2 s of the RAISE/ESCALATE it did not see is superseded
        await asyncio.sleep(2.5)
        r = await c.post(
            f"/api/alarms/{alarm['alarm_id']}/ack",
            json={"request_id": str(rid), "note": "seen, watching"},
        )
        assert r.status_code == 202, r.text
    return str(rid)


async def record(users_file: Path, db: str, v: str, m: Path) -> str:
    acked: list[str] = []

    async def resume(n: int) -> None:
        if n == ACK_AT:
            acked.append(await post_ack(users_file, db, v))
            q = "select status from alarm_ack_request where request_id=%s"
            await until(lambda: [r["status"] for r in rows(q, acked[0], db=db)] == ["applied"], 30)
        else:
            edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 26.5")
            q = "select 1 from limits_versions where set_sha256=%s"
            await until(lambda: bool(rows(q, set_formula(m), db=db)), 10)

    faults = [parse_fault("leak@600", v), parse_fault('dropout@2400:{"duration_s":10}', v)]
    async with worker([v], dsn=app_dsn(db), mission_dir=m):
        r = aclient("sim")
        try:
            await backfill(r, v, START, END, "seed", faults, [ACK_AT, EDIT_AT], resume)
        finally:
            await r.aclose()
        await until(lambda: drained(v), 600)
        q = (
            "select 1 from events where vehicle=%s and kind='LIMITS_APPLIED'"
            " and details->>'set_sha256'=%s"
        )
        await until(lambda: bool(rows(q, v, set_formula(m), db=db)), 10)
    return acked[0]


@pytest.fixture(scope="module")
def recording(users_file: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Recording]:
    v = vid()
    m = mission_copy(tmp_path_factory.mktemp("rec"))
    with archive_db() as db:
        try:
            key = asyncio.run(record(users_file, db, v, m))
            yield Recording(db, v, key)
        finally:
            client("admin").delete(f"tm:{v}")
            root = s3_client("root")
            for o in root.list_objects("archive", prefix=f"{v}/", recursive=True):
                root.remove_object("archive", o.object_name)


async def archive(rec: Recording) -> None:
    if rec.archived:
        return
    async with app_conn(rec.db) as c:
        res = await run_pass(archiver_ctx(retention_h=0.0), c)
        assert not res.failed and res.archived >= 1
    assert rows(
        "select 1 from archive_object where vehicle=%s and hour=%s", rec.vehicle, START, db=rec.db
    )
    rec.archived = True


async def no_sleep(_: float) -> None:
    return None


async def replay(rec: Recording, mdb: MissionDb = SHIPPED, t_to: datetime = END) -> list[Any]:
    async with app_conn(rec.db) as c:
        return [
            m
            async for m in replay_messages(
                c, s3_client("archiver"), "archive", mdb, uuid4(), rec.vehicle, START, t_to, 10,
                no_sleep,
            )
        ]  # fmt: skip


def recorded_alarm_keys(rec: Recording) -> list[str]:
    return [
        r["key"]
        for r in rows(
            "select key from alarm_events where vehicle=%s order by"
            " coalesce(trigger_t_onboard, after_t_onboard), coalesce(trigger_seq, after_seq), id",
            rec.vehicle, db=rec.db,
        )
    ]  # fmt: skip


def recorded_events(rec: Recording) -> list[dict[str, Any]]:
    return rows(
        "select key, kind, t_onboard from events where vehicle=%s and t_onboard >= %s"
        " and t_onboard < %s order by t_onboard, id", rec.vehicle, START, END, db=rec.db,
    )  # fmt: skip


@pytest.mark.parametrize("source", ["hot", "parquet"])  # hot first: nothing is archived yet
async def test_replay_equals_recorded(source: str, recording: Recording) -> None:
    if source == "parquet":
        await archive(recording)
    else:
        assert not recording.archived
    msgs = await replay(recording)
    alarm_keys = [m.alarm.last.key for m in msgs if isinstance(m, ReplayAlarm)]
    event_keys = [m.event.key for m in msgs if isinstance(m, ReplayLink)]
    assert alarm_keys == recorded_alarm_keys(recording)
    assert event_keys == [e["key"] for e in recorded_events(recording)]
    kinds = [m.alarm.last.kind for m in msgs if isinstance(m, ReplayAlarm)]
    assert kinds[:3] == ["RAISE", "ACK", "ESCALATE"]  # ack-then-escalate
    assert recording.ack_key in alarm_keys
    assert {"GAP", "LIMITS_APPLIED"} <= {e["kind"] for e in recorded_events(recording)}
    tms = [m for m in msgs if isinstance(m, ReplayTm)]
    assert len({m.seq for m in tms}) == len(tms) == 42_000 - 100  # the 10 s dropout lost 100
    # position order: time never goes back across the whole stream
    ts: list[datetime] = []
    for m in msgs:
        if isinstance(m, ReplayAlarm):
            ts.append(m.alarm.last.t)
        elif isinstance(m, ReplayLink) and m.event.t_onboard:
            ts.append(m.event.t_onboard)
        elif isinstance(m, ReplayTm):
            ts.append(m.t_onboard)
    assert ts == sorted(ts)


async def frames_of(rec: Recording) -> list[pa.Table]:
    async with app_conn(rec.db) as c:
        return [
            t
            async for t in window_hours(
                c, s3_client("archiver"), "archive", rec.vehicle, START, END, SHIPPED.param_names
            )
        ]


def pair_gaps(events: list[dict[str, Any]]) -> set[str]:
    """Keys of LOS/AOS pairs closer than 2 s in vehicle time: the read loop's wall-clock timer
    fired while the backfill paused; the fold never sees such a gap (it never resets the engine)."""
    out: set[str] = set()
    for i, e in enumerate(events):
        nxt = next((x for x in events[i + 1 :] if x["kind"] == "AOS"), None)
        if e["kind"] == "LOS" and nxt and nxt["t_onboard"] - e["t_onboard"] <= timedelta(seconds=2):
            out |= {e["key"], nxt["key"]}
    return out


async def test_recompute_reproduces_recorded_keys(recording: Recording) -> None:
    db, v = recording.db, recording.vehicle
    sets = {
        r["set_sha256"]: load(
            r["limits_yaml"].encode(), r["commands_yaml"].encode(), r["parameters_yaml"].encode()
        )
        for r in rows("select * from limits_versions", db=db)
    }
    applied = [
        (r["details"]["first_seq"], r["details"]["set_sha256"])
        for r in rows(
            "select details from events where vehicle=%s and kind='LIMITS_APPLIED' order by id",
            v, db=db,
        )
    ]  # fmt: skip
    assert len(applied) == 2 and applied[0][0] == 0
    acks = rows("select * from alarm_events where vehicle=%s and kind='ACK' order by id", v, db=db)
    keys, events = recompute_keys(await frames_of(recording), acks, sets, v, applied)
    recorded = rows("select key from alarm_events where vehicle=%s order by id", v, db=db)
    assert keys == [r["key"] for r in recorded]  # incl. the ACK row, in order
    q = "select key, kind, t_onboard from events where vehicle=%s order by id"
    all_events = rows(q, v, db=db)
    frame_events = {e["key"] for e in all_events} - pair_gaps(all_events)
    assert set(events) >= frame_events  # GAP, SEQ_*, FROZEN, LIMITS_APPLIED, the dropout's LOS pair
    assert any(e["kind"] == "LOS" and e["key"] in frame_events for e in all_events)


async def test_reducer_matches_alarms_table(recording: Recording) -> None:
    db, v = recording.db, recording.vehicle
    history = rows("select * from alarm_events where vehicle=%s order by id", v, db=db)
    final: dict[UUID, Any] = {}
    previous: dict[UUID, str] = {}
    for r in history:
        aid, kind = r["alarm_id"], r["kind"]
        closes = kind == "CLEAR" or (kind == "ACK" and previous.get(aid) == "RTN")
        kind_t: TransitionKind = kind
        final[aid] = next_state(final.get(aid), kind_t, closes, r["alert"])
        previous[aid] = kind
    assert final
    for a in rows("select alarm_id, state from alarms where vehicle=%s", v, db=db):
        assert final[a["alarm_id"]] == a["state"]


async def test_late_frame_in_archived_hour(recording: Recording) -> None:
    await archive(recording)
    t, seq = START + timedelta(minutes=30, milliseconds=50), 90_000
    insert_late(recording.vehicle, [{"t_onboard": t, "seq": seq}], db=recording.db)
    try:
        tms = [m for m in await replay(recording) if isinstance(m, ReplayTm)]
    finally:
        with psycopg.connect(admin_dsn(recording.db), autocommit=True) as c:
            c.execute("delete from telemetry where vehicle=%s and seq=%s", (recording.vehicle, seq))
    assert [m.seq for m in tms].count(seq) == 1  # merged, once (EX-29)
    assert len(tms) == 42_000 - 100 + 1


async def test_window_across_added_parameter(recording: Recording, tmp_path: Path) -> None:
    await archive(recording)
    col = "g4rp_added"
    m = mission_copy(tmp_path)
    p = m / "parameters.yaml"
    p.write_text(p.read_text() + f"  {col}: {{unit: V, type: float, description: added later}}\n")
    with psycopg.connect(admin_dsn(recording.db), autocommit=True) as c:
        c.execute(f"alter table telemetry add column {col} double precision")  # noqa: S608
        try:
            msgs = await replay(recording, load_dir(m), START + HOUR)
        finally:
            c.execute(f"alter table telemetry drop column {col}")  # noqa: S608
    tms = [x for x in msgs if isinstance(x, ReplayTm)]
    assert len(tms) > 30_000 and all(x.values[col] is None for x in tms)  # EX-19: null, not error
