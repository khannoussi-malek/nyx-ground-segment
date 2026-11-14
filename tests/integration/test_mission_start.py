import asyncio
import time
from pathlib import Path

import psycopg
import pytest
from pytest import MonkeyPatch

from gs.common.migrate import add_columns
from gs.common.missiondb import MissionDb, MissionDbError, load_dir
from gs.ingest import mission
from gs.ingest.events import GroundEvent, insert_set
from gs.ingest.mission import MissionWatcher, start_set
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy
from tests.support.pg import app_dsn, owner_dsn, throwaway_db

SHIPPED = load_dir(MISSION_DIR)


def add_param(m: Path, name: str = "cabin_humidity_pct") -> None:
    with (m / "parameters.yaml").open("a") as f:
        f.write(f"  {name}: {{unit: pct, type: float}}\n")


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


async def test_start_fallback_takes_newest_valid_row_and_skips_invalid(tmp_path: Path) -> None:
    newer, bad_row, bad_files = (mission_copy(tmp_path / n) for n in ("n", "r", "f"))
    edit(newer / "limits.yaml", "persistence: 3", "persistence: 4")
    add_param(bad_row, "cabin_co2_pct")
    add_param(bad_files)
    with throwaway_db() as db:
        migrate(db, SHIPPED)
        sets = [SHIPPED, load_dir(newer), load_dir(bad_row)]  # oldest .. newest
        async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
            for s in sets:
                await insert_set(c, s)
        mdb, ev = await start_set_on(db, bad_files)
        assert mdb.set_sha256 == sets[1].set_sha256  # not the oldest, not the invalid newest
        assert [e.kind for e in ev] == ["LIMITS_REJECTED"]


REJ = "select key, vehicle from events where kind='LIMITS_REJECTED'"


async def test_watcher_rejects_then_revalidates_after_migrate(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    m = mission_copy(tmp_path)
    calls: list[int] = []

    def counting(*a: object, **k: object) -> MissionDb:
        calls.append(1)
        return real(*a, **k)  # type: ignore[arg-type]

    real = mission.validate_set
    monkeypatch.setattr(mission, "validate_set", counting)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        try:
            add_param(m)
            bad_sha = load_dir(m).set_sha256
            assert await _until(lambda: len(rows(db, REJ)) == 1, 5)
            assert rows(db, REJ) == [(f"LIMITS_REJECTED/{bad_sha}", None)]
            assert rows(db, "select 1 from pg_stat_activity where application_name='t-mission'")
            assert w.current.set_sha256 == SHIPPED.set_sha256
            # a re-validation happens (and re-inserts the same key) while still un-migrated
            assert await _until(lambda: len(calls) >= 2, mission.REVALIDATE_S + 3)
            assert len(rows(db, REJ)) == 1 and w.current.set_sha256 == SHIPPED.set_sha256
            migrate(db, load_dir(m))
            assert await _until(lambda: w.current.set_sha256 == bad_sha, mission.REVALIDATE_S + 4)
            assert len(rows(db, REJ)) == 1
        finally:
            stop.set()
            await task


async def _ticks(w: MissionWatcher, db: str, n: int) -> None:
    async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
        for _ in range(n):
            await w._tick(c)


async def test_tick_needs_two_equal_polls(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        edit(m / "limits.yaml", "persistence: 3", "persistence: 4")
        await _ticks(w, db, 1)
        edit(m / "limits.yaml", "persistence: 4", "persistence: 5")  # changed between polls
        await _ticks(w, db, 1)
        assert w.current is SHIPPED
        await _ticks(w, db, 1)
        assert "persistence: 5" in w.current.limits_yaml


async def test_tick_rejects_hazardous_downgrade(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
        await _ticks(w, db, 2)
        assert w.current is SHIPPED
        err = rows(db, "select details->>'error' from events where kind='LIMITS_REJECTED'")
        assert len(err) == 1 and "hazardous true→false needs a restart" in str(err[0][0])


async def test_tick_skips_unreadable_file_and_forgets_rejection_on_revert(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with throwaway_db() as db:
        await store_shipped_set(db)
        w = MissionWatcher(m, app_dsn(db), "t", SHIPPED)
        good = (m / "limits.yaml").read_bytes()
        (m / "limits.yaml").unlink()  # mid-replace
        await _ticks(w, db, 3)
        assert w.current is SHIPPED and rows(db, REJ) == []
        (m / "limits.yaml").write_bytes(good)
        add_param(m)
        await _ticks(w, db, 2)
        assert w._rejected is not None
        (m / "parameters.yaml").write_bytes((MISSION_DIR / "parameters.yaml").read_bytes())
        await _ticks(w, db, 1)
        assert w._rejected is None and w.current is SHIPPED
