"""Prepare NYX-TA9 for e2e/replay.spec.ts: two backfilled hours (A with a leak and a 10 s dropout,
B plain), both archived, B's object corrupted. Prints one JSON line. `make up` first.

    uv run python -m tests.support.replay_fixture
"""

import io
import json
from datetime import datetime, timedelta

from gs.archiver.hours import object_key
from tests.support.archive import TA9, archive_rows, backfill_ta9, next_ta9_window
from tests.support.env import wait_until
from tests.support.keys import trim_if_drained
from tests.support.pg import rows
from tests.support.redisx import client
from tests.support.s3 import s3_client

FRAMES_A = 36_000 - 100  # the 10 s dropout never sends 100 frames
FRAMES_B = 36_000
WINDOW = timedelta(minutes=20)
CLOSED = ("CLEAR",)  # an RTN stays open (RTN_UNACK) until acknowledged


def open_params(t_from: datetime, t_to: datetime) -> list[str]:
    """Params of the alarms still open at t_to, from the live alarm_events in the window."""
    ev = rows(
        "select alarm_id, param, kind from alarm_events where vehicle=%s"
        " and trigger_t_onboard >= %s and trigger_t_onboard < %s order by id", TA9, t_from, t_to,
    )  # fmt: skip
    last = {e["alarm_id"]: e for e in ev}
    return sorted({e["param"] for e in last.values() if e["kind"] not in CLOSED})


def main() -> None:
    a, b_end = next_ta9_window(2)
    b = a + timedelta(hours=1)
    backfill_ta9(
        a, b, "--fault", 'leak@600:{"duration_s":120}', "--fault", 'dropout@900:{"duration_s":10}'
    )
    backfill_ta9(b, b_end)

    # the archiver (300 s loop) deletes the hot rows once an hour is verified, so the archived row
    # count, not the hot count, is the proof that every frame arrived
    def archived() -> dict[datetime, int]:
        return {r["hour"]: r["rows"] for r in archive_rows(TA9)}

    wait_until(lambda: archived().get(a) == FRAMES_A and archived().get(b) == FRAMES_B, 1200)
    trim_if_drained(client("admin"), f"tm:{TA9}")  # the replay only needs the archive now
    root = s3_client("root")
    data = bytearray(root.get_object("archive", object_key(TA9, b)).read())
    data[len(data) // 2] ^= 0xFF
    root.put_object("archive", object_key(TA9, b), io.BytesIO(bytes(data)), len(data))
    t_to = a + WINDOW
    fmt = "%Y-%m-%dT%H:%M"
    out: dict[str, object] = {
        "a": a.strftime(fmt),
        "a_to": t_to.strftime(fmt),
        "b": b.strftime(fmt),
    }
    out |= {"b_to": (b + WINDOW).strftime(fmt), "open_params": open_params(a, t_to)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
