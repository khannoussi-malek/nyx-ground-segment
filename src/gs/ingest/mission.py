"""Mission-db hot reload: poll rule, set validation, start-time fallback (EX-09/25)."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psycopg

from gs.common.missiondb import Hashes, MissionDb, MissionDbError, load, set_hashes
from gs.ingest.events import GroundEvent, insert_events, insert_set, rejected_key

log = logging.getLogger(__name__)

POLL_S = 1.0
REVALIDATE_S = 10.0
_FILES = ("limits.yaml", "commands.yaml", "parameters.yaml")
# raises MissionDbError to refuse `new` while `old` is active (the API's G31 rule, P-pf5)
Check = Callable[[MissionDb, MissionDb], None]
OnApply = Callable[[psycopg.AsyncConnection, MissionDb, MissionDb], Awaitable[None]]
OnEvents = Callable[[Sequence[GroundEvent]], Awaitable[None]]


@dataclass(frozen=True)
class Poll:
    last: Hashes | None = None


def observe(p: Poll, hashes: Hashes, active: Hashes) -> tuple[Poll, bool]:
    """Ready only after two consecutive equal polls (EX-09) that differ from the active set."""
    return Poll(hashes), hashes == p.last and hashes != active


def validate_set(raw: tuple[bytes, bytes, bytes], columns: Collection[str]) -> MissionDb:
    """The same verdict at start and on reload (G31): a rule that depends on the active set
    would let a restarted worker run a set its peer rejects. The hazardous true→false rule
    protects the command path and lives in the API's own reload (day 3)."""
    mdb = load(*raw)
    for p in mdb.param_names:
        if p not in columns:
            raise MissionDbError(
                "parameters.yaml", f"parameter {p} has no telemetry column: run make migrate"
            )
    return mdb


async def telemetry_columns(conn: psycopg.AsyncConnection) -> set[str]:
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'telemetry' AND table_schema = current_schema()"
    )
    return {r[0] for r in await cur.fetchall()}


def _read(mission_dir: Path, name: str) -> bytes:
    try:
        return (mission_dir / name).read_bytes()
    except OSError as e:
        raise MissionDbError(name, f"cannot read: {e}") from e


def _read_raw(mission_dir: Path) -> tuple[bytes, bytes, bytes]:
    lim, cmd, par = (_read(mission_dir, n) for n in _FILES)
    return lim, cmd, par


def _rejected(
    raw: tuple[bytes, bytes, bytes], e: MissionDbError, by: str | None = None
) -> GroundEvent:
    (lim, cmd, par), set_sha = set_hashes(*raw)
    details = {
        "file": e.file,
        "error": e.message,
        "set_sha256": set_sha,
        "limits_sha256": lim,
        "commands_sha256": cmd,
        "parameters_sha256": par,
    }
    return GroundEvent(
        key=rejected_key(set_sha),
        vehicle=None,
        kind="LIMITS_REJECTED",
        t_onboard=None,
        details=details if by is None else {**details, "by": by},
    )


async def start_set(
    conn: psycopg.AsyncConnection, mission_dir: Path
) -> tuple[MissionDb, tuple[GroundEvent, ...]]:
    cols = await telemetry_columns(conn)
    raw: tuple[bytes, bytes, bytes] | None = None
    try:
        raw = _read_raw(mission_dir)
        mdb = validate_set(raw, cols)
    except MissionDbError as e:
        err = e
    else:
        await insert_set(conn, mdb)
        return mdb, ()
    rows = await (
        await conn.execute(
            "SELECT limits_yaml, commands_yaml, parameters_yaml FROM limits_versions"
            " ORDER BY first_seen_at DESC"
        )
    ).fetchall()
    for row in rows:
        try:
            old = validate_set((row[0].encode(), row[1].encode(), row[2].encode()), cols)
        except MissionDbError:
            continue
        # unreadable files: hash what we could not read as empty so the key stays deterministic
        ev = _rejected(raw if raw is not None else (b"", b"", b""), err)
        return old, (ev,)
    raise err


