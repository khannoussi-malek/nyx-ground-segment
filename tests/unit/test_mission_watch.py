import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest

from gs.api.app import HazardGuard
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
    pf = m / "parameters.yaml"
    pf.write_text(
        pf.read_text().replace(
            "derived:", "  cabin_humidity_pct: {unit: pct, type: float}\nderived:"
        )
    )
    cols = load_dir(MISSION_DIR).param_names
    with pytest.raises(
        MissionDbError, match="cabin_humidity_pct has no telemetry column: run make migrate"
    ):
        validate_set(_raw(m), cols)


class _Cur:
    def __init__(self, rows: list[tuple[str]], rowcount: int = 1) -> None:
        self.rows, self.rowcount = rows, rowcount

    async def fetchall(self) -> list[tuple[str]]:
        return self.rows


class _Conn:
    """Just enough of an AsyncConnection for MissionWatcher._tick; records each statement."""

    def __init__(self, columns: Sequence[str], inserts_win: bool = True) -> None:
        self.columns, self.sql, self.inserts_win = columns, list[str](), inserts_win

    async def execute(self, query: str, params: object = None) -> _Cur:
        self.sql.append(query)
        won = self.inserts_win or "INSERT INTO events" not in query
        return _Cur([(c,) for c in self.columns], 1 if won else 0)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        self.sql.append("BEGIN")
        try:
            yield
        except BaseException:
            self.sql.append("ROLLBACK")
            raise
        self.sql.append("COMMIT")


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


async def test_on_events_only_when_this_insert_won(tmp_path: Path) -> None:
    # another service stored (and announced) the same rejected set first: no duplicate link
    m = mission_copy(tmp_path)
    w, _, (applied, rejected) = watched(m)
    conn = cast(Any, _Conn(w.current.param_names, inserts_win=False))
    edit(m / "limits.yaml", "hysteresis_pct: 15", "hysteresis_pct: -1")
    await ticks(w, conn)
    assert rejected == [] and applied == []
    assert any("INSERT INTO events" in q for q in conn.sql)


async def test_audit_failure_keeps_the_old_set_and_retries(tmp_path: Path) -> None:  # P-pf45
    m = mission_copy(tmp_path)
    fails = [psycopg.OperationalError("audit lost")]
    audited: list[str] = []

    async def audit(conn: Any, old: MissionDb, new: MissionDb) -> None:
        conn.sql.append("AUDIT")
        if fails:
            raise fails.pop()
        audited.append(new.set_sha256)

    active = load_dir(m)
    w = MissionWatcher(m, "unused", "t", active, on_apply=audit)
    conn = cast(Any, _Conn(active.param_names))
    await ticks(w, conn, 1)
    edit(m / "commands.yaml", "max: 10", "max: 8")
    await w._tick(conn)  # first poll of the new files
    with pytest.raises(psycopg.OperationalError):
        await w._tick(conn)
    assert w.current is active and audited == []
    i = conn.sql.index("BEGIN")
    assert "INSERT INTO limits_versions" in conn.sql[i + 1]
    assert conn.sql[i + 2 : i + 4] == ["AUDIT", "ROLLBACK"]  # the set row goes with the audit
    await w._tick(conn)  # the next poll retries
    assert w.current.set_sha256 == audited[0] != active.set_sha256
    assert conn.sql[-1] == "COMMIT"


async def test_refused_while_the_check_refuses_the_files(tmp_path: Path) -> None:  # P-pf46
    m = mission_copy(tmp_path)
    w, conn, _ = watched(m, check=HazardGuard(load_dir(m)), by="api")
    shipped = (m / "commands.yaml").read_text()
    await ticks(w, conn)
    assert not w.refused
    edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
    await ticks(w, conn)
    assert w.refused
    (m / "commands.yaml").write_text(shipped)
    await ticks(w, conn)
    assert not w.refused


async def test_refused_survives_later_invalid_files(tmp_path: Path) -> None:  # P-pf46 review
    m = mission_copy(tmp_path)  # ingest runs the downgrade; invalid files don't change that
    w, conn, _ = watched(m, check=HazardGuard(load_dir(m)), by="api")
    await ticks(w, conn)
    edit(m / "commands.yaml", "hazardous: true", "hazardous: false")
    await ticks(w, conn)
    assert w.refused
    edit(m / "limits.yaml", "hysteresis_pct: 15", "hysteresis_pct: -1")
    await ticks(w, conn)
    assert w.refused


async def test_run_survives_any_tick_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    m = mission_copy(tmp_path)
    w, conn, _ = watched(m)
    stop = asyncio.Event()
    calls: list[int] = []

    async def connect(*a: object, **kw: object) -> Any:
        conn.close = _aclose
        return conn

    async def _aclose() -> None:
        pass

    async def tick(c: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("not a database error")
        stop.set()

    monkeypatch.setattr(mission, "POLL_S", 0.0)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(w, "_tick", tick)
    await asyncio.wait_for(w.run(stop), 2)
    assert len(calls) == 2
    assert any("not a database error" in (r.exc_text or "") for r in caplog.records)


def _commands(m: Path, text: str) -> MissionDb:
    (m / "commands.yaml").write_text(text)
    return load_dir(m)


def test_hazard_guard_remove_then_readd(tmp_path: Path) -> None:  # P-pf44
    m = mission_copy(tmp_path)
    shipped = (m / "commands.yaml").read_text()
    start = load_dir(m)
    head, _ = shipped.split("  vent_cabin:\n")
    removed = _commands(m, head)
    readded = _commands(m, shipped.replace("hazardous: true", "hazardous: false"))
    guard = HazardGuard(start)
    guard(start, removed)  # dropping a command is no downgrade
    with pytest.raises(MissionDbError) as e:
        guard(removed, readded)  # back without its flag: refused although `old` lacks it
    assert e.value.file == "commands.yaml"
    assert e.value.message == (
        "mission db not updated in the API until API restart (hazardous downgrade: vent_cabin)"
    )
    guard(removed, start)  # back with its flag


def test_hazard_guard_grows_with_applied_sets(tmp_path: Path) -> None:  # P-pf44
    m = mission_copy(tmp_path)
    start = load_dir(m)
    up = _commands(m, (m / "commands.yaml").read_text().replace(
        "hazardous: false", "hazardous: true\n    hazard: Isolates the cabin from the supply"
    ))  # fmt: skip
    guard = HazardGuard(start)
    guard(start, up)  # applied: isolate_valve is now hazardous too
    with pytest.raises(MissionDbError, match=r"downgrade: isolate_valve\)"):
        guard(up, start)
