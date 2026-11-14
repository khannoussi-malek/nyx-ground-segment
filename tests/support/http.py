"""API test helpers: settings, demo passwords, the login call and the session id of a client."""

import base64
import json
from pathlib import Path

import httpx

from gs.api.settings import Settings
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
    )


async def login(client: httpx.AsyncClient, user: str, password: str) -> httpx.Response:
    return await client.post(
        "/api/login", json={"user": user, "password": password}, headers=CLIENT_HEADERS
    )


def session_sid(client: httpx.AsyncClient) -> str:
    """The sid inside the client's session cookie (payload is base64 JSON before the first dot)."""
    payload = client.cookies["session"].split(".")[0]
    return str(json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))["sid"])
