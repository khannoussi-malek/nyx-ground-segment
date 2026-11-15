"""The replay read side: one verified table per UTC hour of a window, archive plus late hot rows."""

import hashlib
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.compute
import pytest
from minio import Minio

from gs.archiver.hours import HOUR, hour_bounds, hour_of, object_key
from gs.archiver.job import archive_hour, delete_hot
from gs.archiver.window import (
    BASE_COLUMNS,
    ArchiveCorrupt,
    has_data,
    verify_window,
    window_hours,
)
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archiver_ctx,
    archiver_paused,
    hot_hour,
    insert_late,
    param_columns,
)
from tests.support.env import vid as _vid
from tests.support.pg import admin_dsn, app_dsn
from tests.support.s3 import s3_client

H = hour_of(datetime.now(UTC)) - timedelta(hours=30)
pytestmark = pytest.mark.usefixtures("archiver_paused")
_made: list[str] = []
BUCKET = "archive"


def vid() -> str:
    v = _vid()
    _made.append(v)
    return v


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    root = s3_client("root")
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for v in _made:
            for t in ("events", "archive_object", "telemetry"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
            for o in root.list_objects(BUCKET, prefix=f"{v}/", recursive=True):
                root.remove_object(BUCKET, o.object_name)
    _made.clear()


def s3() -> Minio:
    return s3_client("archiver")


async def read(
    conn: psycopg.AsyncConnection[Any], v: str, t_from: datetime, t_to: datetime, params: list[str]
) -> list[pa.Table]:
    return [t async for t in window_hours(conn, s3(), BUCKET, v, t_from, t_to, params)]


async def archive_and_delete(c: psycopg.AsyncConnection[Any], v: str, h: datetime) -> None:
    ctx = archiver_ctx()
    row = await archive_hour(ctx, c, v, h)
    assert row is not None and await delete_hot(ctx, c, row, hour_bounds(v, h))


def flip(key: str) -> str:
    root = s3_client("root")
    data = bytearray(root.get_object(BUCKET, key).read())
    data[len(data) // 2] ^= 0xFF
    root.put_object(BUCKET, key, io.BytesIO(bytes(data)), len(data))
    return hashlib.sha256(bytes(data)).hexdigest()


def keys(tables: list[pa.Table]) -> list[tuple[Any, Any]]:
    t = pa.concat_tables(tables)
    return list(zip(t["t_onboard"].to_pylist(), t["seq"].to_pylist(), strict=True))


async def test_window_from_parquet_equals_hot() -> None:
    v = vid()
    params = param_columns()[:3]
    hot_hour(v, H, n=600, rate_hz=1.0)  # 10 minutes of 1 Hz
    lo, hi = H + timedelta(minutes=2), H + timedelta(minutes=7)
    async with app_conn() as c:
        before = await read(c, v, lo, hi, params)
        await archive_and_delete(c, v, H)
        after = await read(c, v, lo, hi, params)
    assert len(before) == len(after) == 1 and before[0].num_rows == 300
    assert before[0].equals(after[0])
    assert before[0].column_names == [*BASE_COLUMNS, *params]
    assert before[0].schema.field(params[0]).type == pa.float64()


async def test_seam_window_no_dup_no_gap() -> None:
    v = vid()
    hot_hour(v, H, n=36_000)
    hot_hour(v, H + HOUR, n=9_000)  # the next hour: hot only
    lo, hi = H + timedelta(minutes=30), H + timedelta(minutes=75)
    async with app_conn() as c:
        await archive_and_delete(c, v, H)
        late = [(H + timedelta(minutes=31), 90_000), (H + timedelta(minutes=45), 90_001)]
        insert_late(v, [{"t_onboard": t, "seq": s} for t, s in late])  # late, after the delete
        tables = await read(c, v, lo, hi, [])
    assert len(tables) == 2  # one per hour, oldest first
    ks = keys(tables)
    assert len(ks) == len(set(ks)) == 18_000 + 9_000 + 2
    assert all(lo <= t < hi for t, _ in ks) and ks == sorted(ks)
    assert {s for _, s in ks} >= {90_000, 90_001, 18_000, 35_999, 0, 8_999}
    assert [s for _, s in ks if s < 36_000 and _ < H + HOUR] == list(range(18_000, 36_000))


async def test_rows_in_both_archive_and_hot_appear_once() -> None:
    v = vid()
    hot_hour(v, H, n=200, rate_hz=1.0)
    async with app_conn() as c:
        assert await archive_hour(archiver_ctx(), c, v, H) is not None  # hot rows stay
        (t,) = await read(c, v, H, H + HOUR, [])
    assert t.num_rows == 200 and len(set(keys([t]))) == 200


COL = "g4w_added_col"


async def test_window_across_added_parameter() -> None:
    v = vid()
    hot_hour(v, H, n=100, rate_hz=1.0)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as adm:
        try:
            async with app_conn() as c:
                await archive_and_delete(c, v, H)  # v0 knows nothing of COL
                adm.execute(
                    f"alter table telemetry add column if not exists {COL} double precision"
                )
                insert_late(v, [{"t_onboard": H + HOUR, "seq": 1, COL: 7.5}])
                first, second = await read(c, v, H, H + 2 * HOUR, [COL, "never_existed"])
        finally:
            adm.execute(f"alter table telemetry drop column if exists {COL}")
    for t in (first, second):
        assert t.schema.field(COL).type == pa.float64()
        assert t.schema.field("never_existed").type == pa.float64()
    assert first[COL].null_count == 100 and first["never_existed"].null_count == 100
    assert second[COL].to_pylist() == [7.5]


async def test_corrupt_hour_raises() -> None:
    v = vid()
    hot_hour(v, H - HOUR, n=50, rate_hz=1.0)
    hot_hour(v, H, n=50, rate_hz=1.0)
    async with app_conn() as c:
        await archive_and_delete(c, v, H - HOUR)
        await archive_and_delete(c, v, H)
        expected = (
            await (
                await c.execute(
                    "select sha256 from archive_object where vehicle=%s and hour=%s", (v, H)
                )
            ).fetchone()
        )[0]  # type: ignore[index]
        found = flip(object_key(v, H))
        got: list[pa.Table] = []
        with pytest.raises(ArchiveCorrupt, match="archive object failed hash check") as e:
            async for t in window_hours(c, s3(), BUCKET, v, H - HOUR, H + HOUR, []):
                got.append(t)
        assert len(got) == 1  # the clean hour before it was served
        assert (e.value.vehicle, e.value.hour, e.value.expected, e.value.found) == (
            v,
            H,
            expected,
            found,
        )
        with pytest.raises(ArchiveCorrupt):
            await verify_window(c, s3(), BUCKET, v, H - HOUR, H + HOUR)
        await verify_window(c, s3(), BUCKET, v, H - HOUR, H)  # the clean hour alone passes
        s3_client("root").remove_object(BUCKET, object_key(v, H))  # a missing object is corrupt too
        with pytest.raises(ArchiveCorrupt) as e2:
            await read(c, v, H, H + HOUR, [])
        assert e2.value.found is None


async def test_window_ignores_session_timezone() -> None:
    v = vid()
    hot_hour(v, H, n=120, rate_hz=1.0)
    hot_hour(v, H + HOUR, n=60, rate_hz=1.0)
    lo, hi = H + timedelta(minutes=1), H + timedelta(minutes=61)
    params = param_columns()[:2]
    async with app_conn() as c:
        await archive_and_delete(c, v, H)
        utc = await read(c, v, lo, hi, params)
    url = app_dsn("gs") + "?options=-c%20timezone%3DAsia/Tokyo"
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as tokyo:
        cur = await tokyo.execute("show timezone")
        assert await cur.fetchone() == ("Asia/Tokyo",)
        tok = await read(tokyo, v, lo, hi, params)
        assert await has_data(tokyo, v, lo, hi)
    assert len(utc) == len(tok) == 2 and all(a.equals(b) for a, b in zip(utc, tok, strict=True))


async def test_has_data() -> None:
    v = vid()
    async with app_conn() as c:
        assert not await has_data(c, v, H, H + HOUR)
        hot_hour(v, H, n=60, rate_hz=1.0)
        assert await has_data(c, v, H, H + HOUR)
        assert not await has_data(c, v, H + HOUR, H + 2 * HOUR)
        await archive_and_delete(c, v, H)  # no hot rows left, only the archive row
        assert await has_data(c, v, H + timedelta(minutes=10), H + timedelta(minutes=20)) is False
        assert await has_data(c, v, H, H + timedelta(seconds=30))
        assert not await has_data(c, v, H + timedelta(minutes=2), H + HOUR)  # after the last row


async def test_empty_hours_and_half_open_edges_yield_nothing() -> None:
    v = vid()
    hot_hour(v, H + HOUR, n=10, rate_hz=1.0)
    async with app_conn() as c:
        assert await read(c, v, H, H + HOUR, []) == []  # to is exclusive: next hour not read
        assert await read(c, v, H + HOUR, H + HOUR, []) == []  # empty window
        (t,) = await read(c, v, H, H + HOUR + timedelta(seconds=3), [])
        assert t.num_rows == 3
        assert await read(c, vid(), H, H + 3 * HOUR, []) == []


class ArchiveAfterRowRead:
    """A connection proxy: right after the reader asked for the archive row, the archiver archives
    the hour and deletes its hot rows (what a pass could do between the reader's two queries)."""

    def __init__(self, real: psycopg.AsyncConnection[Any], v: str, h: datetime) -> None:
        self._real, self._v, self._h, self.fired = real, v, h, False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def execute(self, query: Any, *args: Any, **kw: Any) -> Any:
        out = await self._real.execute(query, *args, **kw)
        if "from archive_object where vehicle" in str(query) and not self.fired:
            self.fired = True
            rows_ = await out.fetchall()  # what the reader gets: no archive row yet
            async with app_conn() as other:
                await archive_and_delete(other, self._v, self._h)
            return _Cursor(rows_)
        return out


class _Cursor:
    def __init__(self, rows_: list[Any]) -> None:
        self._rows = rows_

    async def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


async def test_archive_and_delete_between_the_two_reads_loses_no_row() -> None:
    v = vid()
    hot_hour(v, H, n=300, rate_hz=1.0)
    async with app_conn() as real:
        proxy = ArchiveAfterRowRead(real, v, H)
        tables = [t async for t in window_hours(proxy, s3(), BUCKET, v, H, H + HOUR, [])]  # type: ignore[arg-type]
        assert proxy.fired
    ks = keys(tables)
    assert len(ks) == len(set(ks)) == 300  # the hot read came first: every row, once


async def test_naive_datetimes_are_rejected() -> None:
    v = vid()
    naive = datetime(2026, 1, 1)
    async with app_conn() as c:
        for lo, hi in ((naive, H), (H, naive)):
            with pytest.raises(ValueError, match="naive"):
                await read(c, v, lo, hi, [])
            with pytest.raises(ValueError, match="naive"):
                await verify_window(c, s3(), BUCKET, v, lo, hi)
            with pytest.raises(ValueError, match="naive"):
                await has_data(c, v, lo, hi)


async def test_archived_hour_merged_with_hot_rows_carrying_an_extra_column() -> None:
    v = vid()
    hot_hour(v, H, n=100, rate_hz=1.0)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as adm:
        try:
            async with app_conn() as c:
                assert await archive_hour(archiver_ctx(), c, v, H) is not None  # without COL
                adm.execute(
                    f"alter table telemetry add column if not exists {COL} double precision"
                )
                insert_late(v, [{"t_onboard": H + timedelta(minutes=30), "seq": 5000, COL: 7.5}])
                (t,) = await read(c, v, H, H + HOUR, [COL])
        finally:
            adm.execute(f"alter table telemetry drop column if exists {COL}")
    assert t.num_rows == 101 and t[COL].null_count == 100
    assert t.filter(pa.compute.equal(t["seq"], 5000))[COL].to_pylist() == [7.5]
