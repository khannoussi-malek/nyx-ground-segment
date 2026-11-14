"""The compose `api` service, through its published port, with the demo users from .env."""

import httpx
import pytest

from tests.support.env import dotenv, sh
from tests.support.http import login


def test_compose_config_is_valid() -> None:
    sh("docker", "compose", "config", "-q")


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
