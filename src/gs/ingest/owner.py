"""Vehicle ownership through a blocking Postgres advisory lock (D-ING-10/11/12)."""

import asyncio
import contextlib
from collections.abc import Mapping

import psycopg

from gs.common.config import VEHICLE_ID, ConfigError
from gs.ingest.errors import OperationalDbError

LOCK_SQL = "SELECT pg_advisory_lock(hashtext(%s))"
UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext(%s))"
# pg_locks shows a bigint advisory key split over classid (high 32 bits) / objid (low 32 bits),
# objsubid 1; the shift recombines it, also for a negative hashtext (Review Focus 3)
MATCH = (
    "l.locktype = 'advisory' AND l.objsubid = 1"
    " AND ((l.classid::bigint << 32) | l.objid::bigint) = hashtext(%s)::bigint"
)


def _key(vehicle: str) -> str:
    return "tm:" + vehicle


async def acquire(conn: psycopg.AsyncConnection, vehicle: str, stop: asyncio.Event) -> bool:
    """Block in pg_advisory_lock until granted (True) or `stop` is set (False). On stop, and when
    this task is cancelled (a sibling vehicle failed), the server-side wait is cancelled first,
    so neither a SIGTERM nor the connection's close waits for the owner (Review Focus 4)."""
    lock = asyncio.ensure_future(conn.execute(LOCK_SQL, (_key(vehicle),)))
    stopped = asyncio.ensure_future(stop.wait())
    returned = False
    try:
        await asyncio.wait({lock, stopped}, return_when=asyncio.FIRST_COMPLETED)
        returned = lock.done()
    finally:
        stopped.cancel()
        if not returned:  # stop or cancellation: end the wait before anyone closes conn
            with contextlib.suppress(psycopg.Error):
                await conn.cancel_safe()
            with contextlib.suppress(psycopg.Error):
                await lock  # QueryCanceled, or granted in the same instant (freed on close)
    if returned:
        lock.result()  # surfaces a connection error
    return returned and not stop.is_set()


async def release(conn: psycopg.AsyncConnection, vehicle: str) -> None:
    await conn.execute(UNLOCK_SQL, (_key(vehicle),))


async def waiting(conn: psycopg.AsyncConnection, vehicle: str, consumer: str) -> bool:
    """`consumer`'s session waits on the vehicle's lock (not granted)."""
    cur = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a USING (pid)"
        f" WHERE {MATCH} AND NOT l.granted AND a.application_name = %s)",
        (_key(vehicle), consumer),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


async def holder(conn: psycopg.AsyncConnection, vehicle: str) -> tuple[int, str] | None:
    """(pid, application_name) of the session holding the vehicle's lock."""
    cur = await conn.execute(
        "SELECT l.pid, a.application_name FROM pg_locks l JOIN pg_stat_activity a USING (pid)"
        f" WHERE {MATCH} AND l.granted",
        (_key(vehicle),),
    )
    row = await cur.fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


async def fence(conn: psycopg.AsyncConnection) -> None:
    """Pre-read fence (D-ING-12, R2-06): a stolen owner's lock session is gone, so this fails
    before the owner can pull new entries into its PEL."""
    try:
        await conn.execute("SELECT 1")
    except psycopg.Error as e:
        raise OperationalDbError(f"fence: {e}") from e


def parse_homes(raw: str, vehicles: tuple[str, ...]) -> Mapping[str, str]:
    """`ingest-1=NYX-TA1,ingest-2=NYX-TA2` → {vehicle: home consumer}; fails fast (R28)."""
    homes: dict[str, str] = {}
    for part in (p.strip() for p in raw.split(",")):
        consumer, sep, vehicle = (x.strip() for x in part.partition("="))
        if not part or not sep or not consumer or not VEHICLE_ID.fullmatch(vehicle):
            raise ConfigError(f"INGEST_HOMES entry {part!r} is not <consumer>=<vehicle>")
        if vehicle in homes:
            raise ConfigError(f"INGEST_HOMES names vehicle {vehicle} twice")
        if vehicle not in vehicles:
            raise ConfigError(f"INGEST_HOMES vehicle {vehicle} is not in VEHICLES")
        homes[vehicle] = consumer
    return homes
