"""M4 chart buckets (GET /api/telemetry/{vehicle}/{param})."""

import dataclasses
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from gs.api import dbguard
from gs.api.app import create_app
from gs.common.missiondb import load_dir
from gs.common.time import iso_z
from tests.support.env import vid
from tests.support.http import api_settings, authed
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn

P = next(iter(load_dir(MISSION_DIR).parameters))
T0 = datetime(2026, 1, 1, tzinfo=UTC)  # a fixed window: nothing else writes under a fresh vid()


@pytest.fixture
async def app(users_file: Path) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    v = vid()
    application = create_app(dataclasses.replace(api_settings(users_file), vehicles=(v,)))
    async with authed(application) as c:
        yield c, v


def _gen(v: str, n: int, step_ms: int, value_sql: str, where: str = "true") -> None:
    """n rows 100 ms apart (seq = i) from T0, value from SQL on i."""
    with psycopg.connect(app_dsn("gs"), autocommit=True) as c:
        c.execute(
            f"INSERT INTO telemetry (t_onboard, vehicle, seq, mode, {P})"  # noqa: S608
            f" SELECT %s::timestamptz + i * make_interval(secs => %s), %s, i, 'TEST', {value_sql}"
            f" FROM generate_series(0, %s - 1) AS i WHERE {where}",
            (T0, step_ms / 1000, v, n),
        )


async def _get(
    c: httpx.AsyncClient, v: str, seconds: float, width: int, p: str = P
) -> httpx.Response:
    q = {"from": iso_z(T0), "to": iso_z(T0 + timedelta(seconds=seconds)), "width": str(width)}
    return await c.get(f"/api/telemetry/{v}/{p}", params=q)


async def test_first_last_per_bucket(app: tuple[httpx.AsyncClient, str]) -> None:
    c, v = app
    # 100 s of a ramp (value = i), width 10 -> 10 s buckets (100 rows each); one spike at i=55
    _gen(v, 1000, 100, "CASE WHEN i = 55 THEN 9999.0 ELSE i::float8 END")
    r = await _get(c, v, 100, 100)
    assert r.status_code == 200
    buckets = r.json()
    assert len(buckets) == 100  # width 100 -> 1 s buckets, 10 rows each
    b = buckets[5]
    assert b["t"] == iso_z(T0 + timedelta(seconds=5))
    assert (b["first"], b["last"], b["n"]) == (50.0, 59.0, 10)
    assert (b["lo"], b["hi"]) == (50.0, 9999.0)  # the spike is in the band
    assert b["mean"] == pytest.approx((sum(range(50, 60)) - 55 + 9999) / 10)


async def test_bucket_count_from_width(app: tuple[httpx.AsyncClient, str]) -> None:
    c, v = app
    _gen(v, 36000, 100, "i::float8")  # 1 h at 10 Hz
    one_h = (await _get(c, v, 3600, 1500)).json()
    assert len(one_h) == 1500  # 2.4 s buckets, 24 rows each
    assert one_h[1]["t"] == iso_z(T0 + timedelta(seconds=2.4))
    assert {b["n"] for b in one_h} == {24}
    two_min = (await _get(c, v, 120, 4000)).json()
    assert len(two_min) == 1200  # 120 s / 4000 = 30 ms is under the floor: 100 ms buckets
    assert {b["n"] for b in two_min} == {1}


async def test_empty_bucket_absent(app: tuple[httpx.AsyncClient, str]) -> None:
    c, v = app
    _gen(v, 1000, 100, "i::float8", where="i < 200 OR i >= 300")  # a 10 s hole at 20..30 s
    got = (await _get(c, v, 100, 100)).json()
    starts = [b["t"] for b in got]
    hole = [iso_z(T0 + timedelta(seconds=s)) for s in range(20, 30)]
    assert not set(hole) & set(starts)
    assert len(got) == 90


async def test_24h_window_is_valid_and_fast(app: tuple[httpx.AsyncClient, str]) -> None:
    c, v = app
    _gen(v, 864_000, 100, "(i %% 1000)::float8")  # 24 h at 10 Hz
    t = time.perf_counter()
    r = await _get(c, v, 86_400, 1500)  # exactly 24 h is inclusive-valid
    elapsed = time.perf_counter() - t
    print(f"24 h / 864000 rows -> {len(r.json())} buckets in {elapsed:.2f} s")
    assert r.status_code == 200
    buckets = r.json()
    assert len(buckets) == 1500
    assert all(0.0 <= b["lo"] <= b["hi"] <= 999.0 for b in buckets)
    assert sum(b["n"] for b in buckets) == 864_000


@pytest.mark.parametrize(
    ("seconds", "width", "param", "error"),
    [
        (60, 99, P, "WIDTH_OUT_OF_RANGE"),
        (60, 4001, P, "WIDTH_OUT_OF_RANGE"),
        (86_401, 1500, P, "WINDOW_TOO_LARGE"),
        (0, 1500, P, "WINDOW_TOO_LARGE"),
        (-5, 1500, P, "WINDOW_TOO_LARGE"),
        (60, 1500, "no_such_param", "UNKNOWN_PARAM"),
    ],
)
async def test_errors_400(
    app: tuple[httpx.AsyncClient, str], seconds: float, width: int, param: str, error: str
) -> None:
    c, v = app
    r = await _get(c, v, seconds, width, param)
    assert (r.status_code, r.json()["error"]) == (400, error)
    assert set(r.json()) == {"error", "message"}


async def test_unknown_vehicle_400(app: tuple[httpx.AsyncClient, str]) -> None:
    r = await _get(app[0], "NYX-NOPE", 60, 1500)
    assert (r.status_code, r.json()["error"]) == (400, "UNKNOWN_PARAM")


async def test_buckets_database_down_is_503(
    app: tuple[httpx.AsyncClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, v = app
    monkeypatch.setattr(dbguard, "DB_TIMEOUT_S", 0.3)
    bad = AsyncConnectionPool("postgresql://x@127.0.0.1:1/x", min_size=1, open=False)
    await bad.open(wait=False)
    c._transport.app.state.pool = bad  # type: ignore[attr-defined]
    try:
        r = await _get(c, v, 60, 1500)
    finally:
        await bad.close()
    assert (r.status_code, r.json()["error"]) == (503, "UNAVAILABLE")
