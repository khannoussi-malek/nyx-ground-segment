import base64
import hashlib
import io
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import psycopg
import pyarrow as pa
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
from tests.support.archive import app_conn, archive_rows, archiver_ctx, hot_hour, stored_in
from tests.support.env import vid
from tests.support.pg import admin_dsn, app_dsn, insert_rows, rows
from tests.support.s3 import s3_client

H = hour_of(datetime.now(UTC)) - timedelta(hours=30)


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
        late_row(v, H + timedelta(milliseconds=1), 8888)
        await c.execute(
            "delete from telemetry where vehicle=%s and seq=299", (v,)
        )  # max moves down
        late_row(v, H + timedelta(seconds=29, milliseconds=990), 8889)
    assert archive_rows(v)[0]["version"] == 0  # the old version stays the recorded one


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


def archive_row(v: str) -> Any:
    from gs.archiver.plan import ArchiveRow

    return ArchiveRow(*rows(
        "select vehicle, hour, sha256, rows, t_min, t_max, version, verified_at, hot_deleted_at,"
        " last_checked_at from archive_object where vehicle=%s", v,
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
