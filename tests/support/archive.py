"""Archiver test helpers: hot hours, archive_object rows, a context with archiver credentials."""

import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import sql

from gs.archiver.hours import HOUR, hour_of
from gs.archiver.job import ArchiverCtx
from gs.common.hooks import Hooks
from tests.support.compose import compose
from tests.support.pg import admin_dsn, app_dsn, rows, throwaway_db
from tests.support.s3 import inspect_health, s3_client

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
    db: str = "gs",
) -> None:
    """`n` frames of `vehicle`: seq 0..n-1 at hour + i / rate_hz, every parameter filled."""
    params = param_columns()
    cols = ["t_onboard", "vehicle", "seq", "mode", *params]
    with psycopg.connect(app_dsn(db)) as c, c.cursor() as cur:
        with cur.copy(f"copy telemetry ({', '.join(cols)}) from stdin") as cp:
            for i in range(n):
                vals = [
                    (values or {}).get(p, 100.0 + (i % 50) / 10 + k) for k, p in enumerate(params)
                ]
                cp.write_row([hour + timedelta(seconds=i / rate_hz), vehicle, i, "TEST", *vals])


def archive_rows(vehicle: str, db: str = "gs") -> list[dict[str, Any]]:
    return rows("select * from archive_object where vehicle=%s order by hour", vehicle, db=db)


def stored_in(vehicle: str, hour: datetime, db: str = "gs") -> int:
    """Hot rows of `vehicle` in the UTC hour starting at `hour`."""
    found = rows(
        "select count(*) as n from telemetry where vehicle=%s and t_onboard >= %s"
        " and t_onboard < %s",
        vehicle, hour, hour + timedelta(hours=1), db=db,
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


def app_conn(db: str = "gs") -> AbstractAsyncContextManager[psycopg.AsyncConnection[Any]]:
    @asynccontextmanager
    async def cm() -> Any:
        async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
            yield c

    return cm()


def insert_late(vehicle: str, rows_: Sequence[Mapping[str, Any]], db: str = "gs") -> None:
    """Telemetry rows (t_onboard and seq required) into `db` as gs_app."""
    with psycopg.connect(app_dsn(db)) as c:
        for row in rows_:
            cols = {"vehicle": vehicle, "mode": "TEST"} | dict(row)
            c.execute(
                sql.SQL("insert into telemetry ({}) values ({})").format(
                    sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)),
                ),
                list(cols.values()),
            )


@contextmanager
def archive_db() -> Iterator[str]:
    """A throwaway database shaped like `gs` (same parameter columns): a pass, which acts on every
    vehicle, must never see other tests' or the simulator's rows."""
    params = param_columns()
    with throwaway_db() as name:
        with psycopg.connect(admin_dsn(name), autocommit=True) as c:
            for p in params:
                c.execute(sql.SQL("alter table telemetry add column {} double precision").format(
                    sql.Identifier(p)
                ))  # fmt: skip
        yield name


@pytest.fixture(scope="module")
def archiver_paused() -> Iterator[None]:
    """G4-3: the compose archiver would archive and delete the hot hours these tests create.
    Stopped for the module, started again (and awaited healthy) in finally."""
    try:
        compose("stop", "archiver")
        yield
    finally:
        compose("start", "archiver")
        deadline = time.monotonic() + 60
        while inspect_health("archiver") != "healthy" and time.monotonic() < deadline:
            time.sleep(1)
        assert inspect_health("archiver") == "healthy", "archiver did not come back healthy"


TA9 = "NYX-TA9"
_Z = "%Y-%m-%dT%H:%M:%SZ"


def next_ta9_window(hours: int) -> tuple[datetime, datetime]:
    """`hours` whole UTC hours of NYX-TA9 history that nothing has covered yet: from six days ago,
    or the hour after the last TA9 frame, stored or archived. A backfill behind earlier TA9
    frames would be DUP_OR_LATE (R2-14), so a used-up stack cannot be backfilled again."""
    now = datetime.now(UTC)
    start = hour_of(now - timedelta(days=6))
    # hot rows are deleted after the retention, so the archive rows count as history too
    last_hot = rows("select max(t_onboard) as t from telemetry where vehicle=%s", TA9)[0]["t"]
    last_arch = rows("select max(hour) as h from archive_object where vehicle=%s", TA9)[0]["h"]
    for t in (last_hot, last_arch):
        if t is not None:
            start = max(start, hour_of(t) + HOUR)
    end = start + hours * HOUR
    if end > now - HOUR:
        raise RuntimeError(
            "NYX-TA9 backfill window exhausted: this stack's TA9 history is used up (use a "
            "fresh stack volume; never `down -v` a stack that holds data you need)"
        )
    return start, end


def backfill_ta9(t_from: datetime, t_to: datetime, *args: str) -> str:
    """`gs-sim backfill` in a one-off simulator container; returns its stdout."""
    out = compose(
        "run", "--rm", "-T", "--no-deps", "simulator", "gs-sim", "backfill",
        "--vehicle", TA9, "--from", t_from.strftime(_Z), "--to", t_to.strftime(_Z), *args,
    )  # fmt: skip
    return out.stdout
