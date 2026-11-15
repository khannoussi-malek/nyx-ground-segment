import logging
from pathlib import Path

import httpx
import pytest

from gs.api.app import create_app
from tests.support.http import api_settings


async def test_healthz_open_and_ok(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/healthz")  # no cookie
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"redis": "ok", "db": "ok"}}


async def test_healthz_503_when_db_unreachable(
    users_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = "postgresql://u:p@127.0.0.1:1/gs"  # nothing listens on port 1; env ports are irrelevant
    app = create_app(api_settings(users_file, database_url=bad))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            with caplog.at_level(logging.WARNING, logger="gs.api"):
                r = await c.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "down", "checks": {"redis": "ok", "db": "down"}}
    assert any("healthz db check failed" in m.getMessage() for m in caplog.records)


async def test_healthz_degraded_after_a_rejected_ui_event(users_file: Path) -> None:  # D-ING-27
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        app.state.hub.degraded = True  # what the hub sets on a schema reject
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {
        "status": "degraded",
        "checks": {"redis": "ok", "db": "ok", "ui_events": "degraded"},
    }


def test_archiver_healthz_degraded_on_archive_corrupt() -> None:
    import json
    import time
    from datetime import UTC, datetime, timedelta
    from typing import Any

    import psycopg

    from gs.archiver.hours import hour_of
    from gs.archiver.job import corrupt_key
    from tests.support.compose import compose
    from tests.support.env import vid
    from tests.support.pg import admin_dsn

    def read() -> dict[str, Any]:
        out = compose(
            "exec", "-T", "archiver", "python", "-c",
            "import urllib.request; print(urllib.request.urlopen("
            "'http://127.0.0.1:8081/healthz').read().decode())",
            check=False,
        ).stdout  # fmt: skip
        return dict(json.loads(out)) if out.strip() else {}

    def corrupt_count(body: dict[str, Any]) -> int:
        scrub = str(body.get("checks", {}).get("scrub", "ok"))
        return 0 if scrub == "ok" else int(scrub.split()[0])

    before = corrupt_count(read())
    v = vid()
    h = hour_of(datetime.now(UTC)) - timedelta(hours=40)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        c.execute(
            "insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max, verified_at)"
            " values (%s, %s, 'abc', 1, %s, %s, now())", (v, h, h, h),
        )  # fmt: skip
        c.execute(
            "insert into events (key, vehicle, kind, details) values (%s, %s, 'ARCHIVE_CORRUPT',"
            " %s::jsonb) on conflict (key) do nothing",
            (corrupt_key(v, h, 0), v, json.dumps({"hour": h.isoformat(), "expected": "abc"})),
        )
    try:
        body: dict[str, Any] = {}
        for _ in range(20):
            body = read()
            if corrupt_count(body) == before + 1:
                break
            time.sleep(1)
        assert body["status"] == "degraded"
        assert corrupt_count(body) == before + 1
        assert body["checks"]["scrub"] == f"{before + 1} corrupt"
    finally:
        with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
            for t in ("events", "archive_object"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
