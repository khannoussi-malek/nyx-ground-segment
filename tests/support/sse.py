"""SSE test helpers: a live-server stream reader, a seeded telemetry row, a ui:events publisher."""

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jsonschema
import psycopg

from gs.common.interfaces import Tm
from tests.support.compose import api_url
from tests.support.env import REPO, dotenv, wait_until
from tests.support.http import CLIENT_HEADERS
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn
from tests.support.redisx import client

TIMEOUT_S = 3.0


@dataclass
class Stream:
    lines: AsyncIterator[str]
    seen: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @property
    def last(self) -> dict[str, Any]:
        return self.seen[-1][1]

    async def _next(self) -> tuple[str, dict[str, Any]]:
        event = ""
        async for line in self.lines:
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                item = (event, json.loads(line[6:]))
                self.seen.append(item)
                return item
        raise EOFError("stream closed")

    async def take(self, n: int) -> list[tuple[str, dict[str, Any]]]:
        async with asyncio.timeout(TIMEOUT_S):
            return [await self._next() for _ in range(n)]

    async def next_of(self, kind: str, timeout: float = TIMEOUT_S) -> tuple[str, dict[str, Any]]:
        async with asyncio.timeout(timeout):
            while (item := await self._next())[0] != kind:
                pass
            return item

    async def until_closed(self, timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    await self._next()
        except EOFError:
            return


@asynccontextmanager
async def sse(live: Any) -> AsyncIterator[Stream]:
    async with httpx.AsyncClient(base_url=live.url, cookies={"session": live.cookie}) as c:
        async with c.stream("GET", "/api/events", timeout=10) as r:
            assert r.status_code == 200
            yield Stream(r.aiter_lines())


def validate(kind: str, data: dict[str, Any]) -> None:
    schema = json.loads(
        (REPO / "interfaces" / "schemas" / "sse" / f"{kind}.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(data)


def seed_row(vehicle: str, seq: int) -> None:
    """One telemetry row inserted as gs_app (only the NOT NULL columns)."""
    t = datetime.now(UTC) - timedelta(seconds=5)
    with psycopg.connect(app_dsn("gs")) as c:
        c.execute(
            "insert into telemetry (t_onboard, vehicle, seq, mode) values (%s, %s, %s, 'TEST')",
            (t, vehicle, seq),
        )


def publish_tm(vehicle: str, seq: int) -> None:
    """XADD a ui:events entry as user ingest, like the worker does after a commit."""
    from gs.common.missiondb import load_dir

    names = load_dir(MISSION_DIR).param_names
    tm = Tm(
        vehicle=vehicle,
        seq=seq,
        t_onboard=datetime.now(UTC) - timedelta(seconds=1),
        mode="TEST",
        last_cmd_id=None,
        values={p: float(seq) for p in names},
    )
    client("ingest").xadd("ui:events", {"data": tm.model_dump_json()})


def now() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class LiveStack:
    """The compose API as an `sse()` target: base url and a logged-in session cookie."""

    url: str
    cookie: str


def live_stack(user: str = "fc1") -> LiveStack:
    """Logs in, retrying a 503 for up to 60 s: an earlier system test may have just restarted
    Redis, and each pooled API connection from before the restart fails once (retry off, R28)
    even after /healthz is green again (HEADS-UP in the Task 27 report)."""
    got: list[httpx.Response] = []

    def logged_in() -> bool:
        try:
            r = httpx.post(
                f"{api_url()}/api/login",
                json={"user": user, "password": dotenv()[f"DEMO_PW_{user.upper()}"]},
                headers=CLIENT_HEADERS,
                timeout=5,
            )
        except httpx.HTTPError:
            return False
        got.append(r)
        assert r.status_code in (204, 503), r.text
        return r.status_code == 204

    wait_until(logged_in, 60)
    return LiveStack(api_url(), got[-1].cookies["session"])


@asynccontextmanager
async def collecting(live: Any) -> AsyncIterator[Stream]:
    """`sse(live)` read by a background task into `stream.seen`, so blocking test steps (compose
    stop/start in a thread) never leave the API with a slow client."""
    async with sse(live) as stream:

        async def drain() -> None:
            with contextlib.suppress(EOFError):
                while True:
                    await stream._next()

        task = asyncio.create_task(drain())
        try:
            yield stream
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def wait_seen(
    stream: Stream, pred: Any, timeout: float, start: int = 0
) -> tuple[str, dict[str, Any]]:
    """The first (kind, data) at index >= start of stream.seen with pred(kind, data) true."""
    async with asyncio.timeout(timeout):
        i = start
        while True:
            while i < len(stream.seen):
                if pred(*stream.seen[i]):
                    return stream.seen[i]
                i += 1
            await asyncio.sleep(0.05)
