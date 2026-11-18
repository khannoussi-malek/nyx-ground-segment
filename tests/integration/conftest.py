import asyncio
import dataclasses
import signal
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
import yaml
from fastapi import FastAPI

from gs.api.app import create_app
from gs.api.auth import hash_password
from gs.api.server import GracefulServer, build_server
from tests.support.env import vid
from tests.support.http import FC1_PW, FC2_PW, api_settings, login
from tests.support.keys import purge_quietly


@pytest.fixture(scope="session", autouse=True)
def purge_test_vehicle_keys() -> Iterator[None]:
    """Clear throwaway-vehicle keys left by earlier runs, and again at the end."""
    purge_quietly()
    yield
    purge_quietly()


@pytest.fixture(scope="session")
def users_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("api") / "users.yaml"
    users = {"fc1": hash_password(FC1_PW), "fc2": hash_password(FC2_PW)}
    path.write_text(yaml.safe_dump({"users": users}))
    return path


@pytest.fixture
async def api_client(users_file: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(api_settings(users_file))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            yield client


@dataclass(frozen=True)
class LiveApi:
    url: str
    app: FastAPI
    server: uvicorn.Server
    cookie: str
    vehicle: str


@pytest.fixture
async def live_api(users_file: Path) -> AsyncIterator[LiveApi]:
    vehicle = vid()  # fresh vehicle, no rows
    settings = dataclasses.replace(api_settings(users_file), vehicles=(vehicle,))
    app = create_app(settings)
    server: GracefulServer = build_server(app, "127.0.0.1", 0)
    # uvicorn re-raises a captured SIGTERM when serve() exits; make that a no-op for pytest
    previous = signal.signal(signal.SIGTERM, lambda *_: None)
    task = asyncio.create_task(server.serve())
    while not server.started:
        assert not task.done(), "server failed to start"
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(base_url=url) as c:
        assert (await login(c, "fc1", FC1_PW)).status_code == 204
        cookie = c.cookies["session"]
    try:
        yield LiveApi(url, app, server, cookie, vehicle)
    finally:
        server.should_exit = True
        app.state.shutdown.set()
        await task
        signal.signal(signal.SIGTERM, previous)
