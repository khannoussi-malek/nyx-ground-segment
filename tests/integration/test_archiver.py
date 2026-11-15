import base64
import hashlib
import io
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import psycopg
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from minio.error import S3Error

from gs.archiver import parquet as parquet_mod
from gs.archiver import s3 as s3_mod
from gs.archiver.hours import HOUR, hour_bounds, hour_of, object_key
from gs.archiver.job import archive_hour, delete_hot, merge_late
from gs.archiver.plan import DeleteHot, plan_pass
from gs.archiver.s3 import checksum_headers
from gs.common.hooks import Hooks
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archive_db,
    archive_rows,
    archiver_ctx,
    archiver_paused,
    hot_hour,
    insert_late,
    stored_in,
)
from tests.support.env import dotenv
from tests.support.env import vid as _vid
from tests.support.pg import admin_dsn, app_dsn, insert_rows, rows
from tests.support.s3 import s3_client

H = hour_of(datetime.now(UTC)) - timedelta(hours=30)
pytestmark = pytest.mark.usefixtures("archiver_paused")
_made: list[str] = []


def vid() -> str:
    v = _vid()
    _made.append(v)
    return v


def object_bytes(key: str) -> bytes:
    return s3_client("root").get_object("archive", key).read()


async def test_hour_archived_with_sidecar() -> None:
    v = vid()
    hot_hour(v, H)
    async with app_conn() as c:
        row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None and row.rows == 36_000 and row.version == 0
    data = object_bytes(object_key(v, H))
    assert hashlib.sha256(data).hexdigest() == row.sha256 == archive_rows(v)[0]["sha256"]
    assert object_bytes(object_key(v, H) + ".sha256") == f"{row.sha256}  {H:%H}.parquet\n".encode()
    meta = pq.ParquetFile(io.BytesIO(data)).metadata
    assert meta.num_row_groups == 1 and meta.row_group(0).column(0).compression == "ZSTD"
    assert set(meta.metadata or {}) <= {b"ARROW:schema"}  # no custom footer metadata (D-ARC-01)
    assert stored_in(v, H) == 36_000  # archiving never deletes hot rows


async def test_archived_rows_equal_the_hot_rows() -> None:
    v = vid()
    hot_hour(v, H, n=500)
    async with app_conn() as c:
        row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None
    t = pq.read_table(io.BytesIO(object_bytes(object_key(v, H))))
    hot = rows("select * from telemetry where vehicle=%s order by t_onboard, seq", v)
    assert t.num_rows == len(hot) == 500
    assert t.column("seq").to_pylist() == [r["seq"] for r in hot]
    assert t.column("cabin_pressure_kpa").to_pylist() == [r["cabin_pressure_kpa"] for r in hot]
    assert t.column("t_received").to_pylist() == [r["t_received"] for r in hot]
    assert t.column("last_cmd_id").to_pylist() == [None] * 500
    assert t.schema.field("t_onboard").type == pa.timestamp("us", tz="UTC")


async def test_window_is_half_open() -> None:
    v = vid()
    one_us = timedelta(microseconds=1)
    insert_rows(v, [{"t_onboard": t, "seq": i} for i, t in enumerate(
        [H - one_us, H, H + timedelta(hours=1) - one_us, H + timedelta(hours=1)]
    )])  # fmt: skip
    async with app_conn() as c:
        row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None and row.rows == 2
    assert (row.t_min, row.t_max) == (H, H + timedelta(hours=1) - one_us)


async def test_t_min_t_max_stored() -> None:
    v = vid()
    hot_hour(v, H, n=100, rate_hz=1.0)
    async with app_conn() as c:
        row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None
    got = rows("select min(t_onboard) lo, max(t_onboard) hi from telemetry where vehicle=%s", v)[0]
    stored = archive_rows(v)[0]
    assert (stored["t_min"], stored["t_max"]) == (got["lo"], got["hi"]) == (row.t_min, row.t_max)
    assert row.verified_at is not None and stored["last_checked_at"] is None
    assert stored["hot_deleted_at"] is None


async def test_empty_hour_returns_none() -> None:
    v = vid()
    async with app_conn() as c:
        assert await archive_hour(archiver_ctx(), c, v, H) is None
    assert archive_rows(v) == []
    with pytest.raises(S3Error):
        object_bytes(object_key(v, H))  # nothing was uploaded


async def test_versions_get_their_own_key_and_the_old_object_stays() -> None:
    v = vid()
    hot_hour(v, H, n=50)
    async with app_conn() as c:
        first = await archive_hour(archiver_ctx(), c, v, H)
        v1 = await archive_hour(archiver_ctx(), c, v, H, version=1)
    assert first is not None and v1 is not None
    stored = archive_rows(v)
    assert len(stored) == 1 and stored[0]["version"] == 1  # one row names the latest version
    assert object_bytes(object_key(v, H, 1)).startswith(b"PAR1")
    assert object_bytes(object_key(v, H)).startswith(b"PAR1")  # the old object is not removed


async def test_encoding_is_deterministic() -> None:
    v = vid()
    hot_hour(v, H, n=50)
    async with app_conn() as c:
        first = await archive_hour(archiver_ctx(), c, v, H)
        await c.execute("select 1")
    assert first is not None
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
        a.execute("delete from archive_object where vehicle=%s", (v,))
    async with app_conn() as c:
        again = await archive_hour(archiver_ctx(), c, v, H)
    assert again is not None and again.sha256 == first.sha256


