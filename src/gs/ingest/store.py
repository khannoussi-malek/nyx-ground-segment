"""Frame-transaction writers: idempotent telemetry (D-ING-13), seq_state, events, alarm rows,
engine_state."""

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from gs.alarms.contract import Level, OpenAlarm, Transition
from gs.alarms.engine import CompiledSet, compile_set
from gs.common.interfaces import FrameHeader
from gs.common.missiondb import MissionDb, load
from gs.ingest.compute import Computed
from gs.ingest.events import insert_events
from gs.ingest.fold import Outcome, VehicleState, dump_vehicle, state_version
from gs.ingest.seq import SeqState


async def insert_frames(
    conn: psycopg.AsyncConnection,
    frames: Sequence[FrameHeader],
    computed: Sequence[Computed],
    mdb: MissionDb,
) -> list[FrameHeader]:
    """Insert inside the caller's transaction; return the frames actually stored, input order.
    `<name>` takes the engineering value, `<name>_raw` the frame's own (D-EXT-04). A frame whose
    (vehicle, t_onboard, seq) already exists is skipped, not an error."""
    if not frames:
        return []
    cols = [
        "t_onboard",
        "vehicle",
        "seq",
        "mode",
        "last_cmd_id",
        *mdb.param_names,
        *mdb.raw_columns,
    ]
    query = sql.SQL(
        "INSERT INTO telemetry ({}) VALUES ({}) ON CONFLICT DO NOTHING"
        " RETURNING vehicle, t_onboard, seq"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
    )
    rows = [
        (
            f.t_onboard, f.vehicle, f.seq, f.mode, f.last_cmd_id,
            *(c.values[p] for p in mdb.param_names),
            *(c.raws[r.removesuffix("_raw")] for r in mdb.raw_columns),
        )
        for f, c in zip(frames, computed, strict=True)
    ]  # fmt: skip
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
    return row


async def load_open_alarms(conn: psycopg.AsyncConnection, vehicle: str) -> list[OpenAlarm]:
    cur = await conn.execute(
        "SELECT alarm_id, param, state, severity, acked_sev, trigger_check, opened_at FROM alarms"
        " WHERE vehicle = %s AND state <> 'CLEARED'",
        (vehicle,),
    )
    return [
        OpenAlarm(aid, p, st, Level[sev], None if acked is None else Level[acked], trig, opened)
        for aid, p, st, sev, acked, trig, opened in await cur.fetchall()
    ]


async def load_cset(conn: psycopg.AsyncConnection, set_sha256: str) -> CompiledSet:
    """The compiled set stored in limits_versions; MissionDbError if it no longer validates."""
    cur = await conn.execute(
        "SELECT limits_yaml, commands_yaml, parameters_yaml FROM limits_versions"
        " WHERE set_sha256 = %s",
        (set_sha256,),
    )
    row = await cur.fetchone()
    if row is None:  # engine_state.set_sha256 references the table: only a bug gets here
        raise LookupError(f"no limits_versions row {set_sha256}")
    return compile_set(load(*(t.encode() for t in row)))


# severity keeps the alarm's last non-NOMINAL value on RTN/CLEAR (ruling G17: the column is
# YELLOW|RED only); limits_sha256 comes from the set row, so it always matches set_sha256
_UPSERT_ALARM = """
INSERT INTO alarms (alarm_id, vehicle, param, state, severity, acked_sev, trigger_check,
                    limits_sha256, set_sha256, opened_at, closed_at, check_set)
VALUES (%(alarm_id)s, %(vehicle)s, %(param)s, %(state)s,
        COALESCE(%(severity)s, (SELECT severity FROM alarms WHERE alarm_id = %(alarm_id)s)),
        %(acked_sev)s, %(trigger_check)s,
        (SELECT limits_sha256 FROM limits_versions WHERE set_sha256 = %(set_sha256)s),
        %(set_sha256)s, %(opened_at)s, %(closed_at)s, %(check_set)s)
ON CONFLICT (alarm_id) DO UPDATE SET state = EXCLUDED.state, severity = EXCLUDED.severity,
  acked_sev = EXCLUDED.acked_sev, trigger_check = EXCLUDED.trigger_check,
  limits_sha256 = EXCLUDED.limits_sha256, set_sha256 = EXCLUDED.set_sha256,
  closed_at = EXCLUDED.closed_at, check_set = EXCLUDED.check_set
"""
_INSERT_ALARM_EVENT = """
INSERT INTO alarm_events (key, alarm_id, vehicle, param, kind, check_kind, level, severity,
                          value, trigger_seq, trigger_t_onboard, by_user, note, alert,
                          limits_sha256, set_sha256, check_set)
VALUES (%(key)s, %(alarm_id)s, %(vehicle)s, %(param)s, %(kind)s, %(check_kind)s, %(level)s,
        %(event_severity)s, %(value)s, %(trigger_seq)s, %(trigger_t)s, %(by_user)s, %(note)s,
        %(alert)s,
        (SELECT limits_sha256 FROM limits_versions WHERE set_sha256 = %(set_sha256)s),
        %(set_sha256)s, %(check_set)s)
ON CONFLICT (key) DO NOTHING
"""


async def write_transitions(
    conn: psycopg.AsyncConnection, set_sha256: str, transitions: Sequence[Transition]
) -> None:
    """alarms row, then its alarm_events row (FK), per transition in engine order."""
    for t in transitions:
        sev = None if t.severity == Level.NOMINAL else t.severity.name
        p = {
            "alarm_id": t.alarm_id,
            "vehicle": t.vehicle,
            "param": t.param,
            "state": t.alarm_state,
            "severity": sev,
            "event_severity": t.severity.name,
            "acked_sev": None if t.acked_sev is None else t.acked_sev.name,
            "trigger_check": t.trigger_check,
            "set_sha256": set_sha256,
            "opened_at": t.opened_at,
            "closed_at": t.closed_at,
            "key": t.key,
            "kind": t.kind,
            "check_kind": t.check_kind,
            "level": t.level,
            "value": t.value,
            "trigger_seq": t.trigger_seq,
            "trigger_t": t.trigger_t,
            "by_user": t.by_user,
            "note": t.note,
            "alert": t.alert,
            "check_set": t.check_set,
        }
        await conn.execute(_UPSERT_ALARM, p)
        await conn.execute(_INSERT_ALARM_EVENT, p)


async def write_outcome(
    conn: psycopg.AsyncConnection, vehicle: str, vs: VehicleState, outcome: Outcome
) -> None:
    """Inside the caller's frame transaction (D-ING-15/22): seq_state, events, alarm
    transitions (built with vs.set_sha256), engine_state."""
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
    await write_transitions(conn, vs.set_sha256, outcome.transitions)
    # no frame yet, or no set applied yet ("": seq_state without a snapshot): nothing to keep
    if s is not None and vs.set_sha256:
        await conn.execute(
            "INSERT INTO engine_state (vehicle, last_seq, epoch, set_sha256, schema_version, state)"
            " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (vehicle) DO UPDATE SET"
            " last_seq=EXCLUDED.last_seq, epoch=EXCLUDED.epoch, set_sha256=EXCLUDED.set_sha256,"
            " schema_version=EXCLUDED.schema_version, state=EXCLUDED.state,"
            " updated_at=clock_timestamp()",
            (vehicle, s.last_seq, s.epoch, vs.set_sha256, state_version(), Jsonb(dump_vehicle(vs))),
        )
