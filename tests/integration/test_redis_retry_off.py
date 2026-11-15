"""Ingest and api build their Redis client with redis-py retry off (D-ING-03/08, R28), and a
pooled connection the server closed (a Redis restart) is reconnected at checkout, before any
command is written on it (P-pf47)."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import redis.asyncio

from gs.api.app import create_app
from gs.ingest.main import IngestSettings
from gs.ingest.main import run as ingest_run
from tests.support.env import REPO
from tests.support.http import api_settings
from tests.support.pg import app_dsn
from tests.support.redisx import client


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[redis.asyncio.Redis]:
    built: list[redis.asyncio.Redis] = []
    real = redis.asyncio.Redis.from_url

    def spy(url: str, **kw: Any) -> redis.asyncio.Redis:
        built.append(real(url, **kw))
        return built[-1]

    monkeypatch.setattr(redis.asyncio.Redis, "from_url", spy)
    return built


def _zero_retries(r: redis.asyncio.Redis) -> bool:
    retry = r.get_retry()
    return retry is not None and retry.get_retries() == 0


async def test_ingest_builds_client_with_zero_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _spy(monkeypatch)
    stop = asyncio.Event()
    stop.set()
    s = IngestSettings(
        "redis://localhost:1/0", app_dsn("gs"), "c", ("NYX-TA1",), str(REPO / "mission"),
        "127.0.0.1", 0,
    )  # fmt: skip
    await ingest_run(s, stop)
    assert _zero_retries(built[0])
    assert not built[0].connection_pool.maint_notifications_enabled()  # type: ignore[no-untyped-call]  # P-pf47


async def test_api_builds_client_with_zero_retries(
    monkeypatch: pytest.MonkeyPatch, users_file: Path
) -> None:
    built = _spy(monkeypatch)
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        pass
    assert _zero_retries(built[0])


async def test_api_reconnects_a_server_closed_connection_before_use(
    monkeypatch: pytest.MonkeyPatch, users_file: Path
) -> None:
    """redis-py 8.1 skips its checkout check for a closed connection while maintenance
    notifications are on (their default, "auto"): the first command after a Redis restart
    failed with "Connection closed by server" (503 on login). Retry stays off: the reconnect
    happens at checkout, before WATCH or any command, so nothing is ever re-sent."""
    calls: list[tuple[str, dict[str, Any]]] = []
    real = redis.asyncio.Redis.from_url

    def spy(url: str, **kw: Any) -> redis.asyncio.Redis:
        calls.append((url, kw))
        return real(url, **kw)

    monkeypatch.setattr(redis.asyncio.Redis, "from_url", spy)
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        pass
    [(url, kw)] = calls
    r = real(url, **kw)  # the api's client settings, with no Hub sharing its pool
    try:
        assert await r.ping()  # one pooled connection, now idle
        # as a restart does to every connection (the compose api's reconnect the same way)
        assert client("admin").client_kill_filter(user="api") >= 1
        await asyncio.sleep(0.2)  # the server's FIN reaches our idle pooled connection
        assert await r.ping()  # retry is off: this passes only on a reconnect at checkout
    finally:
        await r.aclose()
