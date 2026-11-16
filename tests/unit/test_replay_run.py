"""replay_messages on a fake window and fake recorded rows, with a sleep that only records."""

import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa
import pytest
from jsonschema import Draft202012Validator

from gs.archiver.window import ArchiveCorrupt
from gs.common.interfaces import (
    ReplayAlarm,
    ReplayClock,
    ReplayEnd,
    ReplayLink,
    ReplayStart,
    ReplayTm,
)
from gs.common.interfaces.sse import EventView
from gs.common.missiondb import load_dir
from gs.replay import run
from gs.replay.run import CLOCK_STEP_S, replay_messages
from tests.support.mission import MISSION_DIR
from tests.support.redisx import load_schema

MDB = load_dir(MISSION_DIR)
V = "NYX-TA1"
T0 = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)
P = "cabin_temp_c"
VALIDATOR = Draft202012Validator(load_schema("sse/replay"))


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def table(times_s: Sequence[float], first_seq: int = 0) -> pa.Table:
    n = len(times_s)
    cols: dict[str, pa.Array] = {
        "vehicle": pa.array([V] * n),
        "t_onboard": pa.array([at(s) for s in times_s], pa.timestamp("us", tz="UTC")),
        "seq": pa.array(range(first_seq, first_seq + n), pa.int64()),
        "t_received": pa.array([at(s) for s in times_s], pa.timestamp("us", tz="UTC")),
        "mode": pa.array(["TEST"] * n),
        "last_cmd_id": pa.array([None] * n, pa.string()),
    }
    for p in MDB.param_names:
        cols[p] = pa.array([20.0 + i for i in range(n)], pa.float64())
    return pa.table(cols)


def alarm_row(id_: int, kind: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_, "key": f"k{id_}", "alarm_id": UUID(int=1), "vehicle": V, "param": P,
        "kind": kind, "check_kind": "limit", "level": "YELLOW_HIGH", "severity": "YELLOW",
        "value": 28.0, "trigger_seq": None, "trigger_t_onboard": None, "after_seq": None,
        "after_t_onboard": None, "by_user": "system", "note": None, "alert": False,
        "limits_sha256": MDB.limits_sha256, "set_sha256": MDB.set_sha256,
        "opened_at": at(0.2), "closed_at": None, "closes": False, "trigger_check": "limit",
    }  # fmt: skip
    return base | over


class Fake:
    def __init__(
        self,
        tables: Sequence[pa.Table],
        alarms: Sequence[dict[str, Any]] = (),
        events: Sequence[dict[str, Any]] = (),
        corrupt_after: int | None = None,
    ) -> None:
        self.tables, self.alarms, self.events = tables, alarms, events
        self.corrupt_after = corrupt_after
        self.sleeps: list[float] = []

    async def window_hours(self, *_: Any) -> AsyncIterator[pa.Table]:
        for i, t in enumerate(self.tables):
            if i == self.corrupt_after:
                raise ArchiveCorrupt(V, T0, "aa", "bb")
            yield t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(f: Fake) -> Fake:
        async def rows(*_: Any) -> Sequence[dict[str, Any]]:
            return f.alarms

        async def events(*_: Any) -> list[EventView]:
            return [EventView(**{k: v for k, v in e.items() if k != "id"}) for e in f.events]

        async def none(*_: Any) -> list[Any]:
            return []

        async def closed(*_: Any) -> bool:
            return False

        monkeypatch.setattr(run, "window_hours", f.window_hours)
        monkeypatch.setattr(run, "recorded_alarm_rows", rows)
        monkeypatch.setattr(run, "recorded_event_rows", events)
        monkeypatch.setattr(run, "initial_alarms", none)
        monkeypatch.setattr(run, "los_open_at", closed)
        return f

    return install


async def collect(f: Fake, t_from: datetime, t_to: datetime, speed: int = 1) -> list[Any]:
    return [
        m
        async for m in replay_messages(
            None, None, "archive", MDB, uuid4(), V, t_from, t_to, speed,  # type: ignore[arg-type]
            sleep=f.sleep,
        )
    ]  # fmt: skip


