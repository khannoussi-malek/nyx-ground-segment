"""What the soak reads each sample: the simulator ledger (a growing file), the tables the
invariants judge, the DLQ. Everything since the soak's start."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chaos.invariants import AlarmRow, Command, Gap, Key, LedgerLine
from chaos.schedule import VEHICLES
from gs.alarms.quality import QEvent
from tests.support.env import REPO
from tests.support.pg import rows
from tests.support.redisx import client

LIVE = list(VEHICLES)
LEDGER = REPO / "chaos" / "out" / "ledger" / "sim.jsonl"  # the overlay's bind mount
TAIL_BYTES = 4096


class LedgerTail:
    """Reads the ledger incrementally; a half-written last line waits for the next read."""

    def __init__(self, path: Path, since: datetime) -> None:
        self.path, self.since = path, since
        self.lines: list[LedgerLine] = []
        # start at the last line boundary now: the file is never truncated (the simulator holds
        # it open for append, and a host-side truncate leaves NULs on Docker Desktop's shares)
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
        self.pos = size - len(tail) + tail.rfind(b"\n") + 1

    def read(self) -> list[LedgerLine]:
        with self.path.open("rb") as f:
            f.seek(self.pos)
            data = f.read()
        end = data.rfind(b"\n") + 1
        for raw in data[:end].splitlines():
            d = json.loads(raw)
            t = datetime.fromisoformat(d["t"])
            if t >= self.since:
                self.lines.append(LedgerLine(d["vehicle"], d["seq"], t, d["fate"]))
        self.pos += end
        return self.lines


def stored_keys(since: datetime) -> list[Key]:
    q = "select vehicle, t_onboard, seq from telemetry where t_onboard >= %s and vehicle = any(%s)"
    return [(r["vehicle"], r["t_onboard"], int(r["seq"])) for r in rows(q, since, LIVE)]


def gap_events(since: datetime) -> list[Gap]:
    q = (
        "select vehicle, details from events where kind = 'GAP' and created_at >= %s"
        " and vehicle = any(%s)"
    )
    return [
        Gap(r["vehicle"], int(r["details"]["from_seq"]), int(r["details"]["lost"]))
        for r in rows(q, since, LIVE)
    ]


def alarm_rows(since: datetime) -> list[AlarmRow]:
    q = (
        "select alarm_id, vehicle, param, kind from alarm_events where alarm_id is not null"
        " and processed_at >= %s and vehicle = any(%s) order by id"
    )
    return [
        AlarmRow(str(r["alarm_id"]), r["vehicle"], r["param"], r["kind"])
        for r in rows(q, since, LIVE)
    ]


def alarm_qevents(since: datetime) -> list[QEvent]:
    q = (
        "select vehicle, param, kind, coalesce(trigger_t_onboard, processed_at) as t, alarm_id"
        " from alarm_events where alarm_id is not null and processed_at >= %s"
        " and vehicle = any(%s) order by id"
    )
    return [
        QEvent(r["vehicle"], r["param"], r["kind"], r["t"], r["alarm_id"])
        for r in rows(q, since, LIVE)
    ]


def commands(since: datetime) -> list[Command]:
    q = "select queued_at, state, failure_reason from commands where queued_at >= %s"
    return [Command(r["queued_at"], r["state"], r["failure_reason"]) for r in rows(q, since)]


def dlq(since: datetime) -> list[tuple[datetime, str]]:
    entries: Any = client("admin").xrange("tm:dlq", min=str(int(since.timestamp() * 1000)))
    out = []
    for entry_id, fields in entries:
        t = datetime.fromtimestamp(int(entry_id.split("-")[0]) / 1000, UTC)
        out.append((t, str(json.loads(fields["data"])["reason"])))
    return out
