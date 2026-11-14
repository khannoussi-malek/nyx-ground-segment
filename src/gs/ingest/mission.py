"""Mission-db hot reload: poll rule, set validation, start-time fallback (EX-09/25)."""

import asyncio
import hashlib
import logging
import time
from collections.abc import Collection
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psycopg

from gs.common.missiondb import MissionDb, MissionDbError, load
from gs.ingest.events import GroundEvent, insert_events, insert_set, rejected_key

log = logging.getLogger(__name__)

POLL_S = 1.0
REVALIDATE_S = 10.0
_FILES = ("limits.yaml", "commands.yaml", "parameters.yaml")
Hashes = tuple[str, str, str]


@dataclass(frozen=True)
class Poll:
    last: Hashes | None = None


def observe(p: Poll, hashes: Hashes, active: Hashes) -> tuple[Poll, bool]:
    """Ready only after two consecutive equal polls (EX-09) that differ from the active set."""
    return Poll(hashes), hashes == p.last and hashes != active


def validate_set(
    raw: tuple[bytes, bytes, bytes], columns: Collection[str], active: MissionDb | None
) -> MissionDb:
    mdb = load(*raw)
    for p in mdb.param_names:
        if p not in columns:
            raise MissionDbError(
                "parameters.yaml", f"parameter {p} has no telemetry column: run make migrate"
            )
    if active is not None:
        for name, c in active.commands.items():
            new = mdb.commands.get(name)
            if c.hazardous and new is not None and not new.hazardous:
                raise MissionDbError(
                    "commands.yaml", f"{name}: hazardous true→false needs a restart"
                )
    return mdb


async def telemetry_columns(conn: psycopg.AsyncConnection) -> set[str]:
    cur = await conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'telemetry' AND table_schema = current_schema()"
    )
    return {r[0] for r in await cur.fetchall()}


def _read_raw(mission_dir: Path) -> tuple[bytes, bytes, bytes]:
    out: list[bytes] = []
    for n in _FILES:
        try:
            out.append((mission_dir / n).read_bytes())
        except OSError as e:
            raise MissionDbError(n, f"cannot read: {e}") from e
    return out[0], out[1], out[2]


def _hashes(raw: tuple[bytes, bytes, bytes]) -> Hashes:
    h = tuple(hashlib.sha256(b).hexdigest() for b in raw)
    return h[0], h[1], h[2]


def _rejected(raw: tuple[bytes, bytes, bytes], e: MissionDbError) -> GroundEvent:
    lim, cmd, par = _hashes(raw)
    set_sha = hashlib.sha256("".join(f"{h}\n" for h in (lim, cmd, par)).encode()).hexdigest()
    return GroundEvent(
        key=rejected_key(set_sha),
        vehicle=None,
        kind="LIMITS_REJECTED",
        t_onboard=None,
        details={
            "file": e.file,
            "error": e.message,
            "set_sha256": set_sha,
            "limits_sha256": lim,
            "commands_sha256": cmd,
            "parameters_sha256": par,
        },
    )


async def start_set(
    conn: psycopg.AsyncConnection, mission_dir: Path
) -> tuple[MissionDb, tuple[GroundEvent, ...]]:
    cols = await telemetry_columns(conn)
    raw: tuple[bytes, bytes, bytes] | None = None
    try:
        raw = _read_raw(mission_dir)
        mdb = validate_set(raw, cols, None)
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
            old = validate_set((row[0].encode(), row[1].encode(), row[2].encode()), cols, None)
        except MissionDbError:
            continue
        # unreadable files: hash what we could not read as empty so the key stays deterministic
        ev = _rejected(raw if raw is not None else (b"", b"", b""), err)
        return old, (ev,)
    raise err


class MissionWatcher:
    def __init__(self, mission_dir: Path, dsn: str, consumer: str, active: MissionDb) -> None:
        self._dir = mission_dir
        self._dsn = dsn
        self._app = f"{consumer}-mission"
        self._current = active
        self._poll = Poll()
        self._rejected: tuple[Hashes, float] | None = None  # hashes, when last validated

    @property
    def current(self) -> MissionDb:
        return self._current

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
                except psycopg.Error:
                    log.exception("mission watcher: database error, reconnecting")
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
        try:
            raw = _read_raw(self._dir)
        except MissionDbError as e:  # file mid-replace: try again next poll
            log.warning("mission watcher: %s", e)
            return
        hashes = _hashes(raw)
        active = (
            self._current.limits_sha256,
            self._current.commands_sha256,
            self._current.parameters_sha256,
        )
        self._poll, ready = observe(self._poll, hashes, active)
        rej = self._rejected
        if rej is not None and rej[0] == hashes:
            if time.monotonic() - rej[1] < REVALIDATE_S:
                return
        elif not ready:
            return
        try:
            mdb = validate_set(raw, await telemetry_columns(conn), self._current)
        except MissionDbError as e:
            await insert_events(conn, [_rejected(raw, e)])  # key dedups across workers
            self._rejected = (hashes, time.monotonic())
            return
        await insert_set(conn, mdb)
        self._current = mdb
        self._rejected = None
