"""run() wires a tick_loop + tc_loop per vehicle (one CommandBox each) and a send_loop (P-pf15)."""

import asyncio
from typing import Any

import pytest

import gs.simulator.main as main_mod
from gs.common.health import serve_health
from gs.simulator.commands import CommandBox
from gs.simulator.main import SimSettings, run
from tests.support.mission import MISSION_DIR


async def test_run_wires_tc_loop_and_send_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, list[tuple[Any, ...]]] = {"tick": [], "tc": [], "send": []}
    status: list[Any] = []

    def recorder(name: str) -> Any:
        async def fake(*args: Any, **kw: Any) -> None:
            calls[name].append((*args, kw))

        return fake

    async def serve(host: str, port: int, st: Any, routes: Any) -> asyncio.Server:
        status.append(st)
        return await serve_health(host, port, st, routes)

    monkeypatch.setattr(main_mod, "tick_loop", recorder("tick"))
    monkeypatch.setattr(main_mod, "tc_loop", recorder("tc"))
    monkeypatch.setattr(main_mod, "send_loop", recorder("send"))
    monkeypatch.setattr(main_mod, "serve_health", serve)
    vs = ("NYX-TA1", "NYX-TA2")
    await run(
        SimSettings("redis://localhost:1/0", vs, "1", "127.0.0.1", 0, MISSION_DIR), asyncio.Event()
    )

    assert [c[1] for c in calls["tc"]] == list(vs)
    assert [c[0] for c in calls["tick"]] == list(vs)
    [send] = calls["send"]
    buf = send[0]
    for v, tick, tcl in zip(vs, calls["tick"], calls["tc"], strict=True):
        box = tick[-1]["box"]
        assert isinstance(box, CommandBox) and tcl[2] is box  # the same box per vehicle
        assert tick[1] is buf and tcl[3] is buf  # acks and frames share the ordered buffer
        assert tcl[1] == v
    assert set(box.commands) == {"isolate_valve", "vent_cabin"}  # MISSION_DB loaded at start
    assert status[0]()[0] == "ok"
    calls["tick"][0][-1]["box"].schema_errors = 1
    assert status[0]()[0] == "degraded"
