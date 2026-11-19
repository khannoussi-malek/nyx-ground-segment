import os
from dataclasses import dataclass
from pathlib import Path

from gs.common.config import require, vehicle_list
from gs.common.s3_settings import S3Settings


@dataclass(frozen=True)
class Settings:
    redis_url: str
    database_url: str
    session_secret: str
    users_file: Path
    mission_db: Path
    vehicles: tuple[str, ...]
    ui_dist: Path
    s3: S3Settings  # the get-only `replay` MinIO user (T4-l, R2-19)
    test_controls: bool = False  # GS_TEST_CONTROLS=1: /api/test-controls forwards to the simulator
    sim_url: str = "http://simulator:8081"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            redis_url=require("REDIS_URL"),
            database_url=require("DATABASE_URL"),
            session_secret=require("SESSION_SECRET"),
            users_file=Path(require("USERS_FILE")),
            mission_db=Path(require("MISSION_DB")),
            vehicles=vehicle_list(require("VEHICLES")),
            ui_dist=Path(require("UI_DIST")),
            s3=S3Settings.from_env(),
            test_controls=os.environ.get("GS_TEST_CONTROLS") == "1",
            sim_url=os.environ.get("SIM_URL", "http://simulator:8081"),
        )
