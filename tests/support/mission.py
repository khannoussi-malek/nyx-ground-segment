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


def calibrated(table: str = "[[0, 0], [10, 100]]", param: str = "bus_current_a") -> MissionDb:
    """The shipped mission with a calibration table on one raw parameter (tmp, never mission/)."""
    p = files()["parameters"].decode()
    line = next(x for x in p.splitlines() if x.lstrip().startswith(f"{param}:"))
    return ld(
        files(
            parameters=p.replace(line, line[:-1] + f", calibration: {{table: {table}}}}}").encode()
        )
    )
