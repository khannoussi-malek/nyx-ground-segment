"""Backfill through the real stream path into an in-process ingest worker (a vid() vehicle: the
library writes any vehicle, only the CLI is pinned to NYX-TA9)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from gs.archiver.hours import HOUR, hour_of
from gs.archiver.main import run_pass
from gs.simulator.backfill import backfill, parse_fault
from tests.support.archive import app_conn, archive_db, archiver_ctx, archiver_paused  # noqa: F401
from tests.support.env import until, vid
from tests.support.ingest import worker
from tests.support.pg import app_dsn, rows
from tests.support.redisx import aclient, client, drained

pytestmark = pytest.mark.usefixtures("archiver_paused")
START = hour_of(datetime.now(UTC)) - timedelta(days=3)
_made: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    for v in _made:
        client("admin").delete(f"tm:{v}")
    _made.clear()


async def ingest(db: str, v: str, hours: int = 1, leak_at: float | None = 600) -> int:
    faults = [parse_fault(f"leak@{leak_at}", v)] if leak_at is not None else []
    async with worker([v], dsn=app_dsn(db)):
        r = aclient("sim")
        try:
            n = await backfill(r, v, START, START + hours * HOUR, "seed", faults)
        finally:
            await r.aclose()
        await until(lambda: drained(v), 600)
    return n


async def test_backfilled_hour_stored_and_new_to_engine() -> None:
    v = vid()
    _made.append(v)
    with archive_db() as db:
        assert await ingest(db, v) == 36_000
        (n,) = rows("select count(*) as n from telemetry where vehicle=%s", v, db=db)
        assert n["n"] == 36_000
        t = rows(
            "select min(t_onboard) lo, max(t_onboard) hi from telemetry where vehicle=%s", v, db=db
        )
        assert t[0]["lo"] == START and t[0]["hi"] == START + HOUR - timedelta(milliseconds=100)
        raises = rows(
            "select param, trigger_t_onboard from alarm_events where vehicle=%s and kind='RAISE'",
            v, db=db,
        )  # fmt: skip
        assert raises and raises[0]["param"] == "cabin_pressure_kpa"
        assert raises[0]["trigger_t_onboard"] >= START + timedelta(
            seconds=600
        )  # not before the leak
        odd = rows(
            "select kind from events where vehicle=%s and kind in"
            " ('GAP','SEQ_JUMP','SEQ_RESET','SEQ_ANOMALY','POISON')", v, db=db,
        )  # fmt: skip
        assert odd == []  # every frame in order: nothing was DUP_OR_LATE or lost


async def test_backfilled_hour_archives_at_once() -> None:
    v = vid()
    _made.append(v)
    with archive_db() as db:
        await ingest(db, v, leak_at=None)
        async with app_conn(db) as c:
            res = await run_pass(archiver_ctx(), c)  # past hours are eligible at once (D-ARC-05)
            assert not res.failed and res.archived == 1
        (row,) = rows(
            "select rows, t_min, t_max, version from archive_object where vehicle=%s", v, db=db
        )
        assert (row["rows"], row["version"], row["t_min"]) == (36_000, 0, START)
