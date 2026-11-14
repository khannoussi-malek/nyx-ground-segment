"""EX-01, D-DB-11, D-EXT-01: adding a parameter is a mission/ edit plus gs-migrate only."""

import hashlib
import json
import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from gs.common import migrate
from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.interfaces.export import write_all
from gs.common.missiondb import load_dir
from gs.ingest.store import insert_frames
from gs.ingest.validate import classify
from gs.simulator.frames import make_frame
from tests.support.env import REPO, vid
from tests.support.gen import stale_files
from tests.support.pg import app_dsn, owner_dsn, throwaway_db

PARAM = "cabin_humidity_pct"


def tree_digest(*roots: str) -> str:
    h = hashlib.sha256()
    for p in sorted(p for r in roots for p in (REPO / r).rglob("*") if p.is_file()):
        if "__pycache__" not in p.parts:
            h.update(p.relative_to(REPO).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def stored_value(db: str, vehicle: str) -> float | None:
    with psycopg.connect(app_dsn(db)) as c:
        row = c.execute(f"select {PARAM} from telemetry where vehicle=%s", (vehicle,)).fetchone()
    assert row is not None
    return float(row[0]) if row[0] is not None else None


async def test_add_parameter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = tree_digest("src", "interfaces")
    m = tmp_path / "mission"
    shutil.copytree(REPO / "mission", m)
    append(
        m / "parameters.yaml",
        f"  {PARAM}: {{unit: '%', type: float, description: Cabin relative humidity}}\n",
    )
    append(
        m / "limits.yaml",
        f"  {PARAM}: {{checks: [{{type: limit, red_low: 10.0, yellow_low: 20.0, "
        "yellow_high: 60.0, red_high: 70.0, hysteresis_pct: 5}]}\n",
    )
    db = load_dir(m)
    assert db.param_names[-1] == PARAM
    vehicle, now = vid(), datetime.now(UTC)
    with throwaway_db() as name:
        monkeypatch.setenv("DATABASE_URL", owner_dsn(name))
        monkeypatch.setenv("MISSION_DB", str(m))
        assert migrate.main() == 0
        raw = {**json.loads(make_frame(vehicle, 0, now, random.Random(1))), PARAM: 41.5}
        frame = classify(
            {b"data": json.dumps(raw).encode()},
            f"tm:{vehicle}",
            "0-1",
            1,
            build_frame_model(db.param_names),
            now,
        )
        assert isinstance(frame, FrameHeader)
        async with await psycopg.AsyncConnection.connect(app_dsn(name)) as c, c.transaction():
            assert len(await insert_frames(c, [frame], db.param_names)) == 1
        assert stored_value(name, vehicle) == 41.5
    # no code change, and no generated file to commit (CI stale check stays green)
    write_all(tmp_path / "gen")
    assert stale_files(tmp_path / "gen") == []
    assert tree_digest("src", "interfaces") == before
