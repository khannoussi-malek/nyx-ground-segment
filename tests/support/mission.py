"""Shipped mission-db bytes with per-file overrides, for loader tests."""

from pathlib import Path

from gs.common.missiondb import MissionDb, load

MISSION_DIR = Path(__file__).resolve().parents[2] / "mission"


def files(**over: bytes) -> dict[str, bytes]:
    base = {
        n: (MISSION_DIR / f"{n}.yaml").read_bytes() for n in ("limits", "commands", "parameters")
    }
    return {**base, **over}


def ld(f: dict[str, bytes]) -> MissionDb:
    return load(f["limits"], f["commands"], f["parameters"])
