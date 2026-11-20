"""GET /api/export (D-EXT-13): the file equals the stored rows, from hot rows and from verified
Parquet, in both formats; times end in Z; the 24 h cap; one export at a time (EX-02); a corrupt
archive is 409; the temporary file is gone after the answer."""

import asyncio
import csv
import io
import re
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gs.archiver import window
from gs.archiver.hours import hour_of, object_key
from gs.common.time import iso_z
from tests.integration.conftest import LiveApi
from tests.integration.test_window_reader import archive_and_delete, flip
from tests.support import export_worker
from tests.support.archive import (  # noqa: F401  (archiver_paused is the module fixture)
    app_conn,
    archive_rows,
    archiver_paused,
    hot_hour,
    param_columns,
)
from tests.support.env import vid
from tests.support.http import CLIENT_HEADERS
from tests.support.pg import admin_dsn
from tests.support.s3 import s3_client

pytestmark = pytest.mark.usefixtures("archiver_paused")
H = hour_of(datetime.now(UTC)) - timedelta(hours=44)
HOUR = timedelta(hours=1)
N = 3600  # one hour at 1 Hz
FIXED = ("vehicle", "seq", "t_onboard", "t_received", "mode", "last_cmd_id")
_made: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    root = s3_client("root")
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for v in _made:
            for t in ("archive_object", "telemetry", "test_session", "seq_state"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
            for o in root.list_objects("archive", prefix=f"{v}/", recursive=True):
                root.remove_object("archive", o.object_name)
    _made.clear()


def backfilled(n: int = N) -> str:
    v = vid()
    _made.append(v)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:  # a vehicle the ingest knows
        c.execute(
            "insert into seq_state (vehicle, epoch, last_seq, last_t, period_s)"
            " values (%s, now(), 0, now(), 1.0)",
            (v,),
        )
    hot_hour(v, H, n=n, rate_hz=1.0)
    return v


def stored(v: str) -> list[dict[str, Any]]:
    cols = [*FIXED, *param_columns()]
    with psycopg.connect(admin_dsn("gs")) as c, c.cursor() as cur:
        cur.execute(
            f"select {', '.join(cols)} from telemetry where vehicle = %s"  # noqa: S608
            " order by t_onboard, seq",
            (v,),
        )
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


@asynccontextmanager
async def client(live: LiveApi) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=live.url, cookies={"session": live.cookie}, headers=CLIENT_HEADERS, timeout=120
    ) as c:
        yield c


def url(v: str, fmt: str = "csv", t_from: datetime = H, t_to: datetime = H + HOUR, **q: str) -> str:
    extra = "".join(f"&{k}={x}" for k, x in q.items())
    return f"/api/export?vehicle={v}&from={iso_z(t_from)}&to={iso_z(t_to)}&format={fmt}{extra}"


