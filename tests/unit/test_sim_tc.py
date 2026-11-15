"""Simulator command path: parse, one per tick, pause, hold_exec, dropout (D-SIM-06/07/10)."""

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from gs.common.interfaces.streams import Tc, TcAck
from gs.common.missiondb import load_dir
from gs.simulator.buffer import FrameBuffer
from gs.simulator.commands import CommandBox, parse_tc, tc_loop
from gs.simulator.faults import FaultBoard
from tests.support.mission import MISSION_DIR
from tests.support.sim import EPOCH, at_tick, fault_req, tc, tick_pairs

V = "NYX-TA1"
COMMANDS = load_dir(MISSION_DIR).commands


def box_with(*cmds: Tc) -> CommandBox:
    box = CommandBox(COMMANDS)
    for c in cmds:
        box.inbox.put_nowait(c)
    return box


def frames_and_acks(
    pairs: list[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[tuple[int, dict[str, Any]]]]:
    """Frames in order, and each tcack with the index of the frame it followed."""
    frames: list[dict[str, Any]] = []
    acks: list[tuple[int, dict[str, Any]]] = []
    for stream, data in pairs:
        if stream == f"tm:{V}":
            frames.append(json.loads(data))
        else:
            assert stream == f"tcack:{V}"
            TcAck.model_validate_json(data)  # every ack is a valid TcAck
            acks.append((len(frames) - 1, json.loads(data)))
    return frames, acks


def first_seen(frames: list[dict[str, Any]], cmd_id: UUID) -> int:
    return next(i for i, f in enumerate(frames) if f["last_cmd_id"] == str(cmd_id))


# ---- parse_tc
def test_valid_tc_parses() -> None:
    c = tc("isolate_valve", valve="A")
    assert parse_tc(c.model_dump_json()) == c


def test_invalid_tc_nacked_with_schema_code() -> None:  # D-ING-27, requirements name
    u = uuid4()
    got = parse_tc(json.dumps({"cmd_id": str(u), "name": "vent_cabin"}))
    assert isinstance(got, tuple) and got[0] == u
    assert "issued_by" in got[1]


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"name": "vent_cabin", "args": {}}),
        json.dumps({"cmd_id": "not-a-uuid"}),
        json.dumps({"cmd_id": 7}),
        json.dumps(["cmd_id"]),
        '{"cmd_id": ',
        "",
        "[" * 100_000,
    ],
)
def test_tc_without_cmd_id_is_unreadable(raw: str) -> None:
    got = parse_tc(raw)
    assert isinstance(got, tuple) and got[0] is None and got[1]


# ---- tc_loop against a fake Redis
class FakeRedis:
    def __init__(self, entries: list[tuple[str, dict[bytes, bytes]]], stop: asyncio.Event) -> None:
        self.entries, self.stop = entries, stop
        self.groups: list[tuple[str, str, str, bool]] = []
        self.acked: list[str] = []

    async def xgroup_create(self, stream: str, group: str, id: str, mkstream: bool) -> None:
        self.groups.append((stream, group, id, mkstream))

    async def xreadgroup(
        self, group: str, consumer: str, streams: dict[str, str], count: int, block: int
    ) -> list[Any]:
        assert (group, consumer, count, block) == ("sim", "sim", 1, 1000)
        assert streams == {f"tc:{V}": ">"}
        if not self.entries:
            self.stop.set()
            return []
        return [[f"tc:{V}".encode(), [self.entries.pop(0)]]]

    async def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append(entry_id)


def run_tc_loop(raws: list[bytes]) -> tuple[FakeRedis, CommandBox, FrameBuffer]:
    stop, box, buf = asyncio.Event(), CommandBox(COMMANDS), FrameBuffer()
    r = FakeRedis([(f"{i}-0", {b"data": raw}) for i, raw in enumerate(raws)], stop)
    asyncio.run(tc_loop(r, V, box, buf, stop))  # type: ignore[arg-type]
    return r, box, buf


def test_tc_loop_queues_valid_and_nacks_invalid() -> None:
    good, bad = tc("isolate_valve", valve="A"), uuid4()
    r, box, buf = run_tc_loop(
        [
            good.model_dump_json().encode(),
            json.dumps({"cmd_id": str(bad), "name": "vent_cabin"}).encode(),
            b'{"no": "id"}',
        ]
    )
    assert r.groups == [(f"tc:{V}", "sim", "$", True)]  # MKSTREAM, old commands never run
    assert r.acked == ["0-0", "1-0", "2-0"]  # XACKed either way
    assert box.inbox.get_nowait() == good and box.inbox.empty()
    assert box.schema_errors == 2
    stream, data = buf.head() or ("", "")
    ack = TcAck.model_validate_json(data)
    assert stream == f"tcack:{V}" and len(buf) == 1  # no tcack for the unreadable one
    assert (ack.cmd_id, ack.accepted, ack.code) == (bad, False, "SCHEMA") and ack.reason


def test_tc_loop_survives_redis_errors() -> None:
    import redis.exceptions

    stop, box, buf, calls = asyncio.Event(), CommandBox(COMMANDS), FrameBuffer(), [0]

    class Flaky(FakeRedis):
        async def xreadgroup(self, *a: Any, **k: Any) -> list[Any]:
            calls[0] += 1
            if calls[0] == 1:
                raise redis.exceptions.ConnectionError("down")
            return await super().xreadgroup(*a, **k)

    r = Flaky([("0-0", {b"data": tc("isolate_valve", valve="A").model_dump_json().encode()})], stop)

    async def no_sleep(_: float) -> None:
        return None

    asyncio.run(tc_loop(r, V, box, buf, stop, sleep=no_sleep))  # type: ignore[arg-type]
    assert len(r.groups) == 2  # group re-created after the error (a wiped Redis loses it)
    assert r.acked == ["0-0"] and box.inbox.qsize() == 1


