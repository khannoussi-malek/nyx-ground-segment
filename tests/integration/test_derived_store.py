"""Derived expr parameters end to end (D-EXT-05): stored on every row, alarmed through the
engine, a bad expression is a NULL plus a counter, never a DLQ."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from gs.common.migrate import add_columns
from gs.common.missiondb import MissionDb, load_dir
from tests.support.env import until, vid
from tests.support.ingest import worker
from tests.support.missionfiles import mission_copy
from tests.support.pg import app_dsn, owner_dsn, rows, throwaway_db
from tests.support.redisx import dlq_entries, drained, produce_values


def setup(tmp_path: Path, db: str, extra: str = "") -> tuple[Path, MissionDb]:
    m = mission_copy(tmp_path)
    if extra:
        with (m / "parameters.yaml").open("a") as f:
            f.write(extra)
    mdb = load_dir(m)
    with psycopg.connect(owner_dsn(db), autocommit=True) as c:
        add_columns(c, mdb)
    return m, mdb


async def test_power_w_every_row(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        produce_values(v, {"bus_voltage_v": [28.0] * 5, "bus_current_a": [2.0] * 5})
        async with worker([v], dsn=app_dsn(db), mission_dir=m):
            await until(lambda: drained(v), 10)
        r = rows("select power_w from telemetry where vehicle=%s order by seq", v, db=db)
        assert [x["power_w"] for x in r] == [56.0] * 5


async def test_derived_alarm_raised(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        produce_values(v, {"bus_voltage_v": [28.0] * 5, "bus_current_a": [11.0] * 5})  # 308 W
        async with worker([v], dsn=app_dsn(db), mission_dir=m):
            await until(lambda: drained(v), 10)
        q = "select level from alarm_events where vehicle=%s and param='power_w' and kind='RAISE'"
        assert [x["level"] for x in rows(q, v, db=db)] == ["RED_HIGH"]


async def test_bad_expr_never_dlqs(tmp_path: Path) -> None:
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db, "  ratio: {unit: x, expr: bus_voltage_v / rate_x_dps}\n")
        produce_values(v, {"rate_x_dps": [0.0] * 3})
        async with worker([v], dsn=app_dsn(db), mission_dir=m) as w:
            await until(lambda: drained(v), 10)
        assert w.ctx.counters["derived_null:ratio:div0"] == 3
        r = rows("select ratio from telemetry where vehicle=%s", v, db=db)
        assert len(r) == 3 and all(x["ratio"] is None for x in r)
        assert dlq_entries(v) == []


async def test_leak_rate_during_leak(tmp_path: Path) -> None:  # EX-28
    v = vid()
    with throwaway_db() as db:
        m, _ = setup(tmp_path, db)
        kpa = [101.3 - 0.4 * i / 10 for i in range(80)]  # 0.4 kPa/s at 10 Hz
        # whole-second start (now, floored): the fraction of the epoch-seconds float decides the
        # 8th span (7 or 8 NULLs); a fixed date would fall outside the D-ING-18 window
        base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
        produce_values(v, {"cabin_pressure_kpa": kpa}, base=base)
        async with worker([v], dsn=app_dsn(db), mission_dir=m) as w:
            await until(lambda: drained(v), 10)
        q = "select seq, cabin_leak_rate_kpa_s as r from telemetry where vehicle=%s order by seq"
        r = rows(q, v, db=db)
        assert len(r) == 80  # every frame stored, so the checks below are not vacuous
        assert all(x["r"] is None for x in r[:7])  # window under 80 % full: NULL
        assert all(x["r"] is not None and abs(x["r"] + 0.4) < 0.01 for x in r[10:])
        cold = sum(x["r"] is None for x in r)
        assert cold == 7
        assert w.ctx.counters["derived_null:cabin_leak_rate_kpa_s:samples"] == cold
