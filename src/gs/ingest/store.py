"""Frame-transaction writers: idempotent telemetry (D-ING-13), seq_state, events, engine_state."""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from gs.common.interfaces import FrameHeader
from gs.ingest.events import insert_events
from gs.ingest.fold import Outcome, VehicleState, dump_vehicle, state_version
from gs.ingest.seq import SeqState


async def insert_frames(
    conn: psycopg.AsyncConnection, frames: Sequence[FrameHeader], params: Sequence[str]
) -> list[FrameHeader]:
    """Insert inside the caller's transaction; return the frames actually stored, input order.
    A frame whose (vehicle, t_onboard, seq) already exists is skipped, not an error."""
    if not frames:
        return []
    cols = ["t_onboard", "vehicle", "seq", "mode", "last_cmd_id", *params]
    query = sql.SQL(
        "INSERT INTO telemetry ({}) VALUES ({}) ON CONFLICT DO NOTHING"
        " RETURNING vehicle, t_onboard, seq"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    rows = [tuple(getattr(f, c) for c in cols) for f in frames]
    async with conn.cursor() as cur:
        await cur.executemany(query, rows, returning=True)
        # one result set per input row, in order; empty when the row conflicted
        inserted: list[FrameHeader] = []
        for frame in frames:
            if cur.rowcount:
                inserted.append(frame)
            if not cur.nextset():
                break
    return inserted


async def load_seq_state(conn: psycopg.AsyncConnection, vehicle: str) -> SeqState | None:
    cur = await conn.execute(
        "SELECT epoch, last_seq, last_t, period_s FROM seq_state WHERE vehicle = %s", (vehicle,)
    )
    row = await cur.fetchone()
    return None if row is None else SeqState(*row)


async def load_engine_row(
    conn: psycopg.AsyncConnection, vehicle: str
) -> tuple[int, str, dict[str, Any]] | None:
    """(schema_version, set_sha256, state) of the vehicle's engine_state row."""
    cur = await conn.execute(
        "SELECT schema_version, set_sha256, state FROM engine_state WHERE vehicle = %s", (vehicle,)
    )
    row = await cur.fetchone()
    return None if row is None else (row[0], row[1], row[2])


async def write_outcome(
    conn: psycopg.AsyncConnection, vehicle: str, vs: VehicleState, outcome: Outcome
) -> None:
    """Inside the caller's frame transaction (D-ING-15/22): seq_state, events, engine_state."""
    s = vs.seq
    if s is not None:
        await conn.execute(
            "INSERT INTO seq_state (vehicle, epoch, last_seq, last_t, period_s)"
            " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (vehicle) DO UPDATE SET"
            " epoch=EXCLUDED.epoch, last_seq=EXCLUDED.last_seq, last_t=EXCLUDED.last_t,"
            " period_s=EXCLUDED.period_s",
            (vehicle, s.epoch, s.last_seq, s.last_t, s.period_s),
        )
    await insert_events(conn, outcome.events)
    if s is not None:  # a vehicle with no frame yet has no state to keep
        await conn.execute(
            "INSERT INTO engine_state (vehicle, last_seq, epoch, set_sha256, schema_version, state)"
            " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (vehicle) DO UPDATE SET"
            " last_seq=EXCLUDED.last_seq, epoch=EXCLUDED.epoch, set_sha256=EXCLUDED.set_sha256,"
            " schema_version=EXCLUDED.schema_version, state=EXCLUDED.state,"
            " updated_at=clock_timestamp()",
            (vehicle, s.last_seq, s.epoch, vs.set_sha256, state_version(), Jsonb(dump_vehicle(vs))),
        )
