"""Archiver test helpers: hot hours, archive_object rows, a context with archiver credentials."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import psycopg

from gs.archiver.job import ArchiverCtx
from gs.common.hooks import Hooks
from tests.support.pg import app_dsn, rows
from tests.support.s3 import s3_client

NO_HOOKS = Hooks()


def param_columns() -> list[str]:
    """Telemetry parameter columns (the mission db adds them), in table order."""
    found = rows(
        "select column_name from information_schema.columns where table_schema='public'"
        " and table_name='telemetry' and data_type='double precision' order by ordinal_position"
    )
    return [r["column_name"] for r in found]


def hot_hour(
    vehicle: str,
    hour: datetime,
    n: int = 36_000,
    rate_hz: float = 10.0,
    values: Mapping[str, float] | None = None,
) -> None:
    """`n` frames of `vehicle`: seq 0..n-1 at hour + i / rate_hz, every parameter filled."""
    params = param_columns()
    cols = ["t_onboard", "vehicle", "seq", "mode", *params]
    with psycopg.connect(app_dsn("gs")) as c, c.cursor() as cur:
        with cur.copy(f"copy telemetry ({', '.join(cols)}) from stdin") as cp:
            for i in range(n):
                vals = [
                    (values or {}).get(p, 100.0 + (i % 50) / 10 + k) for k, p in enumerate(params)
                ]
                cp.write_row([hour + timedelta(seconds=i / rate_hz), vehicle, i, "TEST", *vals])


def archive_rows(vehicle: str) -> list[dict[str, Any]]:
    return rows("select * from archive_object where vehicle=%s order by hour", vehicle)


def stored_in(vehicle: str, hour: datetime) -> int:
    """Hot rows of `vehicle` in the UTC hour starting at `hour`."""
    found = rows(
        "select count(*) as n from telemetry where vehicle=%s and t_onboard >= %s"
        " and t_onboard < %s",
        vehicle, hour, hour + timedelta(hours=1),
    )  # fmt: skip
    return int(found[0]["n"])


def archiver_ctx(**overrides: Any) -> ArchiverCtx:
    base: dict[str, Any] = {
        "s3": s3_client("archiver"),
        "bucket": "archive",
        "hooks": NO_HOOKS,
        "retention_h": 25.0,
    }
    return ArchiverCtx(**(base | overrides))


def app_conn() -> AbstractAsyncContextManager[psycopg.AsyncConnection[Any]]:
    @asynccontextmanager
    async def cm() -> Any:
        async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
            yield c

    return cm()
