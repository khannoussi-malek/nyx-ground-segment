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