# ---- CommandBox.step
def test_paused_box_takes_nothing() -> None:
    box = box_with(tc("isolate_valve", valve="A"))
    assert box.step(0.0, EPOCH, paused=True, hold_s=None, dropout=False) == ([], None)
    assert box.inbox.qsize() == 1 and box.last_cmd_id is None


def test_due_held_command_runs_before_a_new_one() -> None:
    a, b = tc("isolate_valve", valve="A"), tc("isolate_valve", valve="B")
    box = box_with(a, b)
    acks, done = box.step(0.0, EPOCH, paused=False, hold_s=0.2, dropout=False)
    assert [x.cmd_id for x in acks] == [a.cmd_id] and done is None
    assert box.held == ((0.2, a),)
    assert box.step(0.1, EPOCH, paused=False, hold_s=None, dropout=False)[1] == b
    assert box.step(0.2, EPOCH, paused=False, hold_s=None, dropout=False) == ([], a)
    assert not box.held and box.last_cmd_id == a.cmd_id


# ---- tick_loop with a CommandBox (fake clock, 10 Hz)
def test_one_command_per_tick() -> None:
    cmds = [tc("isolate_valve", valve="A") for _ in range(3)]
    frames, acks = frames_and_acks(tick_pairs(6, box=box_with(*cmds)))
    assert [f["last_cmd_id"] for f in frames] == [None] + [str(c.cmd_id) for c in cmds] + [
        str(cmds[2].cmd_id)
    ] * 2  # frame k+1 carries cmd k
    assert [(i, a["cmd_id"], a["accepted"], a["code"]) for i, a in acks] == [
        (k, str(c.cmd_id), True, "OK") for k, c in enumerate(cmds)
    ]
    assert [a["t_onboard"] for _, a in acks] == [frames[k]["t_onboard"] for k in range(3)]


def test_pause_holds_ack_and_execution() -> None:
    cmds = [tc("isolate_valve", valve="A") for _ in range(2)]
    pairs = tick_pairs(10, at_tick(0, fault_req("pause", duration_s=0.6)), box_with(*cmds))
    frames, acks = frames_and_acks(pairs)
    assert len(frames) == 10  # frames keep flowing
    assert [i for i, _ in acks] == [6, 7]  # nothing for 6 ticks, then one per tick
    assert first_seen(frames, cmds[0].cmd_id) == 7
    assert first_seen(frames, cmds[1].cmd_id) == 8


def test_hold_exec_acks_now_executes_later() -> None:
    c = tc("isolate_valve", valve="A")
    pairs = tick_pairs(20, at_tick(0, fault_req("hold_exec", duration_s=1.6)), box_with(c))
    frames, acks = frames_and_acks(pairs)
    assert [(i, a["code"]) for i, a in acks] == [(0, "OK")]  # acknowledged at once
    assert first_seen(frames, c.cmd_id) == 17  # executed at tick 16 (1.6 s), echoed next frame


def test_dropout_executes_but_loses_ack() -> None:
    c = tc("isolate_valve", valve="A")
    pairs = tick_pairs(10, at_tick(0, fault_req("dropout", duration_s=0.5)), box_with(c))
    frames, acks = frames_and_acks(pairs)
    assert acks == []
    assert frames[0]["seq"] == 5  # ticks 0..4 lost
    assert all(f["last_cmd_id"] == str(c.cmd_id) for f in frames)


def test_invalid_args_acked_false() -> None:
    c = tc("vent_cabin", duration_s=11)
    frames, acks = frames_and_acks(tick_pairs(4, box=box_with(c)))
    assert len(acks) == 1
    a = acks[0][1]
    assert (a["accepted"], a["code"]) == (False, "INVALID_ARGS") and "duration_s" in a["reason"]
    assert all(f["last_cmd_id"] is None for f in frames)  # not executed
    p = [f["cabin_pressure_kpa"] for f in frames]
    assert all(abs(x - 101.3) < 0.2 for x in p)


def test_isolate_in_the_loop_stops_the_leak_and_repressurises() -> None:
    base = [f["cabin_pressure_kpa"] for f in frames_and_acks(tick_pairs(25))[0]]
    box, iso = CommandBox(COMMANDS), tc("isolate_valve", valve="A")
    seen: list[FaultBoard] = []

    def act(k: int, board: FaultBoard, mono: float) -> None:
        seen[:] = [board]
        if k == 0:
            board.add(fault_req("leak"), mono)
        if k == 10:
            box.inbox.put_nowait(iso)

    frames, _ = frames_and_acks(tick_pairs(25, act, box))
    p = [f["cabin_pressure_kpa"] for f in frames]
    for k in range(11):  # leak until the isolate executes after frame 10
        assert p[k] == pytest.approx(base[k] - 0.04 * k)
    for k in range(11, 19):  # +0.5 kPa/s from -0.4
        assert p[k] == pytest.approx(base[k] - 0.4 + 0.05 * (k - 10))
    assert p[19:] == pytest.approx(base[19:])  # back at nominal, no overshoot
    assert seen[0].active(V, 99.0) == ()  # the leak fault is cleared


def test_vent_in_the_loop_drops_two_kpa_per_s_for_its_duration() -> None:
    base = [f["cabin_pressure_kpa"] for f in frames_and_acks(tick_pairs(20))[0]]
    box = box_with(tc("vent_cabin", duration_s=1))
    p = [f["cabin_pressure_kpa"] for f in frames_and_acks(tick_pairs(20, box=box))[0]]
    for k in range(11):
        assert p[k] == pytest.approx(base[k] - 0.2 * k)
    for k in range(11, 20):
        assert p[k] == pytest.approx(base[k] - 2.0)
