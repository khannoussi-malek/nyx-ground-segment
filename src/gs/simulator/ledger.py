"""The simulator's frame ledger (D-EXT-15): one JSON line per frame with what became of it."""

import json
from datetime import datetime
from pathlib import Path

from gs.common.time import iso_z

FATES = ("sent", "dropout", "malformed", "duplicate", "overflow")


class Ledger:
    """Appended to, never truncated: a simulator restart carries on in the same file."""

    def __init__(self, path: Path) -> None:
        self._f = path.open("a", buffering=1)  # line-buffered: the soak reads it while it grows

    def __call__(self, vehicle: str, seq: int, t: datetime, fate: str) -> None:
        assert fate in FATES, fate
        line = {"vehicle": vehicle, "seq": seq, "t": iso_z(t), "fate": fate}
        self._f.write(json.dumps(line) + "\n")

    def close(self) -> None:
        self._f.close()