async def test_order_and_shapes(fake: Any) -> None:
    f = fake(
        Fake(
            [table([0.0, 0.1, 0.2, 0.3, 0.4])],
            alarms=[
                alarm_row(1, "RAISE", trigger_seq=2, trigger_t_onboard=at(0.2), alert=False),
                alarm_row(
                    2,
                    "ACK",
                    check_kind=None,
                    level=None,
                    value=None,
                    by_user="fc1",
                    note="seen",
                    after_seq=3,
                    after_t_onboard=at(0.3),
                ),
            ],  # fmt: skip
            events=[
                {
                    "id": 7,
                    "key": "GAP/x",
                    "vehicle": V,
                    "kind": "GAP",
                    "t_onboard": at(0.1),
                    "details": {"lost": 1},
                }
            ],  # fmt: skip
        )
    )
    msgs = await collect(f, T0, at(0.5))
    shapes = [type(m).__name__ for m in msgs if not isinstance(m, ReplayClock)]
    assert shapes == [
        "ReplayStart", "ReplayTm", "ReplayTm", "ReplayLink", "ReplayTm", "ReplayAlarm", "ReplayTm",
        "ReplayAlarm", "ReplayTm", "ReplayEnd",
    ]  # fmt: skip
    for m in msgs:
        assert m.replay is True
        VALIDATOR.validate(json.loads(m.model_dump_json()))
    start, end = msgs[0], msgs[-1]
    assert isinstance(start, ReplayStart) and (start.alarms, start.los_open) == ([], False)
    assert isinstance(end, ReplayEnd) and (end.reason, end.detail) == ("done", None)
    tms = [m for m in msgs if isinstance(m, ReplayTm)]
    assert [m.seq for m in tms] == [0, 1, 2, 3, 4]
    assert tms[0].values[P] == 20.0 and tms[0].last_cmd_id is None
    assert [m.severity for m in tms] == [{}, {}, {}, {P: "YELLOW"}, {P: "YELLOW"}]
    alarms = [m.alarm for m in msgs if isinstance(m, ReplayAlarm)]
    assert [(a.state, a.last.kind, a.last.t) for a in alarms] == [
        ("UNACK", "RAISE", at(0.2)), ("ACKED", "ACK", at(0.3))
    ]  # fmt: skip
    assert alarms[1].level == "YELLOW_HIGH" and alarms[1].value == 28.0  # kept after the ACK
    (link,) = [m for m in msgs if isinstance(m, ReplayLink)]
    assert link.event.key == "GAP/x"


async def test_pacing_by_t_onboard_at_10x(fake: Any) -> None:
    f = fake(Fake([table([i / 10 for i in range(600)])]))  # 60 s of 10 Hz frames
    msgs = await collect(f, T0, at(60.0), speed=10)
    assert sum(f.sleeps) == pytest.approx(6.0)
    assert max(f.sleeps) <= CLOCK_STEP_S
    assert sum(isinstance(m, ReplayTm) for m in msgs) == 600


async def test_clock_advances_through_dropout(fake: Any) -> None:
    f = fake(Fake([table([0.0, 10.0])]))
    msgs = await collect(f, T0, at(10.1))
    i0, i1 = [i for i, m in enumerate(msgs) if isinstance(m, ReplayTm)]
    clocks = msgs[i0 + 1 : i1]
    assert len(clocks) == 100 and all(isinstance(c, ReplayClock) for c in clocks)
    ts = [c.t for c in clocks]
    assert ts == sorted(set(ts)) and ts[0] == at(0.1) and ts[-1] == at(10.0)
    assert f.sleeps[:100] == [CLOCK_STEP_S] * 100


async def test_corrupt_hour_ends_stream(fake: Any) -> None:
    f = fake(Fake([table([0.0, 0.1]), table([3600.0], first_seq=2)], corrupt_after=1))
    msgs = await collect(f, T0, at(7200.0), speed=10)
    assert [m.seq for m in msgs if isinstance(m, ReplayTm)] == [0, 1]
    end = msgs[-1]
    assert isinstance(end, ReplayEnd)
    assert (end.reason, end.detail) == ("archive_corrupt", "archive object failed hash check")


async def test_recorded_rows_between_hours_and_after_last_frame(fake: Any) -> None:
    f = fake(
        Fake(
            [table([3599.0]), table([3601.0], first_seq=1)],
            events=[
                {
                    "key": "LOS/x",
                    "vehicle": V,
                    "kind": "LOS",
                    "t_onboard": at(3600.0),
                    "details": {},
                },
                {
                    "key": "AOS/x",
                    "vehicle": V,
                    "kind": "AOS",
                    "t_onboard": at(3602.0),
                    "details": {},
                },
            ],
        )
    )
    msgs = await collect(f, at(3599.0), at(3603.0), speed=10)
    assert [m.kind for m in msgs if not isinstance(m, ReplayClock)] == [
        "replay_start",
        "tm",
        "link",
        "tm",
        "link",
        "replay_end",
    ]
    assert sum(f.sleeps) == pytest.approx(0.4)


async def test_event_only_window_advances_to_end(fake: Any) -> None:
    f = fake(
        Fake(
            [],
            events=[
                {"key": "LOS/x", "vehicle": V, "kind": "LOS", "t_onboard": at(1.0), "details": {}},
            ],
        )
    )
    msgs = await collect(f, T0, at(2.0), speed=10)
    assert [m.kind for m in msgs] == ["replay_start", "clock", "link", "clock", "replay_end"]
    assert sum(f.sleeps) == pytest.approx(0.2)
