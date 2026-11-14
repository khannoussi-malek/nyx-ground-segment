"""Mission-db sets applied at the owner's batch boundary (Task 17; ALM-08, D-ALM-12, D-DB-11)."""

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from gs.common.migrate import add_columns
from gs.common.missiondb import MissionDb, load_dir
from gs.ingest.events import insert_set, rejected_key
from gs.simulator.frames import build_frame
from gs.simulator.nominals import NOMINALS
from tests.support.env import now, until, vid
from tests.support.ingest import worker
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy, set_formula, sha256_file
from tests.support.pg import app_dsn, owner_dsn, rows, throwaway_db
from tests.support.redisx import drained, stream_values, xadd_raw

SHIPPED = load_dir(MISSION_DIR)
NOMINAL = {p: nom for p, (nom, _) in NOMINALS.items()}
ROC_LINE = next(ln for ln in (MISSION_DIR / "limits.yaml").read_text().splitlines() if "roc" in ln)
Values = Callable[[float], dict[str, float]]


def applied(v: str, set_sha256: str, db: str = "gs") -> list[dict[str, Any]]:
    return rows(
        "select * from events where vehicle=%s and kind='LIMITS_APPLIED'"
        " and details->>'set_sha256'=%s order by id",
        v,
        set_sha256,
        db=db,
    )


def applied_sets(v: str) -> list[str]:
    q = "select * from events where vehicle=%s and kind='LIMITS_APPLIED' order by id"
    return [e["details"]["set_sha256"] for e in rows(q, v)]


def alarm_events(v: str, db: str = "gs") -> list[dict[str, Any]]:
    return rows("select * from alarm_events where vehicle=%s order by id", v, db=db)


def temp(x: list[float]) -> Values:
    """cabin_temp_c read from a one-element box the test changes."""
    return lambda _: {"cabin_temp_c": x[0]}


async def test_edit_live_within_5s_and_alarm_carries_hash(tmp_path: Path) -> None:  # ALM-08
    m, v = mission_copy(tmp_path), vid()
    async with worker([v], mission_dir=m), stream_values(v, temp([22.0])):
        await until(lambda: bool(applied(v, SHIPPED.set_sha256)), 5)
        t = time.monotonic()
        edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 21.5")
        await until(lambda: bool(applied(v, set_formula(m))), 5)
        assert time.monotonic() - t < 5
        await until(lambda: bool(alarm_events(v)), 3)
    row = alarm_events(v)[0]
    assert (row["kind"], row["severity"], row["level"]) == ("RAISE", "YELLOW", "YELLOW_HIGH")
    assert row["limits_sha256"] == sha256_file(m / "limits.yaml")
    assert row["set_sha256"] == set_formula(m)


async def test_commands_only_edit_is_new_version(tmp_path: Path) -> None:  # EX-10, Q6
    m, v, x = mission_copy(tmp_path), vid(), [22.0]
    async with worker([v], mission_dir=m), stream_values(v, temp(x)):
        await until(lambda: bool(applied(v, SHIPPED.set_sha256)), 5)
        edit(m / "commands.yaml", "Close the cabin isolation valve", "Close the isolation valve")
        await until(lambda: bool(applied(v, set_formula(m))), 5)
        x[0] = 31.0
        await until(lambda: bool(alarm_events(v)), 3)
    (ev,) = applied(v, set_formula(m))
    assert ev["details"]["commands_sha256"] == sha256_file(m / "commands.yaml")
    assert ev["details"]["limits_sha256"] == SHIPPED.limits_sha256
    row = alarm_events(v)[0]
    assert (row["limits_sha256"], row["set_sha256"]) == (SHIPPED.limits_sha256, set_formula(m))


async def test_set_applied_after_two_equal_polls(tmp_path: Path) -> None:  # EX-09
    m, v = mission_copy(tmp_path), vid()
    async with worker([v], mission_dir=m), stream_values(v, temp([22.0])):
        await until(lambda: bool(applied(v, SHIPPED.set_sha256)), 5)
        edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 26.5")  # A'
        first = set_formula(m)
        await asyncio.sleep(0.3)
        edit(m / "limits.yaml", "yellow_high: 26.5", "yellow_high: 26.0")  # A'' (polls are 1 s)
        await until(lambda: bool(applied(v, set_formula(m))), 5)
    assert applied_sets(v) == [SHIPPED.set_sha256, set_formula(m)]
    assert rows("select 1 from limits_versions where set_sha256=%s", first) == []


