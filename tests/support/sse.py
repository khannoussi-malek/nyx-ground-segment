"""SSE test helpers: a live-server stream reader, a seeded telemetry row, a ui:events publisher."""

import asyncio
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
from tests.support.env import REPO
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
