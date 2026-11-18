import asyncio
import io
import logging
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from gs.archiver import main as main_mod
from gs.archiver import s3 as s3_mod
from gs.archiver import snapshot as snap_mod
from gs.archiver.hours import hour_of
from gs.archiver.main import run_pass
from gs.archiver.snapshot import TABLES, snapshot_key, take_snapshot
from scripts.restore_state import main as restore_main
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archive_db,
    archive_rows,
    archiver_ctx,
    archiver_paused,
    hot_hour,
    stored_in,
)
from tests.support.env import vid
from tests.support.pg import owner_dsn, throwaway_db
from tests.support.s3 import s3_client

pytestmark = pytest.mark.usefixtures("archiver_paused")
NOW = datetime(2000, 1, 1, 3, 4, tzinfo=UTC)  # far in the past: never mixes with a real snapshot


@pytest.fixture(autouse=True)
def _remove_test_snapshots() -> Iterator[None]:
    def purge() -> None:
        root = s3_client("root")
        for o in root.list_objects("archive", prefix="state/2000/", recursive=True):
            root.remove_object("archive", o.object_name)

    purge()
    yield
    purge()


ALARM = "00000000-0000-0000-0000-0000000000a1"
SEED = [
    "insert into limits_versions values ('s','l','c','p','ly','cy','py')",
    "insert into test_session (vehicle, name, conductor) values ('V1','run, \"one\"\nline','c')",
    "insert into commands (cmd_id, vehicle, name, args, args_hash, hazardous, reason, issued_by,"
    " state, queued_at) values ('00000000-0000-0000-0000-0000000000c1','V1','n','{\"a\":[1]}',"
    "'h',false,NULL,'u','QUEUED',now())",
    f"insert into alarms (alarm_id, vehicle, param, state, severity, trigger_check, limits_sha256,"
    f" set_sha256, opened_at) values ('{ALARM}','V1','p','UNACK','RED','hi','l','s',now())",
    f"insert into alarm_events (key, alarm_id, vehicle, param, kind, limits_sha256, set_sha256,"
    f" note) values ('k','{ALARM}','V1','p','RAISE','l','s','')",
    f"insert into alarm_ack_request (request_id, alarm_id, vehicle, by_user, note) values"
    f" ('00000000-0000-0000-0000-0000000000d1','{ALARM}','V1','u','n')",
    "insert into events (key, kind) values ('e1','GAP')",
    "insert into seq_state values ('V1', now(), 5, now(), 0.1)",
    "insert into engine_state values ('V1', 5, now(), 's', 1, '{\"x\":null}')",
    "insert into session_report values (1, '{\"r\":1}', 'a,b')",
    "insert into archive_object (vehicle, hour, sha256, rows, verified_at, t_min, t_max)"
    " values ('V1', now(), 'x', 1, now(), now(), now())",
    "insert into audit_log (actor, action, reason) values ('u','a','')",
]


def seed(db: str) -> None:
    with psycopg.connect(owner_dsn(db)) as c:
        for stmt in SEED:
            c.execute(stmt)


def table_rows(db: str, t: str) -> list[str]:
    with psycopg.connect(owner_dsn(db)) as c:
        return sorted(r[0] for r in c.execute(f"select to_jsonb(t)::text from {t} t").fetchall())


def members(key: str) -> dict[str, bytes]:
    c = s3_client("archiver")
    obj = c.get_object("archive", key)
    try:
        data = obj.read()
    finally:
        obj.close()
        obj.release_conn()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}  # type: ignore[union-attr]


def test_snapshot_key() -> None:
    assert snapshot_key(NOW) == "state/2000/01/01/0304.tar.gz"


async def test_snapshot_roundtrip() -> None:
    with throwaway_db() as src, throwaway_db() as dst:
        seed(src)
        async with app_conn(src) as c:
            key = await take_snapshot(archiver_ctx(), c, NOW)
        assert await asyncio.to_thread(restore_main, [key, "--db", dst]) == 0
        for t in TABLES:
            assert table_rows(dst, t) == table_rows(src, t) != [], t
        with psycopg.connect(owner_dsn(dst)) as c:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                c.execute("update audit_log set actor='x'")
            c.rollback()
            new = c.execute(
                "insert into test_session (vehicle, name, conductor) values ('V2','n','c')"
                " returning id"
            ).fetchone()
        assert new is not None and new[0] > 1
        assert await asyncio.to_thread(restore_main, [key, "--db", dst]) == 2  # now non-empty


async def test_snapshot_is_one_consistent_view(monkeypatch: pytest.MonkeyPatch) -> None:
    real = snap_mod._copy_table
    calls: list[str] = []

    async def copy_then_commit_elsewhere(conn: Any, table: str) -> bytes:
        out = await real(conn, table)
        calls.append(table)
        if len(calls) == 1:  # the first read is done: another connection commits
            with psycopg.connect(owner_dsn(db)) as o:
                o.execute("insert into events (key, kind) values ('late','GAP')")
        return out

    monkeypatch.setattr(snap_mod, "_copy_table", copy_then_commit_elsewhere)
    with throwaway_db() as db:
        async with app_conn(db) as c:
            key = await take_snapshot(archiver_ctx(), c, NOW + timedelta(minutes=1))
        assert b"late" not in members(key)["events.csv"]
        assert [r for r in table_rows(db, "events")] != []  # but it did commit


async def test_killed_snapshot_leaves_no_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dead(*a: Any) -> str:
        raise OSError("minio died")

    monkeypatch.setattr(s3_mod, "put", dead)
    with throwaway_db() as db:
        async with app_conn(db) as c:
            with pytest.raises(OSError):
                await take_snapshot(archiver_ctx(), c, NOW + timedelta(minutes=2))
    key = snapshot_key(NOW + timedelta(minutes=2))
    assert list(s3_client("archiver").list_objects("archive", prefix=key)) == []


async def test_snapshot_failure_never_blocks_the_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(*a: Any) -> str:
        raise OSError("snapshot broke")

    monkeypatch.setattr(main_mod, "take_snapshot", boom)
    v, h = vid(), hour_of(datetime.now(UTC)) - timedelta(hours=30)
    with archive_db() as db:
        hot_hour(v, h, n=20, db=db)
        async with app_conn(db) as c:
            with caplog.at_level(logging.ERROR):
                res = await run_pass(archiver_ctx(), c, snapshot=True)
            assert res.archived == 1 and res.snapshot_at is None
            assert [r.message for r in caplog.records if r.levelno == logging.ERROR] == [
                "state snapshot failed"
            ]
            res = await run_pass(archiver_ctx(), c, snapshot=True)
            assert res.deleted == 1 and stored_in(v, h, db) == 0
            assert [r["hour"] for r in archive_rows(v, db)] == [h]


async def test_snapshot_refuses_existing_key(caplog: pytest.LogCaptureFixture) -> None:
    now = NOW + timedelta(minutes=3)
    with throwaway_db() as db:
        async with app_conn(db) as c:
            key = await take_snapshot(archiver_ctx(), c, now)
            first = members(key)
            seed(db)  # the second snapshot would differ
            with caplog.at_level(logging.ERROR):
                with pytest.raises(FileExistsError):
                    await take_snapshot(archiver_ctx(), c, now)
    assert members(key) == first
    assert any(r.levelno == logging.ERROR and key in r.message for r in caplog.records)
