"""Worker → ui:events: alarm, link, resync and tm.frozen (Task 8, D-ING-22, D-UI-18, P-pf9)."""

import asyncio
import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any, NoReturn

import psycopg
import pytest
import redis.exceptions

from gs.common.hooks import Hooks
from gs.common.interfaces import FrameHeader
from gs.common.interfaces.sse import Link
from gs.common.uievents import publish_ui
from gs.common.views import alarm_views
from gs.ingest import worker as worker_mod
from gs.ingest.events import applied_key, link_key, reset_key, seq_key
from gs.ingest.store import insert_frames
from gs.ingest.worker import Timing
from gs.simulator.frames import build_frame
from tests.support.env import now, until, vid
from tests.support.ingest import worker
from tests.support.pg import admin_dsn, alarm_events_for, app_dsn, events_for
from tests.support.redisx import (
    NOMINAL,
    alarm_msgs,
    drained,
    produce,
    produce_values,
    stream_entries,
    validate_ui_event,
    xadd_raw,
)

RED_STEP = {"cabin_temp_c": [22.0] * 5 + [31.0] * 5}


def msgs_for(v: str) -> list[dict[str, Any]]:
    """Every ui:events message about vehicle v (any kind), in stream order."""
    msgs = [json.loads(f["data"]) for _, f in stream_entries("ui:events")]
    return [m for m in msgs if (m.get("alarm") or m.get("event") or m).get("vehicle") == v]


def link_keys(v: str) -> list[str]:
    return [m["event"]["key"] for m in msgs_for(v) if m["kind"] == "link"]


def tms(v: str) -> list[dict[str, Any]]:
    return [m for m in msgs_for(v) if m["kind"] == "tm"]


async def test_alarm_message_after_red_steps() -> None:
    v = vid()
    produce_values(v, RED_STEP)
    async with worker([v]):
        await until(lambda: bool(alarm_msgs(v)), 5)
    (m,) = alarm_msgs(v)
    a = m["alarm"]
    assert (a["severity"], a["last"]["alert"], a["last"]["value"]) == ("RED", True, 31.0)
    assert (a["state"], a["level"], a["value"]) == ("UNACK", "RED_HIGH", 31.0)
    validate_ui_event(m)


async def test_roc_alarm_carries_the_slope() -> None:  # P-pf25: kPa/s, not kPa
    v = vid()
    produce_values(v, {"cabin_pressure_kpa": [101.3 - 0.04 * i for i in range(20)]})
    async with worker([v]):
        await until(lambda: bool(alarm_msgs(v)), 5)
    (m,) = alarm_msgs(v)
    a = m["alarm"]
    assert (a["trigger_check"], a["level"]) == ("roc", "YELLOW_FALLING")
    assert a["value"] == a["last"]["value"] == pytest.approx(-0.4, abs=1e-6)
    (raise_,) = alarm_events_for(v)
    assert raise_["value"] == pytest.approx(-0.4, abs=1e-6)


async def test_alarm_value_stored() -> None:
    v = vid()
    produce_values(v, RED_STEP)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    (raise_,) = alarm_events_for(v)
    assert (raise_["kind"], raise_["value"], raise_["trigger_seq"]) == ("RAISE", 31.0, 7)


async def test_link_message_for_seq_jump() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {"cabin_temp_c": [22.0] * 5}, base=base)
    xadd_raw(v, build_frame(v, 1005, base + timedelta(milliseconds=500), NOMINAL))
    async with worker([v]):
        await until(lambda: drained(v), 5)
    (jump,) = events_for(v, "SEQ_JUMP")
    assert jump["key"] == seq_key("SEQ_JUMP", v, base, 5)
    jumps = [m for m in msgs_for(v) if m["kind"] == "link" and m["event"]["kind"] == "SEQ_JUMP"]
    assert [m["event"]["key"] for m in jumps] == [jump["key"]]
    assert jumps[0]["event"]["details"]["epoch"].endswith("Z")
    assert applied_key(v, base, 0) in link_keys(v)  # LIMITS_APPLIED of the first frame too
    for m in jumps:
        validate_ui_event(m)


async def test_resync_first_on_start() -> None:  # D-UI-18
    v = vid()
    produce(v, 5)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    first, *rest = msgs_for(v)
    assert first == {"kind": "resync", "vehicle": v}
    assert [m["kind"] for m in rest].count("resync") == 0 and len(tms(v)) == 5
    validate_ui_event(first)


async def test_tm_frozen_flag() -> None:
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {"cabin_temp_c": [22.0] * 26}, base=base)  # 2.5 s of identical values
    async with worker([v]):
        await until(lambda: drained(v), 5)
        assert tms(v)[-1]["frozen"] is True
        produce_values(v, {"cabin_temp_c": [23.0, 24.0]}, base=base, start_seq=26)
        await until(lambda: len(tms(v)) == 28, 5)
    assert tms(v)[-1]["frozen"] is False


async def test_tm_stale_ms_follows_the_source_period() -> None:  # R22 F2, console part
    fast, slow, base = vid(), vid(), now() - timedelta(minutes=5)
    produce_values(fast, {"cabin_temp_c": [22.0 + i % 2 for i in range(30)]}, base=base)
    for seq in range(40):  # one frame per 3 s: the smoothed period settles near 3 s
        t = base + timedelta(seconds=3 * seq)
        xadd_raw(slow, build_frame(slow, seq, t, NOMINAL | {"cabin_temp_c": 22.0 + seq % 2}))
    async with worker([fast, slow]):
        await until(lambda: drained(fast) and drained(slow), 10)
    assert {m["stale_ms"] for m in tms(fast)} == {2000}  # 10 Hz: unchanged
    assert 10_000 < tms(slow)[-1]["stale_ms"] <= 15_000  # 5 x the settled ~3 s period
    validate_ui_event(tms(slow)[-1])


