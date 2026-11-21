"""The determinism recompute of the soak (D-REP-05 b, I3): the vehicle's whole stored history
folded from the initial state, ACKs and limit sets applied as recorded, compared to the recorded
transition keys. The fold is tests/support/recompute.py, the one
test_recompute_reproduces_recorded_keys uses."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pyarrow as pa

from chaos.collect import LIVE
from gs.archiver.window import window_hours
from gs.common.missiondb import load, load_dir
from tests.support.archive import app_conn
from tests.support.mission import MISSION_DIR
from tests.support.pg import rows
from tests.support.recompute import recompute_keys
from tests.support.s3 import s3_client


def first_frame(vehicle: str) -> datetime | None:
    q = (
        "select min(t) as t from (select min(t_onboard) as t from telemetry where vehicle = %s"
        " union all select min(t_min) from archive_object where vehicle = %s) x"
    )
    t: datetime | None = rows(q, vehicle, vehicle)[0]["t"]
    return t


async def _frames(vehicle: str, start: datetime, end: datetime) -> list[pa.Table]:
    async with app_conn() as c:
        return [
            t
            async for t in window_hours(
                c, s3_client("archiver"), "archive", vehicle, start, end,
                load_dir(MISSION_DIR).param_names,
            )
        ]  # fmt: skip


def recomputed_keys(vehicle: str) -> tuple[list[str], list[str]] | None:
    """(recomputed, recorded) transition keys of `vehicle`; None when it has no frames."""
    start = first_frame(vehicle)
    if start is None:
        return None
    sets = {
        r["set_sha256"]: load(
            r["limits_yaml"].encode(), r["commands_yaml"].encode(), r["parameters_yaml"].encode()
        )
        for r in rows("select * from limits_versions")
    }
    q = "select details from events where vehicle=%s and kind='LIMITS_APPLIED' order by id"
    applied = [(r["details"]["first_seq"], r["details"]["set_sha256"]) for r in rows(q, vehicle)]
    acks = rows("select * from alarm_events where vehicle=%s and kind='ACK' order by id", vehicle)
    frames = asyncio.run(_frames(vehicle, start, datetime.now(UTC)))
    keys, _ = recompute_keys(frames, acks, sets, vehicle, applied)
    recorded = rows("select key from alarm_events where vehicle=%s order by id", vehicle)
    return keys, [r["key"] for r in recorded]


def recompute_all(
    vehicles: Sequence[str] = LIVE,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(recorded, recomputed) per vehicle, for check_i3_recompute."""
    out: tuple[dict[str, list[str]], dict[str, list[str]]] = ({}, {})
    for v in vehicles:
        if got := recomputed_keys(v):
            out[1][v], out[0][v] = got
    return out
