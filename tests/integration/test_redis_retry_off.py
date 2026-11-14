"""Ingest and api build their Redis client with redis-py retry off (D-ING-03/08, R28)."""

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


async def test_api_builds_client_with_zero_retries(
    monkeypatch: pytest.MonkeyPatch, users_file: Path
) -> None:
    built = _spy(monkeypatch)
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        pass
    assert _zero_retries(built[0])
