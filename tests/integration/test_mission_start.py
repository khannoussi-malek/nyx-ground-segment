import asyncio
import time
from pathlib import Path

import psycopg
import pytest

from gs.common.migrate import add_columns
from gs.common.missiondb import MissionDb, MissionDbError, load_dir
from gs.ingest.events import GroundEvent, insert_set
from gs.ingest.mission import MissionWatcher, start_set
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy
from tests.support.pg import app_dsn, owner_dsn, throwaway_db

SHIPPED = load_dir(MISSION_DIR)


def add_param(m: Path) -> None:
    with (m / "parameters.yaml").open("a") as f:
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")


def migrate(db: str, mdb: MissionDb) -> None:
    with psycopg.connect(owner_dsn(db), autocommit=True) as c:
        add_columns(c, mdb)


async def store_shipped_set(db: str) -> None:
    migrate(db, SHIPPED)
    async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
        await insert_set(c, SHIPPED)


async def start_set_on(db: str, m: Path) -> tuple[MissionDb, tuple[GroundEvent, ...]]:
    async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
        return await start_set(c, m)


def rows(db: str, sql: str) -> list[tuple[object, ...]]:
    with psycopg.connect(app_dsn(db)) as c:
        return c.execute(sql).fetchall()


async def test_valid_files_used_and_stored(tmp_path: Path) -> None:
    with throwaway_db() as db:
        migrate(db, SHIPPED)
        mdb, ev = await start_set_on(db, mission_copy(tmp_path))
        assert mdb.set_sha256 == SHIPPED.set_sha256 and ev == ()
        assert rows(db, "select set_sha256 from limits_versions") == [(SHIPPED.set_sha256,)]


async def test_unmigrated_param_at_start_falls_back(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    add_param(m)
    with throwaway_db() as db:
        await store_shipped_set(db)
        mdb, ev = await start_set_on(db, m)
        assert mdb.set_sha256 == SHIPPED.set_sha256 and [e.kind for e in ev] == ["LIMITS_REJECTED"]
        assert "run make migrate" in ev[0].details["error"]
        assert ev[0].details["file"] == "parameters.yaml"
        assert ev[0].details["set_sha256"] != SHIPPED.set_sha256


async def test_invalid_at_start_without_history_refuses(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    edit(m / "limits.yaml", "red_low: 95.0, yellow_low: 98.0", "red_low: 99.0, yellow_low: 98.0")
    with throwaway_db() as db:
        migrate(db, SHIPPED)
        with pytest.raises(MissionDbError):
            await start_set_on(db, m)


async def _until(pred, secs: float) -> bool:  # type: ignore[no-untyped-def]
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.2)
    return False


async def test_watcher_applies_valid_edit_within_5s(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        try:
            edit(m / "limits.yaml", "persistence: 3", "persistence: 4")
            assert await _until(lambda: w.current.set_sha256 != SHIPPED.set_sha256, 5)
            n = rows(db, "select count(*) from limits_versions")[0][0]
            assert n == 2
        finally:
            stop.set()
            await task


async def test_watcher_rejects_then_revalidates_after_migrate(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        try:
            add_param(m)
            ev = "select count(*) from events where kind='LIMITS_REJECTED'"
            assert await _until(lambda: rows(db, ev)[0][0] == 1, 5)
            assert w.current.set_sha256 == SHIPPED.set_sha256
            migrate(db, load_dir(m))
            assert await _until(lambda: w.current.set_sha256 != SHIPPED.set_sha256, 12.5)
            assert rows(db, ev)[0][0] == 1
        finally:
            stop.set()
            await task
