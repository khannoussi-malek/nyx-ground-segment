"""Ground events: kinds, deterministic keys (R5) and idempotent inserts."""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from gs.common.missiondb import MissionDb
from gs.common.time import iso_z

EventKind = Literal[
    "GAP",
    "SEQ_JUMP",
    "SEQ_RESET",
    "SEQ_ANOMALY",
    "LOS",
    "AOS",
    "FROZEN",
    "POISON",
    "LIMITS_APPLIED",
    "LIMITS_REJECTED",
    "ENGINE_STATE_RESET",
    "LATE_VERIFICATION",
    "INTERFACE_MISMATCH",
    "ARCHIVE_CORRUPT",
]


@dataclass(frozen=True)
class GroundEvent:
    key: str
    vehicle: str | None
    kind: EventKind
    t_onboard: datetime | None
    details: Mapping[str, Any]


def seq_key(kind: str, vehicle: str, epoch: datetime, from_seq: int | None = None) -> str:
    """GAP / SEQ_JUMP carry `from_seq`; SEQ_RESET / SEQ_ANOMALY take the new epoch only."""
    base = f"{kind}/{vehicle}/{iso_z(epoch)}"
    return base if from_seq is None else f"{base}/{from_seq}"


def link_key(kind: Literal["LOS", "AOS"], vehicle: str, epoch: datetime, last_seq: int) -> str:
    return f"{kind}/{vehicle}/{iso_z(epoch)}/{last_seq}"  # AOS shares its LOS's suffix


def frozen_key(vehicle: str, epoch: datetime, first_seq: int) -> str:
    return f"FROZEN/{vehicle}/{iso_z(epoch)}/{first_seq}"


def poison_key(stream: str, entry_id: str) -> str:
    return f"POISON/{stream}/{entry_id}"


def applied_key(vehicle: str, epoch: datetime, first_seq: int) -> str:
    return f"LIMITS_APPLIED/{vehicle}/{iso_z(epoch)}/{first_seq}"


def rejected_key(set_sha256: str) -> str:
    return f"LIMITS_REJECTED/{set_sha256}"


def reset_key(vehicle: str, epoch: datetime, last_seq: int) -> str:
    return f"ENGINE_STATE_RESET/{vehicle}/{iso_z(epoch)}/{last_seq}"


def late_key(cmd_id: uuid.UUID) -> str:
    return f"LATE_VERIFICATION/{cmd_id}"


def mismatch_key(stream_kind: Literal["tc", "tcack", "ui-event"], sha12: str) -> str:
    return f"INTERFACE_MISMATCH/{stream_kind}/{sha12}"


def transition_key(vehicle: str, param: str, epoch: datetime, trigger_seq: int, kind: str) -> str:
    return f"{vehicle}/{param}/{iso_z(epoch)}/{trigger_seq}/{kind}"


def alarm_id_for(raise_key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, "nyx-alarm/" + raise_key)


def _iso(o: object) -> str:
    if isinstance(o, datetime):
        return iso_z(o)  # D-ENG-18: every time leaving the process
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def details_json(details: Mapping[str, Any]) -> str:
    return json.dumps(details, default=_iso)


async def insert_events(conn: psycopg.AsyncConnection, events: Sequence[GroundEvent]) -> int:
    """Inside the caller's transaction; returns the rows actually inserted."""
    inserted = 0
    for e in events:
        cur = await conn.execute(
            "INSERT INTO events (key, vehicle, kind, t_onboard, details) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (key) DO NOTHING",
            (e.key, e.vehicle, e.kind, e.t_onboard, Jsonb(dict(e.details), dumps=details_json)),
        )
        inserted += cur.rowcount
    return inserted


async def insert_set(conn: psycopg.AsyncConnection, mdb: MissionDb) -> None:
    await conn.execute(
        "INSERT INTO limits_versions (set_sha256, limits_sha256, commands_sha256,"
        " parameters_sha256, limits_yaml, commands_yaml, parameters_yaml)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT DO NOTHING",
        (
            mdb.set_sha256,
            mdb.limits_sha256,
            mdb.commands_sha256,
            mdb.parameters_sha256,
            mdb.limits_yaml,
            mdb.commands_yaml,
            mdb.parameters_yaml,
        ),
    )
