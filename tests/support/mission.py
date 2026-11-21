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


def calibrated(
    table: str = "[[0, 0], [10, 100]]", param: str = "bus_current_a", no_limits: bool = False
) -> MissionDb:
    """The shipped mission with a calibration table on one raw parameter (tmp, never mission/);
    `no_limits` drops that parameter's limits entry."""
    p = files()["parameters"].decode()
    line = next(x for x in p.splitlines() if x.lstrip().startswith(f"{param}:"))
    over = {
        "parameters": p.replace(line, f"{line[:-1]}, calibration: {{table: {table}}}}}").encode()
    }
    if no_limits:
        lim = files()["limits"].decode().splitlines(keepends=True)
        over["limits"] = "".join(x for x in lim if not x.startswith(f"  {param}:")).encode()
    return ld(files(**over))
