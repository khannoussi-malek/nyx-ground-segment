"""D-EXT-18: on the running stack, every SSE message of the eight kinds matches its exported
schema (interfaces/schemas/sse/<kind>.schema.json)."""

import asyncio
import functools
import time
from typing import Any

import httpx

from tests.support.compose import compose
from tests.support.http import CLIENT_HEADERS
from tests.support.sim import post_fault
from tests.support.sse import collecting, live_stack, validate, wait_seen

V = "NYX-TA1"
KINDS = {"hello", "snapshot", "tm", "alarm", "cmd", "link", "ground", "hb"}
BROKER_DOWN_S = 3.0


def kind_is(kind: str, **match: Any) -> Any:
    return lambda k, d: k == kind and all(d.get(f) == x for f, x in match.items())


def broker_outage() -> None:
    try:
        compose("stop", "redis")
        time.sleep(BROKER_DOWN_S)
    finally:
        compose("start", "redis")


async def test_every_sse_kind_matches_schema() -> None:
    live = live_stack()
    async with collecting(live) as s:
        await wait_seen(s, kind_is("snapshot"), 5)
        red = functools.partial(
            post_fault, V, "step", param="cabin_temp_c", value=31.0, duration_s=2
        )
        assert await asyncio.to_thread(red) == 204
        await wait_seen(s, lambda k, d: k == "alarm" and d["alarm"]["vehicle"] == V, 10)
        async with httpx.AsyncClient(
            base_url=live.url, cookies={"session": live.cookie}, headers=CLIENT_HEADERS
        ) as c:
            body = {"vehicle": V, "name": "isolate_valve", "args": {"valve": "A"}}
            r = await c.post("/api/commands", json=body)
        assert r.status_code == 202, r.text
        cmd_id = r.json()["cmd_id"]
        await wait_seen(s, lambda k, d: k == "cmd" and d["command"]["cmd_id"] == cmd_id, 10)
        assert await asyncio.to_thread(post_fault, V, "dropout", duration_s=3) == 204
        await wait_seen(s, lambda k, d: k == "link" and d["event"]["vehicle"] == V, 15)
        mark = len(s.seen)
        await asyncio.to_thread(broker_outage)
        down = await wait_seen(s, kind_is("ground", broker="down"), 15, mark)
        up = await wait_seen(s, kind_is("ground", broker="up"), 60, s.seen.index(down))
        await wait_seen(s, kind_is("snapshot"), 10, s.seen.index(up))
    assert {k for k, _ in s.seen} == KINDS
    for kind, data in s.seen:
        validate(kind, data)
