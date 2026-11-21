"""GS-EXT-01 (a), EX-01: a raw parameter, a derived parameter and a SAFE-mode limit are added by
editing only mission/ files (a tmp copy); they are stored, derived, charted and alarmed with no
code change and no generated file to commit."""

import json
import random
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from gs.alarms.contract import Level
from gs.alarms.engine import compile_set
from gs.common import migrate
from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.interfaces.export import write_all
from gs.common.missiondb import load_dir
from gs.ingest.compute import compute
from gs.ingest.store import insert_frames
from gs.ingest.validate import classify
from gs.simulator.frames import make_frame
from tests.meta.test_add_parameter import append, tree_digest
from tests.support.alarms import Frame, run_engine
from tests.support.env import REPO, vid
from tests.support.gen import stale_files
from tests.support.missionfiles import add_raw_parameter, edit, mission_copy
from tests.support.pg import app_dsn, owner_dsn, throwaway_db

RAW, DERIVED = "cabin_humidity_pct", "power2_w"
LIMIT = "{type: limit, red_low: %s, yellow_low: 20.0, yellow_high: 60.0, red_high: 70.0}"


def extended(tmp_path: Path) -> Path:
    m = mission_copy(tmp_path)
    add_raw_parameter(m, f"  {RAW}: {{unit: '%', type: float, description: Cabin humidity}}")
    edit(
        m / "parameters.yaml",
        "cabin_leak_rate_kpa_s:",
        f"{DERIVED}: {{unit: W, description: Twice the bus power, expr: power_w * 2}}\n"
        "  cabin_leak_rate_kpa_s:",
    )
    append(
        m / "limits.yaml",
        f"  {RAW}: {{checks: [{LIMIT % 10.0}], checks_by_mode: {{SAFE: [{LIMIT % 16.0}]}}}}\n"
        f"  {DERIVED}: {{checks: [{{type: limit, yellow_high: 500.0, red_high: 600.0, "
        "hysteresis_abs: 9.0}]}\n",
    )
    return m


async def test_raw_derived_and_mode_limit_by_mission_edit_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tree_digest(REPO, "src", "interfaces", "mission")
    m = extended(tmp_path)
    db = load_dir(m)
    assert RAW in db.raw_names and DERIVED in db.derived_names  # derived after power_w
    vehicle, now = vid(), datetime.now(UTC)
    with throwaway_db() as name:
        monkeypatch.setenv("DATABASE_URL", owner_dsn(name))
        monkeypatch.setenv("MISSION_DB", str(m))
        assert migrate.main() == 0  # make migrate: the new columns only
        raw = {**json.loads(make_frame(vehicle, 0, now, random.Random(1))), RAW: 41.5}
        model = build_frame_model(db.raw_names)
        assert RAW in model.model_fields  # charted: in the frame schema served at runtime
        frame = classify({b"data": json.dumps(raw).encode()}, f"tm:{vehicle}", "0-1", 1, model, now)
        assert isinstance(frame, FrameHeader)
        async with await psycopg.AsyncConnection.connect(app_dsn(name)) as c, c.transaction():
            await insert_frames(c, [frame], [compute(db, frame)[0]], db)
        with psycopg.connect(app_dsn(name)) as sync:
            row = sync.execute(
                f"select {RAW}, {DERIVED}, power_w from telemetry where vehicle=%s",  # noqa: S608
                (vehicle,),
            ).fetchone()
    assert row is not None and row[0] == 41.5 and row[2] is not None
    assert row[1] == row[2] * 2  # derived, bit-equal to its expression
    assert RAW in db.param_names and DERIVED in db.param_names
    cset = compile_set(db)
    frames: list[Frame] = [(i, i * 0.1, {DERIVED: 650.0, RAW: 15.0}) for i in range(1, 4)]
    tr = run_engine(cset, frames)  # derived parameter alarmed; 15 is only YELLOW by default ...
    assert {(t.param, t.severity) for t in tr} == {(DERIVED, Level.RED), (RAW, Level.YELLOW)}
    safe = run_engine(cset, frames, mode="SAFE")  # ... and RED under the SAFE limit set
    assert {(t.param, t.severity) for t in safe} == {(DERIVED, Level.RED), (RAW, Level.RED)}
    write_all(tmp_path / "gen")
    assert stale_files(tmp_path / "gen") == []  # nothing generated to commit (EX-01)
    assert tree_digest(REPO, "src", "interfaces", "mission") == before
