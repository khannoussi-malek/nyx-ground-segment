from datetime import UTC, datetime

import psycopg

from gs.common.missiondb import load_dir
from gs.ingest.events import GroundEvent, insert_events, insert_set, seq_key
from tests.support.env import vid
from tests.support.mission import MISSION_DIR
from tests.support.pg import app_dsn, events_for

E = datetime(2026, 11, 14, 10, tzinfo=UTC)


async def test_insert_events_idempotent() -> None:
    v = vid()
    ev = GroundEvent(seq_key("GAP", v, E, 1), v, "GAP", E, {"lost": 3})
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as c:
        async with c.transaction():
            assert await insert_events(c, [ev, ev]) == 1
        async with c.transaction():
            assert await insert_events(c, [ev]) == 0
    assert [e["details"]["lost"] for e in events_for(v, "GAP")] == [3]


async def test_insert_set_once() -> None:
    db = load_dir(MISSION_DIR)
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as c:
        await insert_set(c, db)
        await insert_set(c, db)
        cur = await c.execute(
            "select limits_yaml from limits_versions where set_sha256=%s", (db.set_sha256,)
        )
        row = await cur.fetchone()
    assert row == (db.limits_yaml,)
