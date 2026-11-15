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
from gs.archiver.hours import hour_of, object_key
from gs.archiver.job import archive_hour
from gs.archiver.s3 import checksum_headers
from gs.common.hooks import Hooks
from tests.support.archive import app_conn, archive_rows, archiver_ctx, hot_hour, stored_in
from tests.support.env import vid
from tests.support.pg import app_dsn, insert_rows, rows
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


async def test_rearchive_is_idempotent_and_versions_get_their_own_key() -> None:
    v = vid()
    hot_hour(v, H, n=50)
    async with app_conn() as c:
        first = await archive_hour(archiver_ctx(), c, v, H)
        again = await archive_hour(archiver_ctx(), c, v, H)
        v1 = await archive_hour(archiver_ctx(), c, v, H, version=1)
    assert first is not None and again is not None and v1 is not None
    assert first.sha256 == again.sha256  # the encoding is deterministic
    stored = archive_rows(v)
    assert len(stored) == 1 and stored[0]["version"] == 1  # one row names the latest version
    assert object_bytes(object_key(v, H, 1)).startswith(b"PAR1")
    assert object_bytes(object_key(v, H)).startswith(b"PAR1")  # the old object is not removed


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


async def test_hour_bounds_ignore_session_timezone() -> None:
    v = vid()
    hot_hour(v, H, n=300)
    async with app_conn() as c:
        utc = await archive_hour(archiver_ctx(), c, v, H)
        await c.execute("SET TIME ZONE 'Asia/Tokyo'")
        tokyo = await archive_hour(archiver_ctx(), c, v, H)
    assert utc is not None and tokyo is not None
    assert (tokyo.rows, tokyo.t_min, tokyo.t_max, tokyo.sha256) == (
        utc.rows, utc.t_min, utc.t_max, utc.sha256,
    )  # fmt: skip
    assert tokyo.rows == 300


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
