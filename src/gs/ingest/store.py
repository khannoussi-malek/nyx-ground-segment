"""Idempotent telemetry storage (D-ING-13)."""

from collections.abc import Sequence

import psycopg
from psycopg import sql

from gs.common.interfaces import FrameHeader


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