async def test_link_message_for_los() -> None:  # P-pf9: the _silence path
    v = vid()
    produce(v, 5)
    async with worker([v], timing=Timing(los_s=0.5)):
        await until(lambda: any(k.startswith(f"LOS/{v}/") for k in link_keys(v)), 5)
    (los,) = events_for(v, "LOS")
    assert los["key"] in link_keys(v)


async def test_link_message_for_engine_state_reset() -> None:  # P-pf9: the load_state path
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {"cabin_temp_c": [22.0] * 5}, base=base)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    with psycopg.connect(admin_dsn("gs")) as c:
        c.execute("update engine_state set schema_version = -1 where vehicle=%s", (v,))
    async with worker([v]):
        await until(lambda: reset_key(v, base, 4) in link_keys(v), 5)
    seen = [m.get("event", m)["key"] if m["kind"] == "link" else m["kind"] for m in msgs_for(v)]
    assert seen.count("resync") == 2  # once per start
    second = len(seen) - 1 - seen[::-1].index("resync")
    assert seen.index(reset_key(v, base, 4)) == second + 1  # published with the second resync


async def test_link_messages_from_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # P-pf9: _isolate's commits, incl. pending-only
    produce_values(v, {"cabin_temp_c": [22.0] * 6}, base=base)

    async def bad_last(conn: Any, frames: list[FrameHeader], params: Any) -> Any:
        if any(f.seq == 5 for f in frames):
            raise ValueError("always bad")
        return await insert_frames(conn, frames, params)

    monkeypatch.setattr(worker_mod, "insert_frames", bad_last)
    async with worker([v]):
        await until(lambda: drained(v), 5)
    (poisoned,) = events_for(v, "POISON")
    assert {applied_key(v, base, 0), poisoned["key"]} <= set(link_keys(v))


async def test_ground_events_logged_with_vehicle_and_seq(caplog: pytest.LogCaptureFixture) -> None:
    v, base = vid(), now() - timedelta(minutes=1)  # ENG-05 (P-pf9)
    produce_values(v, {"cabin_temp_c": [22.0] * 5}, base=base)
    xadd_raw(v, build_frame(v, 1005, base + timedelta(milliseconds=500), NOMINAL))
    with caplog.at_level(logging.INFO, logger=worker_mod.__name__):
        async with worker([v]):
            await until(lambda: drained(v), 5)
    (rec,) = [r for r in caplog.records if "SEQ_JUMP" in r.getMessage()]
    assert (rec.__dict__["vehicle"], rec.__dict__["seq"]) == (v, 1005)


async def test_los_link_republished_after_publish_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis error after the LOS commit: the committed LOS is published on the next pass and
    the vehicle is in LOS, so a frame arriving meanwhile gives its AOS (review of 3197b60)."""
    v, base = vid(), now() - timedelta(minutes=1)
    produce_values(v, {"cabin_temp_c": [22.0] * 5}, base=base)
    failed: list[bool] = []

    async def lose_first_los(r: Any, msgs: Any) -> None:
        if not failed and any(isinstance(m, Link) and m.event.kind == "LOS" for m in msgs):
            failed.append(True)  # a frame lands in the backoff window, 100 ms after seq 4
            xadd_raw(v, build_frame(v, 5, base + timedelta(milliseconds=500), NOMINAL))
            raise redis.exceptions.ConnectionError("test: publish lost")
        await publish_ui(r, msgs)

    monkeypatch.setattr(worker_mod, "publish_ui", lose_first_los)
    los, aos = link_key("LOS", v, base, 4), link_key("AOS", v, base, 4)
    async with worker([v], timing=Timing(los_s=0.5)):
        await until(lambda: bool(failed) and {los, aos} <= set(link_keys(v)), 5)
    assert aos in {e["key"] for e in events_for(v, "AOS")}


async def test_crash_after_commit_before_publish_resync_covers(tmp_path: Path) -> None:
    """D-UI-18: a crash between COMMIT and publish leaves no alarm or link message; the
    restart's resync tells clients to reload, and the reloaded view holds the alarm."""
    v = vid()
    produce_values(v, RED_STEP)
    hooks = Hooks(crash_at="after_commit_before_publish", marker_dir=tmp_path, exit=_crash)
    with pytest.raises(_Crash):
        async with worker([v], hooks=hooks) as w:
            await asyncio.wait_for(w.tasks[0], 5)
    assert [m["kind"] for m in msgs_for(v)] == ["resync"]  # the first start's only
    async with worker([v]):
        await until(lambda: drained(v), 5)
    kinds = [m["kind"] for m in msgs_for(v)]
    assert kinds.count("resync") == 2 and "alarm" not in kinds  # the redelivery is a duplicate
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as c:
        (a,) = await alarm_views(c, vehicle=v)
    assert (a.severity, a.state, a.last.alert, a.value) == ("RED", "UNACK", True, 31.0)


class _Crash(BaseException):
    pass


def _crash(_: int) -> NoReturn:
    raise _Crash
