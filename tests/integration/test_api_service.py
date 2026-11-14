"""The compose `api` service, through its published port, with the demo users from .env."""

import asyncio
import time
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from tests.support.env import dotenv, sh
from tests.support.http import login


@pytest.fixture(scope="module", autouse=True)
def up() -> Iterator[None]:
    sh("docker", "compose", "config", "-q")  # R16
    sh("docker", "compose", "up", "-d", "--build", "--wait", "api")
    yield
    sh("docker", "compose", "stop", "api")


@pytest.fixture
async def service() -> httpx.AsyncClient:
    port = dotenv()["API_HOST_PORT"]
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")


async def test_service_healthy_and_real_demo_login(service: httpx.AsyncClient) -> None:
    async with service as c:
        assert (await c.get("/healthz")).json()["status"] == "ok"
        assert (await login(c, "fc1", "wrong")).status_code == 401
        assert (await login(c, "fc1", dotenv()["DEMO_PW_FC1"])).status_code == 204
        assert (await c.get("/api/me")).json() == {"user": "fc1"}


async def test_restart_with_an_open_stream_is_fast(service: httpx.AsyncClient) -> None:
    """S6: plain uvicorn took 11.1 s to stop with an SSE client attached."""
    async with service as c:
        assert (await login(c, "fc1", dotenv()["DEMO_PW_FC1"])).status_code == 204
        async with c.stream("GET", "/api/events", timeout=30) as r:
            assert r.status_code == 200
            lines = r.aiter_lines()
            assert [line async for line in _first(lines, 3)][-1] == "event: hello"
            t = time.monotonic()
            await asyncio.to_thread(sh, "docker", "compose", "restart", "api")
            assert time.monotonic() - t < 3  # S6: ~1.2 s stop with the hook


async def _first(lines: AsyncIterator[str], n: int) -> AsyncIterator[str]:
    async for line in lines:
        yield line
        n -= 1
        if n == 0:
            return
