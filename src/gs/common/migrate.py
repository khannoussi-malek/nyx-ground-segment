"""gs-migrate: add the mission-db parameter columns to the telemetry hypertable (D-DB-03)."""

import logging
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

from gs.common.config import require
from gs.common.logs import setup_logging
from gs.common.missiondb import MissionDb, load_dir


@dataclass(frozen=True)
class MigrateSettings:
    database_url: str
    mission_db: str

    @classmethod
    def from_env(cls) -> MigrateSettings:
        return cls(database_url=require("DATABASE_URL"), mission_db=require("MISSION_DB"))


def add_columns(conn: psycopg.Connection, db: MissionDb) -> list[str]:
    have = {
        r[0]
        for r in conn.execute(
            "select column_name from information_schema.columns where table_name='telemetry'"
        )
    }
    added = [n for n in db.param_names if n not in have]
    for name in added:
        conn.execute(
            sql.SQL("ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS {} double precision").format(
                sql.Identifier(name)
            )
        )
    return added


log = logging.getLogger(__name__)


def main() -> int:
    setup_logging("gs-migrate")
    s = MigrateSettings.from_env()
    mdb = load_dir(Path(s.mission_db))
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        added = add_columns(conn, mdb)
    log.info("added %d column(s): %s", len(added), ", ".join(added) or "-")
    return 0
