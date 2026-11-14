import subprocess
from pathlib import Path

import psycopg
import pytest

from gs.common.migrate import add_columns, main
from gs.common.missiondb import load_dir
from tests.support.env import REPO
from tests.support.pg import app_dsn, column_types, owner_dsn, throwaway_db

MISSION = Path("mission")


def test_adds_registry_columns_idempotently() -> None:  # D-DB-03
    mdb = load_dir(MISSION)
    with throwaway_db() as db, psycopg.connect(owner_dsn(db), autocommit=True) as c:
        assert add_columns(c, mdb) == list(mdb.param_names)
        assert column_types(c, "telemetry")["cabin_pressure_kpa"] == "double precision"
        assert add_columns(c, mdb) == []


def test_app_role_cannot_alter() -> None:  # D-DB-11 role design
    with (
        throwaway_db() as db,
        psycopg.connect(app_dsn(db)) as c,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        add_columns(c, load_dir(MISSION))


def test_main_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with throwaway_db() as db:
        monkeypatch.setenv("DATABASE_URL", owner_dsn(db))
        monkeypatch.setenv("MISSION_DB", "mission")
        assert main() == 0
        with psycopg.connect(owner_dsn(db)) as c:
            assert "cabin_pressure_kpa" in column_types(c, "telemetry")


def test_main_fails_fast_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(Exception, match="DATABASE_URL"):
        main()


def test_app_image_builds_and_migrate_service_runs() -> None:  # R16: builds nyx-gs:local
    r = subprocess.run(
        ["docker", "compose", "run", "--rm", "--build", "migrate"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stderr