def _z(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def csv_rows(body: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    header, *lines = list(csv.reader(io.StringIO(body.decode())))
    out = []
    for line in lines:
        row: dict[str, Any] = {}
        for name, x in zip(header, line, strict=True):
            if x == "":
                row[name] = None
            elif name in ("t_onboard", "t_received"):
                row[name] = _z(x)
            elif name in ("vehicle", "mode", "last_cmd_id"):
                row[name] = x
            else:
                row[name] = int(x) if name == "seq" else float(x)
            # the last_cmd_id of a stored row is a UUID or NULL: compared as text below
        out.append(row)
    return header, out


@pytest.mark.parametrize(
    ("fmt", "source"),
    [("csv", "hot"), ("csv", "parquet"), ("parquet", "hot"), ("parquet", "parquet")],
    ids=["csv-hot", "csv-parquet", "parquet-hot", "parquet-parquet"],
)
async def test_export_equals_stored(live_api: LiveApi, fmt: str, source: str) -> None:
    v = backfilled()
    want = stored(v)  # read before the archive delete
    assert len(want) == N
    if source == "parquet":
        async with app_conn() as conn:
            await archive_and_delete(conn, v, H)
        assert not stored(v)
    async with client(live_api) as c:
        r = await c.get(url(v, fmt))
    assert r.status_code == 200, r.text
    cols = [*FIXED, *param_columns()]
    if fmt == "csv":
        header, got = csv_rows(r.content)
        assert header == cols
    else:
        t = pq.read_table(io.BytesIO(r.content))
        assert t.column_names == cols
        assert t.schema.field("t_onboard").type == pa.timestamp("us", tz="UTC")
        assert t.schema.field("t_received").type == pa.timestamp("us", tz="UTC")
        got = t.to_pylist()
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        w = {k: (str(x) if k == "last_cmd_id" and x is not None else x) for k, x in w.items()}
        assert g == w


async def test_params_in_mission_order_and_filename(live_api: LiveApi) -> None:
    v = backfilled(60)
    p = param_columns()
    async with client(live_api) as c:
        r = await c.get(url(v, params=f"{p[2]},{p[0]}"))
    assert csv_rows(r.content)[0] == [*FIXED, p[0], p[2]]
    stamp = "%Y%m%dT%H%M%SZ"
    name = f"{v}_{H:{stamp}}_{(H + HOUR):{stamp}}.csv"
    assert r.headers["content-disposition"] == f'attachment; filename="{name}"'


async def test_by_session(live_api: LiveApi) -> None:
    v = backfilled(60)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        row = c.execute(
            "insert into test_session (vehicle, name, conductor, started_at, ended_at, ended_by)"
            " values (%s, 'exp', 'fc1', %s, %s, 'fc1') returning id",
            (v, H, H + HOUR),
        ).fetchone()
    assert row is not None
    async with client(live_api) as c:
        r = await c.get(f"/api/export?session={row[0]}&format=csv")
        assert len(csv_rows(r.content)[1]) == 60
        assert (await c.get("/api/export?session=2000000000")).status_code == 404


async def test_csv_times_end_in_z(live_api: LiveApi) -> None:
    v = backfilled(120)
    async with client(live_api) as c:
        r = await c.get(url(v))
    lines = list(csv.DictReader(io.StringIO(r.text)))
    assert len(lines) == 120
    for row in lines:
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", row["t_onboard"])
        assert row["t_received"] == "" or row["t_received"].endswith("Z")


async def test_window_too_large(live_api: LiveApi) -> None:
    v = backfilled(10)
    async with client(live_api) as c:
        over = await c.get(url(v, t_to=H + timedelta(hours=24, seconds=1)))
        assert (over.status_code, over.json()["error"]) == (400, "WINDOW_TOO_LARGE")
        assert (await c.get(url(v, t_to=H + timedelta(hours=24)))).status_code == 200
        for q, code in {
            f"vehicle={v}": "INVALID_WINDOW",
            f"vehicle={v}&from={iso_z(H)}&to={iso_z(H)}": "INVALID_WINDOW",
            f"session=1&vehicle={v}": "INVALID_WINDOW",
            f"vehicle={v}&from={iso_z(H)}&to={iso_z(H + HOUR)}&params=no_such": "UNKNOWN_PARAM",
        }.items():
            r = await c.get(f"/api/export?{q}")
            assert (r.status_code, r.json()["error"]) == (400, code), q


async def test_login_required(live_api: LiveApi) -> None:
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        assert (await c.get(url("NYX-X"))).status_code == 401


async def test_corrupt_archive_409_and_no_temp_file(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    v = backfilled(60)
    async with app_conn() as conn:
        await archive_and_delete(conn, v, H)
    (row,) = archive_rows(v)
    flip(object_key(row["vehicle"], row["hour"], row["version"]))
    async with client(live_api) as c:
        r = await c.get(url(v))
    assert (r.status_code, r.json()["error"]) == (409, "ARCHIVE_CORRUPT")
    assert not list(tmp_path.iterdir())


async def test_temp_file_removed_after_send(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    v = backfilled(60)
    async with client(live_api) as c:
        for fmt in ("csv", "parquet"):
            assert (await c.get(url(v, fmt))).status_code == 200
    await asyncio.sleep(0.2)  # the background task runs right after the body
    assert not list(tmp_path.iterdir())


async def test_second_export_429(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    v = backfilled(60)
    entered, release = asyncio.Event(), asyncio.Event()
    real: Any = window.check_rows

    async def slow(*a: Any, **k: Any) -> None:
        entered.set()
        await release.wait()
        await real(*a, **k)

    monkeypatch.setattr(window, "check_rows", slow)
    async with client(live_api) as c:
        first = asyncio.create_task(c.get(url(v)))
        await asyncio.wait_for(entered.wait(), 10)
        busy = await c.get(url(v))
        assert busy.status_code == 429 and busy.json()["error"] == "EXPORT_BUSY"
        assert int(busy.headers["retry-after"]) > 0
        release.set()
        assert (await first).status_code == 200
        assert (await c.get(url(v))).status_code == 200  # free again


async def test_unknown_vehicle_404(live_api: LiveApi) -> None:
    async with client(live_api) as c:
        r = await c.get(url(vid()))
    assert (r.status_code, r.json()["error"]) == (404, "NOT_FOUND")


@pytest.fixture
def tmp_export_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


async def _broken_then_ok(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp: Path, worker: str, message: str
) -> None:
    from gs.api import export_calc

    v = backfilled(60)
    async with app_conn() as conn:  # archived, so the worker reads MinIO
        await archive_and_delete(conn, v, H)
    async with client(live_api) as c:
        with monkeypatch.context() as m:
            m.setattr(export_calc, "export", getattr(export_worker, worker))
            r = await c.get(url(v))
        assert r.status_code == 503, r.text
        assert r.json() == {"error": "UNAVAILABLE", "message": message}
        assert not list(tmp.iterdir())  # the temporary file is gone
        assert (await c.get(url(v))).status_code == 200  # busy flag free, pool still works


async def test_worker_s3_failure_503(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_export_dir: Path
) -> None:
    await _broken_then_ok(
        live_api, monkeypatch, tmp_export_dir, "dead_s3", "archive store unreachable"
    )


async def test_worker_db_failure_503(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_export_dir: Path
) -> None:
    await _broken_then_ok(live_api, monkeypatch, tmp_export_dir, "dead_db", "database unreachable")


async def test_killed_worker_503_then_recovers(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_export_dir: Path
) -> None:
    await _broken_then_ok(
        live_api, monkeypatch, tmp_export_dir, "killed", "export worker unreachable"
    )


async def test_route_side_s3_failure_503(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, tmp_export_dir: Path
) -> None:
    v = backfilled(60)
    real: Any = window.check_rows

    async def down(*a: Any, **k: Any) -> None:
        raise OSError("minio down")

    async with client(live_api) as c:
        with monkeypatch.context() as m:
            m.setattr(window, "check_rows", down)
            r = await c.get(url(v))
        assert (r.status_code, r.json()["message"]) == (503, "archive store unreachable")
        assert not list(tmp_export_dir.iterdir())
        monkeypatch.setattr(window, "check_rows", real)
        assert (await c.get(url(v))).status_code == 200
