"""checks_by_mode end to end (D-EXT-06): the simulator's mode picks the limit set."""

import asyncio
from typing import Any

from tests.support.env import until, vid
from tests.support.ingest import worker
from tests.support.pg import rows
from tests.support.sim import post_fault, post_mode, running_sim


def events(v: str) -> list[dict[str, Any]]:
    return rows("select * from alarm_events where vehicle=%s order by id", v)


async def test_switch_at_first_safe_frame() -> None:
    v = vid()
    async with worker([v]), running_sim(v) as port:
        t = asyncio.to_thread
        assert await t(post_mode, v, "SAFE", port) == 204
        assert await t(post_fault, v, "step", port, param="cabin_pressure_kpa", value=92.0,
                       duration_s=60) == 204  # fmt: skip
        await until(lambda: bool(events(v)), 10)
        first = events(v)[0]
        # 92 kPa is RED under the default set (red_low 95) but only YELLOW under SAFE (95 / 90)
        assert (first["severity"], first["level"], first["check_set"]) == (
            "YELLOW",
            "YELLOW_LOW · SAFE limits",
            "SAFE",
        )
        assert await t(post_mode, v, "TEST", port) == 204
        await until(lambda: any(e["kind"] == "ESCALATE" for e in events(v)), 10)
    esc = next(e for e in events(v) if e["kind"] == "ESCALATE")
    assert (esc["severity"], esc["level"], esc["check_set"]) == ("RED", "RED_LOW", "default")
    modes = rows("select distinct mode from telemetry where vehicle=%s", v)
    assert {m["mode"] for m in modes} == {"SAFE", "TEST"}
