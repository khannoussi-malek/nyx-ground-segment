"""R14 E-4: an ingest worker cut from only Redis, or from only Postgres, for 25 s. The invariants
I1-I3 and I6 hold afterwards (nothing lost, nothing stored twice, alarm paths valid) and nothing
reached the DLQ as poison. Needs the chaos overlay (Redis and Postgres on separate networks); the
fixture recreates the stack with it and restores the core topology at the end."""

import os
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest

from chaos import collect
from chaos.actions import run_action, wait_recovered
from chaos.invariants import (
    ACTION_GRACE,
    FRAME_GRACE,
    check_i1,
    check_i2,
    check_i3_paths,
    check_i6,
    cutoff,
)
from chaos.schedule import Action
from tests.support.compose import compose
from tests.support.env import now

CUT_S = 25.0
OVERLAY = "docker-compose.yml:docker-compose.chaos.yml"


@pytest.fixture(scope="module", autouse=True)
def chaos_stack() -> Iterator[None]:
    collect.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    collect.LEDGER.parent.chmod(0o777)  # the simulator runs as uid 10001
    os.environ["COMPOSE_FILE"] = OVERLAY
    try:
        compose("up", "-d", "--wait")
        assert wait_recovered() is not None
        yield
    finally:
        os.environ.pop("COMPOSE_FILE", None)
        compose("up", "-d", "--wait")  # back to the core topology


def cut_and_check(kind: str) -> None:
    since = now()
    time.sleep(1)  # the ledger tail starts at the last line boundary
    tail = collect.LedgerTail(collect.LEDGER, since)
    run_action(Action(0, kind, "ingest-1", CUT_S))
    end = now()
    assert wait_recovered() is not None, f"{kind}: not recovered within 60 s"
    time.sleep((ACTION_GRACE + FRAME_GRACE).total_seconds() + 2)  # everything sent is due
    due = cutoff(now(), end)
    assert due is not None and due > end + timedelta(seconds=20)
    ledger = tail.read()
    assert len(ledger) > 500  # the simulator really wrote its ledger
    stored = collect.stored_keys(since)
    results = [
        check_i1(ledger, stored, collect.gap_events(since), due),
        check_i2(ledger, stored, due),
        check_i3_paths(collect.alarm_rows(since)),
        check_i6(collect.dlq(since), ledger, due),  # poison = 0
    ]
    assert [r for r in results if not r.ok] == []


def test_cut_from_redis() -> None:
    cut_and_check("partition_redis")


def test_cut_from_db() -> None:
    cut_and_check("partition_db")
