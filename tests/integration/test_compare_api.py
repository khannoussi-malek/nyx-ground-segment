"""GET /api/compare (D-EXT-11): sessions overlaid on one bucket width, from hot rows and from
verified Parquet; the limits; one compare at a time (EX-02). The time is recorded, never asserted
(A-R2R-8)."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from gs.archiver import window
from gs.archiver.hours import hour_of, object_key
from tests.integration.conftest import LiveApi
from tests.integration.test_window_reader import archive_and_delete
from tests.support import compare_worker
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
H = hour_of(datetime.now(UTC)) - timedelta(hours=40)
SECONDS = 600
_made: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    root = s3_client("root")
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        for v in _made:
            for t in ("archive_object", "telemetry", "test_session", "events"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
            for o in root.list_objects("archive", prefix=f"{v}/", recursive=True):
                root.remove_object("archive", o.object_name)
    _made.clear()


def session(hours: float = SECONDS / 3600, start: datetime = H, data: bool = True) -> int:
    """A finished session of a fresh vehicle, 1 Hz rows over its first SECONDS seconds."""
    v = vid()
    _made.append(v)
    if data:
        hot_hour(v, start, n=SECONDS, rate_hz=1.0)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        row = c.execute(
            "insert into test_session (vehicle, name, conductor, started_at, ended_at, ended_by)"
            " values (%s, 'cmp', 'fc1', %s, %s, 'fc1') returning id",
            (v, start, start + timedelta(hours=hours)),
        ).fetchone()
    assert row is not None
    return int(row[0])


@asynccontextmanager
async def client(live: LiveApi) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=live.url, cookies={"session": live.cookie}, headers=CLIENT_HEADERS, timeout=60
    ) as c:
        yield c


def url(ids: Sequence[int | str], params: Sequence[str]) -> str:
    return f"/api/compare?sessions={','.join(map(str, ids))}&params={','.join(params)}"


@pytest.mark.parametrize("source", ["hot", "parquet"])
async def test_two_sessions_overlay(
    live_api: LiveApi, source: str, record_property: Callable[[str, object], None]
) -> None:
    ids = [session(), session()]
    params = param_columns()[:2]
    if source == "parquet":
        async with app_conn() as conn:
            for v in _made:
                await archive_and_delete(conn, v, H)
    async with client(live_api) as c:
        t0 = time.perf_counter()
        r = await c.get(url(ids, params))
        record_property("seconds", f"{time.perf_counter() - t0:.3f}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket_s"] == pytest.approx(SECONDS / 1500)
    assert [s["id"] for s in body["sessions"]] == ids
    assert [(s["session_id"], s["param"]) for s in body["series"]] == [
        (i, p) for i in ids for p in params
    ]
    for s in body["series"]:
        k = params.index(s["param"])
        b = s["buckets"]
        assert sum(x["n"] for x in b) == SECONDS and b[0]["t_rel_s"] == 0
        assert b[0]["lo"] == b[0]["hi"] == b[0]["mean"] == 100.0 + k  # frame 0: 100 + 0 + k
        assert [x["t_rel_s"] for x in b] == sorted(x["t_rel_s"] for x in b)


async def test_limits_400(live_api: LiveApi) -> None:
    a, b = session(), session()
    long = session(hours=25, data=False)
    p = param_columns()[0]
    cases = {
        "INVALID_SESSIONS": [url([a], [p]), url([a, a], [p]), url([a, "x"], [p]),
                             url(list(range(1, 8)), [p])],
        "INVALID_PARAMS": [url([a, b], []), url([a, b], param_columns()[:5])],
        "UNKNOWN_PARAM": [url([a, b], ["no_such_param"])],
        "WINDOW_TOO_LARGE": [url([a, long], [p])],
    }  # fmt: skip
    async with client(live_api) as c:
        for code, urls in cases.items():
            for u in urls:
                r = await c.get(u)
                assert (r.status_code, r.json()["error"]) == (400, code), (u, r.text)


async def test_unknown_session_404(live_api: LiveApi) -> None:
    a = session()
    async with client(live_api) as c:
        r = await c.get(url([a, 2_000_000_000], [param_columns()[0]]))
    assert r.status_code == 404


async def test_login_required(live_api: LiveApi) -> None:
    async with httpx.AsyncClient(base_url=live_api.url) as c:
        assert (await c.get(url([1, 2], ["x"]))).status_code == 401


async def test_corrupt_archive_409(live_api: LiveApi) -> None:
    from tests.integration.test_window_reader import flip

    ids = [session(), session()]
    async with app_conn() as conn:
        await archive_and_delete(conn, _made[0], H)
    (row,) = archive_rows(_made[0])
    flip(object_key(row["vehicle"], row["hour"], row["version"]))
    async with client(live_api) as c:
        r = await c.get(url(ids, [param_columns()[0]]))
    assert r.status_code == 409


async def test_second_compare_429(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [session(), session()]
    p = param_columns()[0]
    entered, release = asyncio.Event(), asyncio.Event()
    real: Any = window.check_rows

    async def slow(*a: Any, **k: Any) -> None:
        entered.set()
        await release.wait()
        await real(*a, **k)

    monkeypatch.setattr(window, "check_rows", slow)
    async with client(live_api) as c:
        first = asyncio.create_task(c.get(url(ids, [p])))
        await asyncio.wait_for(entered.wait(), 10)
        busy = await c.get(url(ids, [p]))
        assert busy.status_code == 429 and busy.json()["error"] == "COMPARE_BUSY"
        release.set()
        assert (await first).status_code == 200
        assert (await c.get(url(ids, [p]))).status_code == 200  # free again


async def _broken_then_ok(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch, worker: str, message: str
) -> None:
    from gs.api import compare_calc

    ids = [session(), session()]
    p = param_columns()[0]
    async with app_conn() as conn:  # archived, so the worker reads MinIO
        for v in _made:
            await archive_and_delete(conn, v, H)
    async with client(live_api) as c:
        with monkeypatch.context() as m:
            m.setattr(compare_calc, "compute", getattr(compare_worker, worker))
            r = await c.get(url(ids, [p]))
        assert r.status_code == 503, r.text
        assert r.json() == {"error": "UNAVAILABLE", "message": message}
        assert (await c.get(url(ids, [p]))).status_code == 200  # the pool still works


async def test_worker_s3_failure_503(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    await _broken_then_ok(live_api, monkeypatch, "dead_s3", "archive store unreachable")


async def test_worker_db_failure_503(live_api: LiveApi, monkeypatch: pytest.MonkeyPatch) -> None:
    await _broken_then_ok(live_api, monkeypatch, "dead_db", "database unreachable")


async def test_killed_worker_503_then_recovers(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _broken_then_ok(live_api, monkeypatch, "killed", "compare worker unreachable")


async def test_worker_corrupt_409_names_vehicle_and_hour(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gs.api import compare_calc

    ids = [session(), session()]
    monkeypatch.setattr(compare_calc, "compute", compare_worker.corrupt)
    async with client(live_api) as c:
        r = await c.get(url(ids, [param_columns()[0]]))
    assert r.status_code == 409 and r.json()["error"] == "ARCHIVE_CORRUPT"
    assert "V1" in r.json()["message"] and "2026-11-01T10:00:00.000000Z" in r.json()["message"]


async def test_worker_statement_timeout(live_api: LiveApi) -> None:
    from gs.api import worker_calc

    conn = await worker_calc.connect(live_api.app.state.database_url, statement_timeout_ms=200)
    async with conn:
        with pytest.raises(psycopg.errors.QueryCanceled):
            await conn.execute("select pg_sleep(5)")


async def test_route_side_s3_failure_503(
    live_api: LiveApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = [session(), session()]

    async def down(*a: Any, **k: Any) -> None:
        raise OSError("minio down")

    async with client(live_api) as c:
        with monkeypatch.context() as m:
            m.setattr(window, "check_rows", down)
            r = await c.get(url(ids, [param_columns()[0]]))
        assert (r.status_code, r.json()["message"]) == (503, "archive store unreachable")
        assert (await c.get(url(ids, [param_columns()[0]]))).status_code == 200


def _applied(v: str, t: datetime, sha: str) -> None:
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        c.execute(
            "insert into events (key, vehicle, kind, t_onboard, details)"
            " values (%s, %s, 'LIMITS_APPLIED', %s, %s)",
            (f"LIMITS_APPLIED/{v}/{t.isoformat()}", v, t, Jsonb({"parameters_sha256": sha})),
        )


async def test_compare_flags_two_parameter_sets(live_api: LiveApi) -> None:  # R23 M1
    ids = [session(), session()]
    async with client(live_api) as c:
        plain = (await c.get(url(ids, param_columns()[:1]))).json()
        for i, sha in zip(ids, ("a" * 64, "b" * 64), strict=True):  # one set each, they differ
            row = (
                psycopg.connect(admin_dsn("gs"), autocommit=True)
                .execute("select vehicle from test_session where id = %s", (i,))
                .fetchone()
            )
            assert row is not None
            _applied(row[0], H - timedelta(hours=1), sha)
        flagged = (await c.get(url(ids, param_columns()[:1]))).json()
    assert (plain["parameters_sha256"], plain["spans_parameters_change"]) == ([], False)
    assert flagged["parameters_sha256"] == ["a" * 64, "b" * 64]
    assert flagged["spans_parameters_change"] is True
