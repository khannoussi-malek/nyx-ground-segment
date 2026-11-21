"""A calibrated parameter end to end (D-EXT-04): raw and engineering stored, limits on
engineering, out-of-table raw is a NULL plus a RED alarm, never a DLQ."""

import json
import random
from datetime import timedelta
from pathlib import Path

import psycopg

from gs.archiver.window import window_hours
from gs.common.migrate import add_columns
from gs.common.missiondb import MissionDb, load_dir
from gs.simulator.frames import make_frame
from tests.support.archive import app_conn
from tests.support.env import until, vid
from tests.support.ingest import worker
from tests.support.missionfiles import edit, mission_copy
from tests.support.pg import app_dsn, owner_dsn, rows, throwaway_db
from tests.support.recompute import recompute_columns, recompute_keys
from tests.support.redisx import dlq_entries, drained, produce_values, time_base, xadd_raw
from tests.support.s3 import s3_client

P = "bus_current_a"  # limits: yellow_high 8, red_high 10
TABLE = "[[0, 0], [20, 40]]"  # engineering = 2 x raw on 0..20


def setup(tmp_path: Path, db: str) -> tuple[Path, MissionDb]:
    m = mission_copy(tmp_path)
    edit(
        m / "parameters.yaml",
        "description: Main bus current}",
        f"description: Main bus current, calibration: {{table: {TABLE}}}}}",
    )
    mdb = load_dir(m)
    with psycopg.connect(owner_dsn(db), autocommit=True) as c:
        add_columns(c, mdb)
    return m, mdb


def telemetry(db: str, v: str) -> list[dict[str, object]]:
    return rows(f"select seq, {P} as eng, {P}_raw as raw from telemetry where vehicle=%s"
                " order by seq", v, db=db)  # fmt: skip


def alarms(db: str, v: str) -> list[tuple[object, ...]]:
    q = "select kind, level, check_kind, value from alarm_events where vehicle=%s order by id"
    return [tuple(r.values()) for r in rows(q, v, db=db)]


async def test_raw_and_eng_stored_limits_on_eng_and_out_of_table_red(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        produce_values(v, {P: [3.0] * 3 + [25.0] * 3})  # raw 3 -> 6 nominal; 25 is off the table
        async with worker([v], dsn=app_dsn(db), mission_dir=m) as w:
            await until(lambda: drained(v), 10)
        assert w.ctx.counters[f"calibration_out_of_range:{P}"] == 3
        t = telemetry(db, v)
        assert [(r["eng"], r["raw"]) for r in (t[0], t[3])] == [(6.0, 3.0), (None, 25.0)]
        assert alarms(db, v) == [("RAISE", "RED_HIGH", "calibration_range", 25.0)]


async def test_limit_checks_see_engineering(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        produce_values(v, {P: [6.0] * 4})  # raw 6 is nominal, engineering 12 is over red_high 10
        async with worker([v], dsn=app_dsn(db), mission_dir=m):
            await until(lambda: drained(v), 10)
        assert alarms(db, v) == [("RAISE", "RED_HIGH", "limit", 12.0)]


async def test_overflow_null_is_counted(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        edit(m / "parameters.yaml", TABLE, "[[0, -1.7e+308], [20, 1.7e+308]]")
        produce_values(v, {P: [3.0] * 2})
        async with worker([v], dsn=app_dsn(db), mission_dir=m) as w:
            await until(lambda: drained(v), 10)
        assert w.ctx.counters[f"calibration_nonfinite:{P}"] == 2


async def test_nonfinite_raw_is_dlq_schema_not_a_null(tmp_path: Path) -> None:
    v = vid()
    frame = json.loads(make_frame(v, 0, time_base(), random.Random(0)))
    text = json.dumps(frame).replace(f'"{P}": {frame[P]}', f'"{P}": 1e999')
    assert "1e999" in text
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        xadd_raw(v, text)
        async with worker([v], dsn=app_dsn(db), mission_dir=m):
            await until(lambda: drained(v), 10)
        assert [json.loads(d)["reason"] for d in dlq_entries(v)] == ["schema"]
        assert telemetry(db, v) == []


async def test_recompute_over_stored_rows_reproduces_recorded_keys(tmp_path: Path) -> None:
    """The evidence path (replay equality, chaos I3) feeds the stored `<name>_raw` back through
    fold, so a calibrated parameter is calibrated once, not twice."""
    v = vid()
    with throwaway_db() as db:
        m, mdb = setup(tmp_path, db)
        produce_values(v, {P: [3.0] * 4 + [25.0] * 3 + [3.0] * 4})
        async with worker([v], dsn=app_dsn(db), mission_dir=m):
            await until(lambda: drained(v), 10)
        t0 = time_base()
        async with app_conn(db) as c:
            frames = [
                t
                async for t in window_hours(
                    c, s3_client("archiver"), "archive", v, t0 - timedelta(hours=1),
                    t0 + timedelta(hours=1), recompute_columns([mdb]),
                )
            ]  # fmt: skip
        keys, _ = recompute_keys(frames, [], {mdb.set_sha256: mdb}, v, [(0, mdb.set_sha256)])
        q = "select key from alarm_events where vehicle=%s order by id"
        recorded = [r["key"] for r in rows(q, v, db=db)]
        assert recorded and keys == recorded
