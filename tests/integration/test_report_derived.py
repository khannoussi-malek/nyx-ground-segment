"""The session report lists derived parameters too (unit from `derived`), D-EXT-05."""

from datetime import UTC, datetime, timedelta

import psycopg

from gs.api.bodies import SessionView
from gs.api.report import _stats
from gs.common.migrate import add_columns
from tests.support.mission import files, ld
from tests.support.pg import app_dsn, owner_dsn, throwaway_db


async def test_report_stats_for_a_derived_name() -> None:
    p = files()["parameters"] + b"derived:\n  twice: {unit: degC, expr: cabin_temp_c * 2}\n"
    mdb = ld(files(parameters=p))
    t0 = datetime(2026, 11, 15, 12, tzinfo=UTC)
    s = SessionView(
        id=1, vehicle="NYX-TA1", name="x", conductor="fc1", started_at=t0,
        ended_at=t0 + timedelta(seconds=10), ended_by="fc1",
    )  # fmt: skip
    with throwaway_db() as db:
        with psycopg.connect(owner_dsn(db), autocommit=True) as c:
            add_columns(c, mdb)
        async with await psycopg.AsyncConnection.connect(app_dsn(db)) as conn:
            frames, stats = await _stats(conn, s, mdb)
    assert frames == 0 and stats["twice"].unit == "degC" and stats["cabin_temp_c"].unit == "degC"
