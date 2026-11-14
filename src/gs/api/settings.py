from dataclasses import dataclass
from pathlib import Path

from gs.common.config import require, vehicle_list


@dataclass(frozen=True)
class Settings:
    redis_url: str
    database_url: str
    session_secret: str
    users_file: Path
    mission_db: Path
    vehicles: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            redis_url=require("REDIS_URL"),
            database_url=require("DATABASE_URL"),
            session_secret=require("SESSION_SECRET"),
            users_file=Path(require("USERS_FILE")),
            mission_db=Path(require("MISSION_DB")),
            vehicles=vehicle_list(require("VEHICLES")),
        )