async def test_an_existing_row_is_never_overwritten_by_the_same_or_older_version() -> None:
    v = vid()
    hot_hour(v, H, n=50)
    async with app_conn() as c:
        first = await archive_hour(archiver_ctx(), c, v, H)
        assert first is not None
        before = object_bytes(object_key(v, H))
        late_row(v, H + timedelta(seconds=1, milliseconds=5), 999)
        assert await archive_hour(archiver_ctx(), c, v, H) is None  # version 0 again
        v1 = await archive_hour(archiver_ctx(), c, v, H, version=1)
        assert v1 is not None
        assert await archive_hour(archiver_ctx(), c, v, H, version=1) is None  # same version
        assert await archive_hour(archiver_ctx(), c, v, H, version=0) is None  # older version
    assert object_bytes(object_key(v, H)) == before  # the recorded copy is untouched
    assert archive_rows(v)[0]["version"] == 1 and archive_rows(v)[0]["sha256"] == v1.sha256


async def test_a_hot_deleted_hour_is_never_rebuilt_from_its_late_rows() -> None:
    """After the hot delete the object is the only copy: archive_hour over just the late rows
    would overwrite it with them."""
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        before = object_bytes(object_key(v, H))
        late_row(v, H + timedelta(seconds=5, milliseconds=5), 5000)
        for version in (0, 1, 2):
            assert await archive_hour(ctx, c, v, H, version=version) is None
    assert object_bytes(object_key(v, H)) == before and hot_n(v) == 1
    with pytest.raises(S3Error):
        object_bytes(object_key(v, H, 1))  # nothing was uploaded


def _tamper_value(real: Any) -> Any:
    def encode(t: pa.Table) -> bytes:
        i = t.schema.get_field_index("cabin_pressure_kpa")
        vals = t.column(i).to_pylist()
        vals[3] = (vals[3] or 0.0) + 0.5
        out: bytes = real(t.set_column(i, "cabin_pressure_kpa", pa.array(vals, pa.float64())))
        return out

    return encode


async def test_verify_compares_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    v = vid()
    hot_hour(v, H, n=200)
    monkeypatch.setattr("gs.archiver.job.encode", _tamper_value(parquet_mod.encode))
    with caplog.at_level(logging.ERROR):
        async with app_conn() as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is None
    assert archive_rows(v) == [] and stored_in(v, H) == 200
    assert [r.vehicle for r in caplog.records if r.levelno == logging.ERROR] == [v]  # type: ignore[attr-defined]


async def test_verify_compares_bytes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    v = vid()
    hot_hour(v, H, n=50)
    real_get = s3_mod.get

    async def corrupt(*a: Any) -> bytes:
        return await real_get(*a) + b"\0"

    monkeypatch.setattr("gs.archiver.job.get", corrupt)
    with caplog.at_level(logging.ERROR):
        async with app_conn() as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is None
    assert archive_rows(v) == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


