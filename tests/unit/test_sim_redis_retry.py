"""The simulator's Redis client has redis-py's own retry off; send_loop owns retries (D-SIM-05)."""

import asyncio
from typing import Any

import pytest
import redis.asyncio

from gs.simulator.main import SimSettings, run


async def test_run_builds_client_with_zero_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[redis.asyncio.Redis] = []
    real = redis.asyncio.Redis.from_url

    def spy(url: str, **kw: Any) -> redis.asyncio.Redis:
        built.append(real(url, **kw))
        return built[-1]

    monkeypatch.setattr(redis.asyncio.Redis, "from_url", spy)
    stop = asyncio.Event()
    stop.set()
    await run(SimSettings("redis://localhost:1/0", ("NYX-TA1",), "1", "127.0.0.1", 0), stop)
    retry = built[0].get_retry()
    assert retry is not None and retry.get_retries() == 0
