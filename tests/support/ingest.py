"""In-process ingest harness: run_vehicle tasks / single batches against the compose redis + db."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg

from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.validate import FieldStats
from gs.ingest.worker import Ctx, process_batch, run_vehicle
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn
from tests.support.redisx import aclient


def make_ctx(consumer: str) -> Ctx:
    names = load_dir(MISSION_DIR).param_names
    return Ctx(
        consumer, build_frame_model(names), names, aclient("ingest", decode=False), FieldStats(), {}
    )


@dataclass
class Running:
    ctx: Ctx
    tasks: list[asyncio.Task[None]]

    def done(self) -> bool:
        return any(t.done() for t in self.tasks)


@asynccontextmanager
async def worker(vehicles: list[str], consumer: str = "ingest-test") -> AsyncIterator[Running]:
    ctx, stop = make_ctx(consumer), asyncio.Event()
    tasks = [asyncio.create_task(run_vehicle(ctx, v, app_dsn("gs"), stop)) for v in vehicles]
    try:
        yield Running(ctx, tasks)
    finally:
        stop.set()
        try:
            await asyncio.gather(*tasks)
        finally:
            await ctx.redis.aclose()


async def process_batch_once(
    v: str, entries: list[tuple[str, dict[bytes, bytes]]], consumer: str = "c1"
) -> int:
    ctx = make_ctx(consumer)
    try:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as conn:
            return await process_batch(ctx, conn, f"tm:{v}", entries)
    finally:
        await ctx.redis.aclose()