async def test_hot_rows_changing_during_the_pass_write_no_row(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    v = vid()
    hot_hour(v, H, n=50)
    real_put = s3_mod.put

    async def late_frame(*a: Any) -> str:
        digest = await real_put(*a)
        insert_rows(v, [{"t_onboard": H + timedelta(minutes=30), "seq": 999}])  # lands mid-pass
        return digest

    monkeypatch.setattr("gs.archiver.job.put", late_frame)
    with caplog.at_level(logging.ERROR):
        async with app_conn() as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is None
    assert archive_rows(v) == []  # the object no longer matches the hot rows: not verified


@pytest.mark.parametrize("zone", ["Asia/Tokyo", "Asia/Kolkata", "America/St_Johns"])
async def test_hour_bounds_ignore_session_timezone(zone: str) -> None:
    v = vid()
    hot_hour(v, H, n=300)
    async with app_conn() as c:
        utc = await archive_hour(archiver_ctx(), c, v, H)
    assert utc is not None
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
        a.execute("delete from archive_object where vehicle=%s", (v,))  # so the zone run may write
    async with app_conn() as c:
        await c.execute(f"SET TIME ZONE '{zone}'")  # noqa: S608 (a fixed test string)
        local = await archive_hour(archiver_ctx(), c, v, H)
    assert local is not None
    assert (local.rows, local.t_min, local.t_max, local.sha256) == (
        utc.rows, utc.t_min, utc.t_max, utc.sha256,
    )  # fmt: skip
    assert local.rows == 300


def _raise_exit(code: int) -> NoReturn:
    raise SystemExit(code)


@pytest.mark.parametrize("point", ["archive:after_put", "archive:after_reread"])
async def test_crash_leaves_no_row_and_a_rerun_writes_it(point: str, tmp_path: Path) -> None:
    v = vid()
    hot_hour(v, H, n=100)
    hooks = Hooks(crash_at=point, marker_dir=tmp_path, exit=_raise_exit)
    async with app_conn() as c:
        with pytest.raises(SystemExit):
            await archive_hour(archiver_ctx(hooks=hooks), c, v, H)
        assert archive_rows(v) == []
        if point == "archive:after_put":
            assert object_bytes(object_key(v, H)).startswith(b"PAR1")  # uploaded, not yet verified
        row = await archive_hour(archiver_ctx(hooks=hooks), c, v, H)  # the marker: fires once
    assert row is not None and row.rows == 100
    assert hashlib.sha256(object_bytes(object_key(v, H))).hexdigest() == row.sha256


async def test_sidecar_written_after_the_reread_hook(tmp_path: Path) -> None:
    v = vid()
    hot_hour(v, H, n=20)
    hooks = Hooks(crash_at="archive:after_reread", marker_dir=tmp_path, exit=_raise_exit)
    async with app_conn() as c:
        with pytest.raises(SystemExit):
            await archive_hour(archiver_ctx(hooks=hooks), c, v, H)
    with pytest.raises(S3Error):
        object_bytes(object_key(v, H) + ".sha256")  # no sidecar before the reread passed


def test_put_sends_the_checksum_header_and_the_server_enforces_it() -> None:
    body = b"hello archive"
    key = f"t/{vid()}.bin"
    good = hashlib.sha256(body).digest()
    assert checksum_headers(good) == {"x-amz-checksum-sha256": base64.b64encode(good).decode()}
    root, c = s3_client("root"), s3_client("archiver")
    c.put_object("archive", key, io.BytesIO(body), len(body), metadata=checksum_headers(good))  # type: ignore[arg-type]
    try:
        bad = checksum_headers(hashlib.sha256(b"other").digest())
        with pytest.raises(S3Error) as e:
            c.put_object("archive", key + ".bad", io.BytesIO(body), len(body), metadata=bad)  # type: ignore[arg-type]
        assert e.value.code in {"BadDigest", "XAmzContentChecksumMismatch"}
    finally:
        root.remove_object("archive", key)


async def test_second_connection_sees_the_same_rows_in_another_timezone() -> None:
    v = vid()
    hot_hour(v, H, n=10)
    with psycopg.connect(app_dsn("gs"), options="-c timezone=Asia/Tokyo") as c:
        got = c.execute("select min(t_onboard) from telemetry where vehicle=%s", (v,)).fetchone()
    assert got is not None and got[0] == H  # same instant, a different zone


# ---- Task 4: hot delete and versioned late objects --------------------------------------------
def late_row(v: str, t: datetime, seq: int) -> None:
    insert_rows(v, [{"t_onboard": t, "seq": seq}])


def hot_n(v: str) -> int:
    return int(rows("select count(*) n from telemetry where vehicle=%s", v)[0]["n"])


async def test_delete_only_after_verify() -> None:
    v = vid()
    hot_hour(v, H)
    now = H + HOUR + timedelta(hours=30)
    assert not [a for a in plan_pass(now, {(v, H): 36_000}, {}, 0.0) if isinstance(a, DeleteHot)]
    ctx = archiver_ctx(retention_h=0.0)
    assert stored_in(v, H) == 36_000  # nothing planned, nothing deleted
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is True
    assert stored_in(v, H) == 0 and archive_rows(v)[0]["hot_deleted_at"] is not None
    assert (v, H) not in ctx.refused


async def test_row_count_mismatch_rolls_back() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        late_row(v, H + timedelta(minutes=30), 9999)  # one more row inside the hour
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is False
    assert stored_in(v, H) == 301 and (v, H) in ctx.refused
    assert archive_rows(v)[0]["hot_deleted_at"] is None


async def test_range_mismatch_rolls_back() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        await c.execute("delete from telemetry where vehicle=%s and seq=0", (v,))  # the first row
        late_row(v, H + timedelta(minutes=59), 7777)  # same count, a later max
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is False
    assert stored_in(v, H) == 300


async def test_delete_wrong_hour_refused() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    hot_hour(v, H + HOUR, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        assert not await delete_hot(ctx, c, row, hour_bounds(v, H + HOUR))  # same counts, not range
    assert stored_in(v, H + HOUR) == 300 and stored_in(v, H) == 300 and (v, H) in ctx.refused


async def test_delete_other_vehicle_refused() -> None:
    a, b = vid(), vid()
    hot_hour(a, H, n=300)
    hot_hour(b, H, n=300)  # the very same (t_onboard, seq) set
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, a, H)
        assert row is not None
        assert not await delete_hot(ctx, c, row, hour_bounds(b, H))
    assert stored_in(b, H) == 300


@pytest.mark.parametrize("same_seq", [False, True])
async def test_swapped_row_is_refused_by_the_seq_fingerprint(same_seq: bool) -> None:
    """One archived row lost outside the archiver, one late row in range: count, min and max still
    equal the archive row, yet a row that was never archived would be deleted."""
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        lost = rows("select t_onboard from telemetry where vehicle=%s and seq=150", v)[0]
        await c.execute("delete from telemetry where vehicle=%s and seq=150", (v,))
        late_row(v, lost["t_onboard"] + timedelta(milliseconds=3), 150 if same_seq else 5150)
        got = rows(
            "select count(*) n, min(t_onboard) lo, max(t_onboard) hi from telemetry"
            " where vehicle=%s",
            v,
        )[0]
        assert (got["n"], got["lo"], got["hi"]) == (row.rows, row.t_min, row.t_max)  # the trap
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is False
    assert stored_in(v, H) == 300 and (v, H) in ctx.refused
    assert archive_rows(v)[0]["hot_deleted_at"] is None


async def test_delete_refused_when_the_archived_object_is_not_the_recorded_one() -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        junk = b"not the archive"
        s3_client("root").put_object("archive", object_key(v, H), io.BytesIO(junk), len(junk))
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is False
    assert stored_in(v, H) == 100


async def test_refused_cleared_by_later_delete() -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        await c.execute("delete from telemetry where vehicle=%s and seq=3", (v,))
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is False
        assert (v, H) in ctx.refused
        hot_hour_rows = rows("select 1 from telemetry where vehicle=%s limit 1", v)
        assert hot_hour_rows
        # the hot row comes back (a re-ingest) and the hour then deletes
        await c.execute(
            "insert into telemetry (t_onboard, vehicle, seq, mode) select %s, %s, 3, 'TEST'",
            (H + timedelta(seconds=0.3), v),
        )
        # (t_received and parameter columns differ from the original: the archived KEY set equals)
        assert await delete_hot(ctx, c, row, hour_bounds(v, H)) is True
    assert (v, H) not in ctx.refused and stored_in(v, H) == 0


async def test_late_frame_before_delete_gives_v1() -> None:
    v = vid()
    hot_hour(v, H, n=36_000)
    ctx = archiver_ctx()
    async with app_conn() as c:
        v0 = await archive_hour(ctx, c, v, H)
        assert v0 is not None
        late_row(v, H + timedelta(seconds=7, milliseconds=5), 36_000)
        v1 = await archive_hour(ctx, c, v, H, version=1)
        assert v1 is not None and v1.version == 1 and v1.rows == 36_001
        assert pq.read_table(io.BytesIO(object_bytes(object_key(v, H, 1)))).num_rows == 36_001
        assert pq.read_table(io.BytesIO(object_bytes(object_key(v, H)))).num_rows == 36_000
        assert await delete_hot(ctx, c, v1, hour_bounds(v, H)) is True
    assert stored_in(v, H) == 0 and archive_rows(v)[0]["version"] == 1


async def test_v1_needs_a_superset_of_the_old_version() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        v0 = await archive_hour(ctx, c, v, H)
        assert v0 is not None
        await c.execute("delete from telemetry where vehicle=%s and seq=0", (v,))  # min moves up
        assert await archive_hour(ctx, c, v, H, version=1) is None
    assert archive_rows(v)[0]["version"] == 0  # the old version stays the recorded one


async def test_v1_refused_when_the_latest_row_is_gone() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        assert await archive_hour(ctx, c, v, H) is not None
        await c.execute(
            "delete from telemetry where vehicle=%s and seq=299", (v,)
        )  # max moves down
        late_row(v, H + timedelta(seconds=29, milliseconds=990), 8889)
        assert await archive_hour(ctx, c, v, H, version=1) is None
    assert archive_rows(v)[0]["version"] == 0


async def test_v1_swap_inside_the_range_is_refused() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    ctx = archiver_ctx()
    async with app_conn() as c:
        assert await archive_hour(ctx, c, v, H) is not None
        await c.execute("delete from telemetry where vehicle=%s and seq=150", (v,))  # lost outside
        late_row(v, H + timedelta(seconds=15, milliseconds=3), 5150)  # same count, same min/max
        assert await archive_hour(ctx, c, v, H, version=1) is None
    assert archive_rows(v)[0]["version"] == 0


async def test_late_frame_after_delete_merged() -> None:
    v = vid()
    hot_hour(v, H, n=36_000)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        row = archive_row(v)
        late_row(v, H + timedelta(seconds=5, milliseconds=5), 40_000)
        late_row(v, H + timedelta(minutes=59, seconds=59, milliseconds=999), 40_001)
        merged = await merge_late(ctx, c, row)
    assert merged is not None and merged.version == 1 and merged.rows == 36_002
    t = pq.read_table(io.BytesIO(object_bytes(object_key(v, H, 1))))
    assert t.num_rows == 36_002 and {40_000, 40_001} <= set(t.column("seq").to_pylist())
    assert hot_n(v) == 0 and archive_rows(v)[0]["version"] == 1
    assert hashlib.sha256(object_bytes(object_key(v, H, 1))).hexdigest() == merged.sha256
    assert object_bytes(object_key(v, H, 1) + ".sha256").startswith(merged.sha256.encode())
    assert merged.t_max == H + timedelta(minutes=59, seconds=59, milliseconds=999)


async def test_merge_deduplicates_a_row_that_is_in_both() -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        row = archive_row(v)
        late_row(v, H + timedelta(seconds=0.5), 5)  # the same (t_onboard, seq) as archived row 5
        merged = await merge_late(ctx, c, row)
    assert merged is not None and merged.rows == 100 and hot_n(v) == 0


def archive_row(v: str, db: str = "gs") -> Any:
    from gs.archiver.plan import ArchiveRow

    return ArchiveRow(*rows(
        "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
        " last_checked_at from archive_object where vehicle=%s", v, db=db,
    )[0].values())  # fmt: skip


async def test_merge_refused_when_prev_corrupt() -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        row = archive_row(v)
        late_row(v, H + timedelta(seconds=5, milliseconds=5), 5000)
        junk = b"overwritten"
        s3_client("root").put_object("archive", object_key(v, H), io.BytesIO(junk), len(junk))
        assert await merge_late(ctx, c, row) is None
    assert hot_n(v) == 1 and archive_rows(v)[0]["version"] == 0  # the late row stays hot


async def test_merge_rolls_back_when_the_late_rows_change_before_the_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        row = archive_row(v)
        late_row(v, H + timedelta(seconds=5, milliseconds=5), 5000)
        real_put = s3_mod.put
        calls = 0

        async def another_late_frame(*a: Any) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:  # the sidecar: the object is already verified
                late_row(v, H + timedelta(seconds=6, milliseconds=5), 5001)
            return await real_put(*a)

        monkeypatch.setattr("gs.archiver.job.put", another_late_frame)
        assert (
            await merge_late(ctx, c, row) is None
        )  # the delete would take a row not in the object
    assert hot_n(v) == 2 and archive_rows(v)[0]["version"] == 0 and (v, H) in ctx.refused
    async with app_conn() as c:  # the next pass builds the version again from both late rows
        monkeypatch.undo()
        again = await merge_late(archiver_ctx(), c, archive_row(v))
    assert again is not None and again.rows == 102 and again.version == 1 and hot_n(v) == 0


async def test_put_itself_sends_the_checksum_header(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Break the digest put() sends: the server must refuse the body, so no object and no row."""
    wrong = {"x-amz-checksum-sha256": base64.b64encode(hashlib.sha256(b"other").digest()).decode()}
    monkeypatch.setattr(s3_mod, "checksum_headers", lambda digest: wrong)
    key = f"t/{vid()}.bin"
    with pytest.raises(S3Error) as e:
        await s3_mod.put(s3_client("archiver"), "archive", key, b"the real body")
    assert e.value.code in {"BadDigest", "XAmzContentChecksumMismatch"}
    with pytest.raises(S3Error) as missing:
        s3_client("root").stat_object("archive", key)
    assert missing.value.code == "NoSuchKey"
    v = vid()
    hot_hour(v, H, n=20)
    monkeypatch.setattr("gs.archiver.job.put", s3_mod.put)
    async with app_conn() as c:
        with pytest.raises(S3Error):
            await archive_hour(archiver_ctx(), c, v, H)
    assert archive_rows(v) == []
    with pytest.raises(S3Error):
        object_bytes(object_key(v, H))


# ---- Task 5: rolling scrub, run_pass ------------------------------------------------------
# Passes act on every vehicle, so everything that runs one (or scrubs globally) uses a throwaway db.


def _flip(key: str) -> None:
    root = s3_client("root")
    data = bytearray(object_bytes(key))
    data[len(data) // 2] ^= 0xFF
    root.put_object("archive", key, io.BytesIO(bytes(data)), len(data))


def _damage(key: str, how: str) -> None:
    if how == "flip":
        _flip(key)
    else:
        s3_client("root").remove_object("archive", key)


def _drop_vehicle(v: str) -> None:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for t in ("events", "archive_object", "telemetry"):
            c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
    root = s3_client("root")
    for o in root.list_objects("archive", prefix=f"{v}/", recursive=True):
        root.remove_object("archive", o.object_name)


@pytest.fixture(autouse=True)
def _cleanup() -> Any:
    """G4-3: whatever a test leaves in the shared db or bucket goes, pass or fail."""
    yield
    for v in _made:
        _drop_vehicle(v)
    _made.clear()


async def test_scrub_detects_corrupt_object() -> None:
    from gs.archiver.job import corrupt_key
    from gs.archiver.scrub import scrub_pass, unresolved_corrupt
    from gs.common.time import iso_z

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=200, db=db)
        async with app_conn(db) as c:
            row = await archive_hour(archiver_ctx(), c, v, H)
            assert row is not None
            _flip(object_key(v, H))
            assert await scrub_pass(archiver_ctx(), c) == 1
            ev = rows("select * from events where vehicle=%s and kind='ARCHIVE_CORRUPT'", v, db=db)
            want = f"ARCHIVE_CORRUPT/{v}/{iso_z(H)}"
            assert [e["key"] for e in ev] == [corrupt_key(v, H, 0)] == [want]
            found = hashlib.sha256(object_bytes(object_key(v, H))).hexdigest()
            assert (ev[0]["details"]["expected"], ev[0]["details"]["found"]) == (row.sha256, found)
            assert await unresolved_corrupt(c, v) == 1
            await scrub_pass(archiver_ctx(), c)  # idempotent by key
            assert await unresolved_corrupt(c, v) == 1


async def test_scrub_missing_object_is_corrupt_and_s3_down_raises() -> None:
    from gs.archiver.scrub import scrub_pass, unresolved_corrupt

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=50, db=db)
        async with app_conn(db) as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is not None
            s3_client("root").remove_object("archive", object_key(v, H))
            with pytest.raises(s3_mod.S3_ERRORS):
                await scrub_pass(_dead_ctx(), c)
            assert archive_rows(v, db)[0]["last_checked_at"] is None
            await scrub_pass(archiver_ctx(), c)
            assert await unresolved_corrupt(c, v) == 1
            assert archive_rows(v, db)[0]["last_checked_at"] is not None


RECENT = "select hour from archive_object where last_checked_at > now() - interval '1 minute'"


async def test_scrub_oldest_checked_first() -> None:
    from gs.archiver.scrub import SCRUB_K, scrub_pass
    from tests.support.pg import throwaway_db

    assert SCRUB_K == 20
    hours = [H - i * HOUR for i in range(25)]
    with throwaway_db() as db:
        with psycopg.connect(admin_dsn(db), autocommit=True) as a:
            for i, h in enumerate(hours):
                when = "now() - interval '1 hour'" if i < 5 else "null"
                a.execute(
                    "insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max,"
                    f" verified_at, last_checked_at) values ('NYX-TSCRUB', %s, 'x', 1, %s, %s,"
                    f" now(), {when})",  # noqa: S608
                    (h, h, h),
                )
        async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
            assert await scrub_pass(archiver_ctx(), c, 20) == 20  # the 20 NULL rows, not the 5
            assert {r["hour"] for r in rows(RECENT, db=db)} == set(hours[5:])
            assert await scrub_pass(archiver_ctx(), c, 5) == 5  # now the 5 oldest-checked
            assert {r["hour"] for r in rows(RECENT, db=db)} == set(hours)


async def test_new_version_resolves_corrupt() -> None:
    from gs.archiver.scrub import scrub_pass, unresolved_corrupt

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=100, db=db)
        async with app_conn(db) as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is not None
            _flip(object_key(v, H))
            await scrub_pass(archiver_ctx(), c)
            assert await unresolved_corrupt(c, v) == 1
            insert_late(v, [{"t_onboard": H + timedelta(minutes=30), "seq": 5000}], db)
            row = await archive_hour(archiver_ctx(), c, v, H, version=1)
            assert row is not None and row.version == 1 and row.rows == 101
            assert await unresolved_corrupt(c, v) == 0
            await scrub_pass(archiver_ctx(), c)
            assert await unresolved_corrupt(c, v) == 0


def _dead_ctx() -> Any:
    import urllib3
    from minio import Minio

    return archiver_ctx(
        s3=Minio("127.0.0.1:1", "a", "b", secure=False, region="us-east-1",
                 http_client=urllib3.PoolManager(retries=False, timeout=1))
    )  # fmt: skip


async def test_minio_down_pass_deletes_nothing() -> None:
    from gs.archiver.main import run_pass

    v = vid()
    old, older, oldest = H, H - HOUR, H - 2 * HOUR
    with archive_db() as db:
        for h in (old, older, oldest):
            hot_hour(v, h, n=20, db=db)
        async with app_conn(db) as c:
            assert await archive_hour(archiver_ctx(), c, v, old) is not None  # verified, delete due
            res = await run_pass(_dead_ctx(), c)
            assert res.failed and res.deleted == 0
            assert [stored_in(v, h, db) for h in (old, older, oldest)] == [20, 20, 20]
            assert [r["hour"] for r in archive_rows(v, db)] == [old]
            res = await run_pass(archiver_ctx(retention_h=1000.0), c)  # nothing delete-due
            assert not res.failed
            done = {r["hour"]: r["verified_at"] for r in archive_rows(v, db)}
            assert set(done) == {old, older, oldest}
            assert done[oldest] < done[older]  # the oldest due hour was built first


async def test_run_pass_end_to_end() -> None:
    from gs.archiver.main import run_pass

    v = vid()
    h1, h2 = H - 2 * HOUR, H - HOUR
    with archive_db() as db:
        hot_hour(v, h1, n=30, db=db)
        hot_hour(v, h2, n=30, db=db)
        async with app_conn(db) as c:
            ctx = archiver_ctx(retention_h=0.0)
            first = await run_pass(ctx, c)  # archives; the delete is planned from the row next pass
            assert not first.failed and first.archived == 2 and stored_in(v, h1, db) == 30
            res = await run_pass(ctx, c)
            assert not res.failed and res.deleted == 2 and res.scrubbed == 2
            assert (stored_in(v, h1, db), stored_in(v, h2, db)) == (0, 0)
            rs = archive_rows(v, db)
            assert [r["hour"] for r in rs] == [h1, h2]
            assert all(r["hot_deleted_at"] and r["last_checked_at"] for r in rs)
            insert_late(v, [{"t_onboard": h1 + timedelta(minutes=1), "seq": 9000}], db)
            third = await run_pass(ctx, c)  # late after the delete: merged
            assert third.merged == 1 and third.oldest_unarchived is None
            assert archive_rows(v, db)[0]["version"] == 1 and stored_in(v, h1, db) == 0


@pytest.mark.parametrize("how", ["flip", "remove"])
async def test_corrupt_hour_with_all_rows_hot_is_rebuilt_then_deleted(how: str) -> None:
    from gs.archiver.main import run_pass
    from gs.archiver.scrub import unresolved_corrupt

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=100, db=db)
        async with app_conn(db) as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is not None
            _damage(object_key(v, H), how)
            ctx = archiver_ctx(retention_h=0.0)
            res = await run_pass(ctx, c)  # the scrub flags it, the planner builds version 1
            assert res.archived == 1 and res.deleted == 0
            assert archive_rows(v, db)[0]["version"] == 1 and stored_in(v, H, db) == 100
            assert await unresolved_corrupt(c, v) == 0
            assert (await run_pass(ctx, c)).deleted == 1 and stored_in(v, H, db) == 0


async def test_corrupt_hour_already_hot_deleted_stays_open_and_is_left_alone() -> None:
    from gs.archiver.main import run_pass
    from gs.archiver.scrub import unresolved_corrupt

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=100, db=db)
        async with app_conn(db) as c:
            row = await archive_hour(archiver_ctx(), c, v, H)
            assert row is not None and await delete_hot(archiver_ctx(), c, row, hour_bounds(v, H))
            _flip(object_key(v, H))
            insert_late(v, [{"t_onboard": H + timedelta(minutes=1), "seq": 9000}], db)
            res = await run_pass(archiver_ctx(retention_h=0.0), c)
            assert (res.archived, res.merged, res.deleted) == (0, 0, 0)
            assert archive_rows(v, db)[0]["version"] == 0 and stored_in(v, H, db) == 1
            assert await unresolved_corrupt(c, v) == 1


def _lose_min(v: str, db: str) -> str:
    insert_late(v, [{"t_onboard": H + timedelta(milliseconds=1), "seq": 8888}], db)
    return "delete from telemetry where vehicle=%s and seq=0"  # min moves up, count equal


def _lose_max(v: str, db: str) -> str:
    insert_late(v, [{"t_onboard": H + timedelta(seconds=5, milliseconds=50), "seq": 8889}], db)
    return "delete from telemetry where vehicle=%s and seq=99"  # max moves down, count equal


def _lose_count(v: str, db: str) -> str:
    return "delete from telemetry where vehicle=%s and seq=50"  # range intact, one row fewer


@pytest.mark.parametrize("lose", [_lose_min, _lose_max, _lose_count], ids=["min", "max", "count"])
async def test_heal_refused_when_the_hot_rows_lost_the_old_range(lose: Any) -> None:
    """The corrupt object cannot prove the new version drops nothing, but the row can: count,
    first and last row time must all still be covered."""
    from gs.archiver.scrub import scrub_pass, unresolved_corrupt

    v = vid()
    with archive_db() as db:
        hot_hour(v, H, n=100, db=db)  # 10 Hz: seq 99 is the last, at H + 9.9 s
        async with app_conn(db) as c:
            assert await archive_hour(archiver_ctx(), c, v, H) is not None
            _flip(object_key(v, H))
            await scrub_pass(archiver_ctx(), c)
            await c.execute(lose(v, db), (v,))
            assert await archive_hour(archiver_ctx(), c, v, H, version=1) is None
            assert archive_rows(v, db)[0]["version"] == 0
            assert await unresolved_corrupt(c, v) == 1


async def test_hash_failures_in_delete_and_replace_write_the_event() -> None:
    from gs.archiver.job import corrupt_key
    from gs.archiver.scrub import unresolved_corrupt

    v, w = vid(), vid()
    with archive_db() as db:
        for x in (v, w):
            hot_hour(x, H, n=20, db=db)
        async with app_conn(db) as c:
            ctx = archiver_ctx()
            row = await archive_hour(ctx, c, v, H)
            assert row is not None
            _flip(object_key(v, H))
            assert not await delete_hot(ctx, c, row, hour_bounds(v, H))
            assert await unresolved_corrupt(c, v) == 1  # on record without waiting for the scrub
            assert rows("select key from events where vehicle=%s", v, db=db) == [
                {"key": corrupt_key(v, H, 0)}
            ]
            assert await archive_hour(ctx, c, w, H) is not None
            _flip(object_key(w, H))
            insert_late(w, [{"t_onboard": H + timedelta(minutes=1), "seq": 9}], db)
            assert await archive_hour(ctx, c, w, H, version=1) is not None  # healed
            assert rows("select key from events where vehicle=%s", w, db=db)  # and recorded


async def test_delete_and_merge_need_an_autocommit_connection() -> None:
    """On a non-autocommit connection conn.transaction() is a savepoint nobody commits."""
    v = vid()
    hot_hour(v, H, n=10)
    async with app_conn() as c:
        row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as plain:
        with pytest.raises(AssertionError, match="autocommit"):
            await delete_hot(archiver_ctx(), plain, row, hour_bounds(v, H))
        with pytest.raises(AssertionError, match="autocommit"):
            await merge_late(archiver_ctx(), plain, row)
    assert stored_in(v, H) == 10


# ---- Task 5: the service around the pass ----------------------------------------------------


def _settings(loop_s: float = 5.0) -> Any:
    from gs.archiver.main import ArchiverSettings
    from gs.archiver.s3 import S3Settings

    env = dotenv()
    redis_url = f"redis://health:{env['REDIS_HEALTH_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0"
    s3 = S3Settings(f"127.0.0.1:{env['MINIO_HOST_PORT']}", "archiver", env["S3_ARCHIVER_SECRET"],
                    "archive")  # fmt: skip
    return ArchiverSettings(app_dsn("gs"), redis_url, s3, "127.0.0.1", 0, 25.0, loop_s)


async def test_action_error_is_logged_with_vehicle_and_hour_then_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from gs.archiver import main
    from gs.common.time import iso_z

    v = vid()

    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "archive_hour", boom)
    with archive_db() as db:
        hot_hour(v, H, n=10, db=db)
        async with app_conn(db) as c:
            with pytest.raises(RuntimeError, match="boom"):
                await main.run_pass(archiver_ctx(), c)
    rec = [
        r for r in caplog.records if r.levelno >= logging.ERROR and r.__dict__.get("vehicle") == v
    ]
    assert rec and rec[0].__dict__["hour"] == iso_z(H)


@pytest.mark.parametrize("outcome", ["raises", "ok"])
async def test_run_survives_failures_and_backs_off(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    import asyncio

    from gs.archiver import main

    waits: list[float] = []

    async def fake_sleep(stop: asyncio.Event, seconds: float) -> None:
        if seconds == 777:
            await stop.wait()  # the health refresher idles
            return
        waits.append(seconds)
        if len(waits) >= 4:
            stop.set()

    async def bad_pass(*a: Any, **k: Any) -> Any:
        raise RuntimeError("not S3, not DB")

    async def good_pass(*a: Any, **k: Any) -> Any:
        return main.PassResult()

    async def bad_probe(*a: Any, **k: Any) -> Any:
        raise RuntimeError("probe")

    monkeypatch.setattr(main, "HEALTH_REFRESH_S", 777.0)
    monkeypatch.setattr(main, "_sleep", fake_sleep)
    monkeypatch.setattr(main, "run_pass", bad_pass if outcome == "raises" else good_pass)
    monkeypatch.setattr(main, "_probe", bad_probe)  # even the first probe may fail
    await asyncio.wait_for(main.run(_settings(5.0), asyncio.Event()), 20)
    assert waits == ([1, 2, 4, 5] if outcome == "raises" else [5, 5, 5, 5])


async def test_refresher_outlives_a_failing_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from gs.archiver import main

    calls: list[int] = []

    async def probe() -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("probe")
        return main.Snapshot(True, True, True, 3)

    async def fake_sleep(stop: asyncio.Event, seconds: float) -> None:
        if len(calls) >= 2:
            stop.set()

    monkeypatch.setattr(main, "_sleep", fake_sleep)
    box = main.Box(main.Snapshot())
    await main._refresh(box, probe, asyncio.Event())
    assert (box.snap.db_ok, box.snap.corrupt) == (True, 3)


async def test_probe_reports_the_db_within_the_timeout_when_minio_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import redis.asyncio

    from gs.archiver import main

    class Hang:
        def list_objects(self, *a: Any, **k: Any) -> list[Any]:
            time.sleep(2)
            return []

    monkeypatch.setattr(main, "PROBE_TIMEOUT_S", 0.5)
    s = _settings()
    r = redis.asyncio.Redis.from_url(s.redis_url)
    t0 = time.monotonic()
    snap = await main._probe(s, archiver_ctx(s3=Hang()), r)
    await r.aclose()
    assert time.monotonic() - t0 < 1.5
    assert snap.db_ok and snap.redis_ok and not snap.s3_ok


async def test_connection_is_utc_autocommit_whatever_the_server_default() -> None:
    from gs.archiver.main import _connect

    async with await _connect(app_dsn("gs") + "?options=-c%20timezone%3DAsia/Tokyo") as c:
        cur = await c.execute("show timezone")
        assert (await cur.fetchone()) == ("UTC",) and c.autocommit


def test_s3_client_has_bounded_timeouts() -> None:
    from gs.archiver.s3 import client

    t = client(_settings().s3)._http.connection_pool_kw["timeout"]
    assert 0 < t.connect_timeout <= 5 and 0 < t.read_timeout <= 30


# ---- Task 4 fix round 1 -------------------------------------------------------------------

COL = "g4_added_col"  # test-only parameter column, dropped again


async def test_merge_late_keeps_a_column_added_after_v0() -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
        try:
            async with app_conn() as c:
                row = await archive_hour(ctx, c, v, H)
                assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
                a.execute(f"alter table telemetry add column if not exists {COL} double precision")
                insert_rows(v, [{"t_onboard": H + timedelta(minutes=30), "seq": 7000, COL: 42.5}])
                merged = await merge_late(ctx, c, archive_row(v))
            assert merged is not None and merged.version == 1 and hot_n(v) == 0
            t = pq.read_table(io.BytesIO(object_bytes(object_key(v, H, 1))))
            got = t.filter(pc.equal(t["seq"], 7000))[COL].to_pylist()
            assert got == [42.5]
        finally:
            a.execute(f"alter table telemetry drop column if exists {COL}")
    _drop_vehicle(v)


@pytest.mark.parametrize("change", ["sha256 = repeat('f', 64)", "version = 5"])
async def test_delete_hot_rolls_back_when_the_archive_row_changed(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from gs.archiver import job

    v = vid()
    hot_hour(v, H, n=50)
    ctx = archiver_ctx()
    real = job._recorded_table

    async def racing(*a: Any) -> Any:
        t = await real(*a)
        with psycopg.connect(admin_dsn("gs"), autocommit=True) as adm:  # changes after the check
            adm.execute(f"update archive_object set {change} where vehicle = %s", (v,))  # noqa: S608
        return t

    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None
        monkeypatch.setattr(job, "_recorded_table", racing)
        assert not await delete_hot(ctx, c, row, hour_bounds(v, H))
    assert stored_in(v, H) == 50 and archive_rows(v)[0]["hot_deleted_at"] is None
    assert (v, H) in ctx.refused


async def test_upsert_never_overwrites_a_row_that_appeared_during_the_archive() -> None:
    from dataclasses import dataclass

    v = vid()
    hot_hour(v, H, n=50)

    @dataclass(frozen=True)
    class Racer(Hooks):
        def fire(self, point: str) -> None:
            if point == "archive:after_reread":
                with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
                    a.execute(
                        "insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max,"
                        " verified_at, hot_deleted_at) values (%s, %s, 'other', 1, %s, %s, now(),"
                        " now())", (v, H, H, H),
                    )  # fmt: skip

    async with app_conn() as c:
        assert await archive_hour(archiver_ctx(hooks=Racer()), c, v, H) is None
    assert [(r["sha256"], r["version"]) for r in archive_rows(v)] == [("other", 0)]
    _drop_vehicle(v)


async def test_upsert_guard_refuses_a_row_hot_deleted_during_the_archive() -> None:
    from dataclasses import dataclass

    v = vid()
    hot_hour(v, H, n=50)

    @dataclass(frozen=True)
    class Racer(Hooks):
        def fire(self, point: str) -> None:
            if point == "archive:after_reread":
                with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
                    a.execute(
                        "update archive_object set hot_deleted_at = now() where vehicle=%s", (v,)
                    )

    async with app_conn() as c:
        assert await archive_hour(archiver_ctx(), c, v, H) is not None  # v0
        late_row(v, H + timedelta(minutes=1), 9000)
        assert await archive_hour(archiver_ctx(hooks=Racer()), c, v, H, version=1) is None
    (r,) = archive_rows(v)
    assert r["version"] == 0 and r["rows"] == 50 and r["hot_deleted_at"] is not None


async def test_merge_warns_when_a_duplicate_key_differs(caplog: pytest.LogCaptureFixture) -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        late_row(v, H + timedelta(seconds=0.5), 5)  # archived row 5 has values, this one has none
        with caplog.at_level(logging.WARNING, logger="gs.archiver.job"):
            merged = await merge_late(ctx, c, archive_row(v))
    assert merged is not None and merged.rows == 100
    warn = [r for r in caplog.records if "differ" in r.getMessage()]
    assert warn and warn[0].__dict__.get("vehicle") == v
    t = pq.read_table(io.BytesIO(object_bytes(object_key(v, H, 1))))
    assert t.filter(pc.equal(t["seq"], 5))["cabin_pressure_kpa"].to_pylist() != [
        None
    ]  # archived wins


async def test_merge_refusal_names_a_changed_archive_row(caplog: pytest.LogCaptureFixture) -> None:
    v = vid()
    hot_hour(v, H, n=100)
    ctx = archiver_ctx()
    async with app_conn() as c:
        row = await archive_hour(ctx, c, v, H)
        assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, H))
        late_row(v, H + timedelta(minutes=1), 7001)
        stale = archive_row(v)  # the row changes after it was read: the guarded UPDATE matches none
        with psycopg.connect(admin_dsn("gs"), autocommit=True) as a:
            a.execute("update archive_object set sha256 = 'z' where vehicle=%s", (v,))
        assert await merge_late(ctx, c, stale) is None
    refusal = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert refusal and "archive row changed" in refusal[-1]
    assert hot_n(v) == 1
    _drop_vehicle(v)
