from pathlib import Path

import httpx

from gs.api.app import create_app
from tests.integration.conftest import api_settings
from tests.support.pg import app_dsn


async def test_healthz_open_and_ok(api_client: httpx.AsyncClient) -> None:
    r = await api_client.get("/healthz")  # no cookie
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"redis": "ok", "db": "ok"}}


async def test_healthz_503_when_db_unreachable(users_file: Path) -> None:
    bad = app_dsn("gs").replace(":5432/", ":1/")  # nothing listens on port 1
    app = create_app(api_settings(users_file, database_url=bad))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://a"
        ) as c:
            r = await c.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "down", "checks": {"redis": "ok", "db": "down"}}
