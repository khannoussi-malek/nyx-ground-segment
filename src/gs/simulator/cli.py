"""gs-sim entry point: `gs-sim backfill ...` writes past hours into NYX-TA9; else the simulator."""

import sys

from gs.simulator import backfill
from gs.simulator.main import main as run_sim


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        raise SystemExit(backfill.main(sys.argv[2:]))
    run_sim()
