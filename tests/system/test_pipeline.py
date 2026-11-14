"""sim -> ingest -> telemetry -> ui:events -> SSE, on the running containers."""

import httpx

from tests.support.compose import api_url
from tests.support.env import dotenv
from tests.support.http import CLIENT_HEADERS
from tests.support.sse import sse, validate

VEHICLES = ("NYX-TA1", "NYX-TA2")


class _Live:
    def __init__(self, url: str, cookie: str) -> None:
        self.url, self.cookie = url, cookie


def _login_cookie(user: str) -> str:
    r = httpx.post(
        f"{api_url()}/api/login",
        json={"user": user, "password": dotenv()[f"DEMO_PW_{user.upper()}"]},
        headers=CLIENT_HEADERS,
    )
    assert r.status_code == 204
    return r.cookies["session"]


async def test_live_frames_reach_sse() -> None:
    async with sse(_Live(api_url(), _login_cookie("fc1"))) as stream:
        try:
            await stream.until_closed(3.0)
        except TimeoutError:
            pass
    msgs = stream.seen
    assert set(msgs[1][1]["vehicles"]) == set(VEHICLES)
    for v in VEHICLES:
        seqs = [d["seq"] for e, d in msgs if e == "tm" and d["vehicle"] == v]
        assert len(seqs) >= 15 and seqs == sorted(set(seqs)), v
    for e, d in msgs:
        validate(e, d)
