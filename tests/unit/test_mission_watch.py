from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from gs.common.missiondb import MissionDb, MissionDbError, load_dir
from gs.ingest import mission
from gs.ingest.events import GroundEvent
from gs.ingest.mission import MissionWatcher, Poll, observe, validate_set
from tests.support.mission import MISSION_DIR
from tests.support.missionfiles import edit, mission_copy

A = ("a1", "a2", "a3")
B = ("b1", "b2", "b3")
C = ("c1", "c2", "c3")


def test_applied_after_two_equal_polls() -> None:
    p, ready = observe(Poll(), B, A)
    assert not ready
    p, ready = observe(p, B, A)
    assert ready


def test_intermediate_set_never_ready() -> None:
    p, r1 = observe(Poll(), B, A)
    p, r2 = observe(p, C, A)
    p, r3 = observe(p, C, A)
    assert (r1, r2, r3) == (False, False, True) and p.last == C


def test_unchanged_never_ready() -> None:
    p, r1 = observe(Poll(), A, A)
    _, r2 = observe(p, A, A)
    assert not (r1 or r2)


def _raw(d: Path) -> tuple[bytes, bytes, bytes]:
    b = [(d / n).read_bytes() for n in ("limits.yaml", "commands.yaml", "parameters.yaml")]
    return b[0], b[1], b[2]


def test_hazardous_downgrade_is_not_an_ingest_rule(tmp_path: Path) -> None:  # G31
    # ingest validates one way at start and on reload (no active-set rule), so two workers
    # can't end up on different sets; the rule belongs to the API's own reload (day 3)
    m = mission_copy(tmp_path)
    edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
    mdb = validate_set(_raw(m), load_dir(m).param_names)
    assert mdb.commands["vent_cabin"].hazardous is False


def test_unmigrated_param_rejected(tmp_path: Path) -> None:
    m = mission_copy(tmp_path)
    with (m / "parameters.yaml").open("a") as f:
        f.write("  cabin_humidity_pct: {unit: pct, type: float}\n")
    cols = load_dir(MISSION_DIR).param_names
    with pytest.raises(
        MissionDbError, match="cabin_humidity_pct has no telemetry column: run make migrate"
    ):
        validate_set(_raw(m), cols)


class _Cur:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self.rows, self.rowcount = rows, 1

    async def fetchall(self) -> list[tuple[str]]:
        return self.rows


class _Conn:
    """Just enough of an AsyncConnection for MissionWatcher._tick; records each statement."""

    def __init__(self, columns: Sequence[str]) -> None:
        self.columns, self.sql = columns, list[str]()

    async def execute(self, query: str, params: object = None) -> _Cur:
        self.sql.append(query)
        return _Cur([(c,) for c in self.columns])


Hooks = tuple[list[tuple[str, str]], list[list[str]]]


def watched(m: Path, **kw: Any) -> tuple[MissionWatcher, Any, Hooks]:
    active = load_dir(m)
    applied: list[tuple[str, str]] = []
    rejected: list[list[str]] = []

    async def on_apply(conn: Any, old: MissionDb, new: MissionDb) -> None:
        applied.append((old.set_sha256, new.set_sha256))

    async def on_events(evs: Sequence[GroundEvent]) -> None:
        rejected.append([e.kind for e in evs])

    w = MissionWatcher(m, "unused", "t", active, on_apply=on_apply, on_events=on_events, **kw)
    return w, cast(Any, _Conn(active.param_names)), (applied, rejected)


async def ticks(w: MissionWatcher, conn: Any, n: int = 3) -> None:
    for _ in range(n):
        await w._tick(conn)


async def test_on_apply_once_per_applied_set(tmp_path: Path) -> None:  # Task 12
    m = mission_copy(tmp_path)
    w, conn, (applied, rejected) = watched(m)
    old = w.current.set_sha256
    await ticks(w, conn)
    assert applied == [] and "INSERT INTO limits_versions" in conn.sql[0]  # first tick stores it
    edit(m / "commands.yaml", "max: 10", "max: 8")
    await ticks(w, conn, 5)
    assert applied == [(old, w.current.set_sha256)] and rejected == []
    assert w.current.commands["vent_cabin"].args["duration_s"].model_dump()["max"] == 8


async def test_on_events_once_per_rejected_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mission, "REVALIDATE_S", 0.0)  # revalidated every tick from here on
    m = mission_copy(tmp_path)
    w, conn, (applied, rejected) = watched(m)
    old = w.current
    edit(m / "limits.yaml", "hysteresis_pct: 15", "hysteresis_pct: -1")
    await ticks(w, conn, 6)
    assert rejected == [["LIMITS_REJECTED"]] and applied == [] and w.current is old


async def test_check_rejects_with_by(tmp_path: Path) -> None:  # P-pf5: the API's own rule
    seen: list[tuple[str, str]] = []

    def no_downgrade(old: MissionDb, new: MissionDb) -> None:
        seen.append((old.set_sha256, new.set_sha256))
        raise MissionDbError("commands.yaml", "commands not updated until API restart")

    m = mission_copy(tmp_path)
    w, conn, (applied, rejected) = watched(m, check=no_downgrade, by="api")
    old = w.current
    edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
    await ticks(w, conn)
    assert len(seen) == 1 and applied == [] and rejected == [["LIMITS_REJECTED"]]
    assert w.current is old
    (insert,) = [q for q in conn.sql if "INSERT INTO events" in q]
    assert insert  # the event row; its details are checked in the API integration test
