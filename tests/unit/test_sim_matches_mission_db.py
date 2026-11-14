from pathlib import Path

from gs.common.missiondb import LimitCheck, load_dir
from gs.simulator.frames import NOMINALS


def test_nominals_match_mission_db() -> None:  # D-SIM-11, D-SIM-08
    db = load_dir(Path("mission"))
    assert set(NOMINALS) == set(db.param_names)
    for p, (nominal, _) in NOMINALS.items():
        lim = next(c for c in db.limits[p].checks if isinstance(c, LimitCheck))
        assert lim.yellow_low is None or lim.yellow_low < nominal
        assert lim.yellow_high is None or nominal < lim.yellow_high
    assert NOMINALS["cabin_pressure_kpa"][1] <= 0.027
