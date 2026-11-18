"""API test helpers: settings, demo passwords, the login call and the session id of a client."""

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import httpx
from fastapi import FastAPI

from gs.api.settings import Settings
from gs.archiver.s3 import S3Settings
from tests.support.env import REPO, dotenv
from tests.support.pg import app_dsn

CLIENT_HEADERS = {"X-GS-Client": "console"}
FC1_PW, FC2_PW = "fc1-test-password", "fc2-test-password"


def api_settings(users_file: Path, **over: str) -> Settings:
    env = dotenv()
    ui_dist = users_file.parent / "ui"  # stands in for the built UI
    ui_dist.mkdir(exist_ok=True)
    (ui_dist / "index.html").write_text("<!doctype html><title>Nyx</title>")
    return Settings(
        redis_url=over.get(
            "redis_url",
            f"redis://api:{env['REDIS_API_PW']}@127.0.0.1:{env['REDIS_HOST_PORT']}/0",
        ),
        database_url=over.get("database_url", app_dsn("gs")),
        session_secret="test-secret",
        users_file=users_file,
        mission_db=REPO / "mission",
        vehicles=("NYX-TA1",),
        ui_dist=ui_dist,
        s3=S3Settings(
            f"127.0.0.1:{env['MINIO_HOST_PORT']}", "replay", env["S3_REPLAY_SECRET"], "archive"
        ),
    )


async def login(client: httpx.AsyncClient, user: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/login", json={"user": user, "password": password}, headers=CLIENT_HEADERS
    )


def session_sid(client: httpx.AsyncClient) -> str:
    """The sid inside the client's session cookie (payload is base64 JSON before the first dot)."""
    payload = client.cookies["session"].split(".")[0]
    return str(json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))["sid"])


@asynccontextmanager
async def authed(
    app: FastAPI, user: str = "fc1", password: str = FC1_PW
) -> AsyncIterator[httpx.AsyncClient]:
    """In-process ASGI client with the app's lifespan running, logged in, CLIENT_HEADERS set."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api", headers=CLIENT_HEADERS
        ) as client:
            assert (await login(client, user, password)).status_code == 204
            yield client


class LiveTarget(Protocol):
    url: str
    cookie: str


async def get_json(target: httpx.AsyncClient | LiveTarget, path: str) -> Any:
    """GET `path` and return the parsed JSON body (asserts status 200); `target` is an httpx
    client or a live server (the live_api fixture: base `url` and session `cookie`)."""
    if isinstance(target, httpx.AsyncClient):
        resp = await target.get(path)
    else:
        async with httpx.AsyncClient(base_url=target.url, cookies={"session": target.cookie}) as c:
            resp = await c.get(path)
    assert resp.status_code == 200, (path, resp.status_code, resp.text)
    return resp.json()