async def test_invalid_reload_keeps_old_set(tmp_path: Path) -> None:  # D-ALM-12
    m, v, x = mission_copy(tmp_path), vid(), [22.0]
    async with worker([v], mission_dir=m), stream_values(v, temp(x)):
        await until(lambda: bool(applied(v, SHIPPED.set_sha256)), 5)
        edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 27.0, bogus: 1")
        bad = rejected_key(set_formula(m))
        await until(lambda: bool(rows("select 1 from events where key=%s", bad)), 5)
        x[0] = 31.0
        await until(lambda: bool(alarm_events(v)), 3)
    (rej,) = rows("select * from events where key=%s", bad)
    assert rej["vehicle"] is None and rej["details"]["file"] == "limits.yaml"
    assert "bogus" in rej["details"]["error"]
    row = alarm_events(v)[0]
    assert (row["limits_sha256"], row["set_sha256"]) == (SHIPPED.limits_sha256, SHIPPED.set_sha256)
    assert applied_sets(v) == [SHIPPED.set_sha256]


def stored_in(db: str, v: str) -> int:
    return len(rows("select 1 from telemetry where vehicle=%s", v, db=db))


def migrate(db: str, mdb: MissionDb) -> None:
    with psycopg.connect(owner_dsn(db), autocommit=True) as c:
        add_columns(c, mdb)


async def test_unmigrated_param_at_start_falls_back(tmp_path: Path) -> None:  # D-DB-11, R2-18
    m, v = mission_copy(tmp_path), vid()
    with (m / "parameters.yaml").open("a") as f:
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")
    with throwaway_db() as db:
        migrate(db, SHIPPED)
        async with await psycopg.AsyncConnection.connect(app_dsn(db), autocommit=True) as c:
            await insert_set(c, SHIPPED)
        async with worker([v], dsn=app_dsn(db), mission_dir=m), stream_values(v, temp([22.0])):
            await until(lambda: bool(applied(v, SHIPPED.set_sha256, db)), 5)  # the stored set
            assert rows("select 1 from events where key=%s", rejected_key(set_formula(m)), db=db)
            t = time.monotonic()
            migrate(db, load_dir(m))
            await until(lambda: bool(applied(v, set_formula(m), db)), 14)
            assert time.monotonic() - t <= 10 + 2 + 1.5  # revalidation + two polls + a batch
            n = stored_in(db, v)
            await until(lambda: stored_in(db, v) > n + 5, 3)  # frames keep flowing
        last = rows("select * from telemetry where vehicle=%s order by seq desc limit 1", v, db=db)
        assert "cabin_humidity_pct" in last[0] and last[0]["cabin_temp_c"] == 22.0


async def test_removed_roc_check_returns_to_normal(tmp_path: Path) -> None:  # R8 7-8
    m, v = mission_copy(tmp_path), vid()

    def leak(t: float) -> dict[str, float]:  # -0.4 kPa/s inside the limit band (98..104)
        return {"cabin_pressure_kpa": 103.5 - 0.4 * t}

    async with worker([v], mission_dir=m), stream_values(v, leak):
        await until(lambda: [r["check_kind"] for r in alarm_events(v)] == ["roc"], 5)
        edit(m / "limits.yaml", ROC_LINE + "\n", "")
        await until(lambda: len(alarm_events(v)) == 2, 5)
    raise_, rtn = alarm_events(v)
    assert (raise_["kind"], raise_["severity"]) == ("RAISE", "YELLOW")
    assert raise_["param"] == "cabin_pressure_kpa"
    assert (rtn["kind"], rtn["note"]) == ("RTN", "LIMITS_CHANGED")
    assert rtn["alarm_id"] == raise_["alarm_id"]
    assert rtn["set_sha256"] == set_formula(m)
    (applied_ev,) = applied(v, set_formula(m))
    assert rtn["trigger_seq"] == applied_ev["details"]["first_seq"]  # switched at that frame


def put(v: str, base: datetime, seqs: range) -> None:
    for q in seqs:
        xadd_raw(v, build_frame(v, q, base + timedelta(seconds=q / 10), NOMINAL))


async def test_edit_while_stopped_applied_at_first_batch(tmp_path: Path) -> None:  # D-ALM-12
    m, v, base = mission_copy(tmp_path), vid(), now() - timedelta(minutes=1)
    put(v, base, range(5))
    async with worker([v], mission_dir=m):
        await until(lambda: drained(v), 5)
    edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 26.0")
    async with worker([v], mission_dir=m):  # restart on the edited files, no new frame
        await asyncio.sleep(1.5)
    assert applied_sets(v) == [SHIPPED.set_sha256]  # nothing applied without a frame
    put(v, base, range(5, 8))
    async with worker([v], mission_dir=m):
        await until(lambda: drained(v), 5)
    assert applied_sets(v) == [SHIPPED.set_sha256, set_formula(m)]
    assert applied(v, set_formula(m))[0]["details"]["first_seq"] == 5