class MissionWatcher:
    """Polls the mission dir; applies a new valid set after two equal polls (EX-09).
    `on_apply(conn, old, new)` runs in the transaction that stores the set, and the set becomes
    current only after it commits (P-pf45); `on_events([LIMITS_REJECTED])` once per newly
    rejected set whose row this watcher inserted; `check(old, new)` before applying (P-pf5);
    `by` names the rejecting service in the event details."""

    def __init__(
        self,
        mission_dir: Path,
        dsn: str,
        consumer: str,
        active: MissionDb,
        on_apply: OnApply | None = None,
        on_events: OnEvents | None = None,
        check: Check | None = None,
        by: str | None = None,
    ) -> None:
        self._dir = mission_dir
        self._dsn = dsn
        self._app = f"{consumer}-mission"
        self._current = active
        self._on_apply, self._on_events, self._check, self._by = on_apply, on_events, check, by
        self._stored = False  # the active set may come from load_dir with the DB down (P-pf5)
        self._poll = Poll()
        self._rejected: tuple[Hashes, float] | None = None  # hashes, when last validated
        self._refused = False

    @property
    def current(self) -> MissionDb:
        return self._current

    @property
    def refused(self) -> bool:
        """`check` refused the files on disk; cleared when a set applies or the files revert."""
        return self._refused

    async def run(self, stop: asyncio.Event) -> None:
        conn: psycopg.AsyncConnection | None = None
        try:
            while not stop.is_set():
                try:
                    if conn is None:
                        conn = await psycopg.AsyncConnection.connect(
                            self._dsn, autocommit=True, application_name=self._app
                        )
                    await self._tick(conn)
                except Exception:  # noqa: BLE001  the watcher must never die silently
                    log.exception("mission watcher: tick failed, reconnecting")
                    if conn is not None:
                        with suppress(psycopg.Error):
                            await conn.close()
                    conn = None
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), POLL_S)
        finally:
            if conn is not None:
                await conn.close()

    async def _tick(self, conn: psycopg.AsyncConnection) -> None:
        if not self._stored:  # limits_versions holds every set that was ever active
            await insert_set(conn, self._current)
            self._stored = True
        try:
            raw = _read_raw(self._dir)
        except MissionDbError as e:  # file mid-replace: try again next poll
            log.warning("mission watcher: %s", e)
            return
        hashes, _ = set_hashes(*raw)
        active = (
            self._current.limits_sha256,
            self._current.commands_sha256,
            self._current.parameters_sha256,
        )
        self._poll, ready = observe(self._poll, hashes, active)
        if hashes == active:  # files reverted to the active set: forget the rejection
            self._rejected, self._refused = None, False
        rej = self._rejected
        if rej is not None and rej[0] == hashes:
            if time.monotonic() - rej[1] < REVALIDATE_S:
                return
        elif not ready:
            return
        refused = False
        try:
            mdb = validate_set(raw, await telemetry_columns(conn))
            refused = self._check is not None
            if self._check is not None:
                self._check(self._current, mdb)
        except MissionDbError as e:
            ev = _rejected(raw, e, self._by)
            won = await insert_events(conn, [ev])  # key dedups across workers and the API
            fresh = rej is None or rej[0] != hashes  # not a 10 s revalidation of the same files
            # only an applied set or reverted files end a refusal: ingest still runs the newer set
            self._rejected, self._refused = (hashes, time.monotonic()), refused or self._refused
            if fresh and won and self._on_events is not None:  # one link per stored rejection
                await self._on_events([ev])
            return
        async with conn.transaction():  # the set row and its audit, or neither (P-pf45)
            await insert_set(conn, mdb)
            if self._on_apply is not None:
                await self._on_apply(conn, self._current, mdb)
        self._current, self._rejected, self._refused = mdb, None, False
