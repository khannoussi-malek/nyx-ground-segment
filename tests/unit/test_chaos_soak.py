"""The soak's verdict and bookkeeping (D-EXT-15): a run that did not run its checks or its chaos
is not a PASS."""

import csv
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

import chaos.collect as collect
import chaos.soak as soak
from chaos.invariants import Result
from chaos.report import Tally, render
from chaos.schedule import Action

T = datetime(2026, 11, 20, 10, tzinfo=UTC)


def tally(skip: tuple[str, ...] = ()) -> Tally:
    t = Tally()
    for i in ("I1", "I2", "I3", "I5", "I6", "I7", "I8"):
        t.add(Result(i, True), skipped=i in skip)
    return t


def summary(t: Tally, incomplete: tuple[str, ...] = ()) -> str:
    r = render(1, 5, T, t, {}, None, None, [1.0], incomplete=incomplete)
    return r.split("\n")[2]


def test_verdict_passes_when_every_invariant_was_judged() -> None:
    assert summary(tally()) == "**PASS**"


def test_verdict_fails_when_an_invariant_had_no_sample() -> None:
    assert summary(tally(skip=("I1",))).startswith("**FAIL**")  # I1 shows NOT RUN


def test_verdict_fails_when_the_run_is_incomplete() -> None:
    assert summary(tally(), ("final pass skipped",)).startswith("**FAIL**")


def test_summary_states_p6_is_skipped() -> None:
    assert "P6 skipped" in render(1, 5, T, tally(), {}, None, None, [])


def test_shortfall_needs_15_actions_and_30_faults_from_30_min() -> None:
    ok = {"actions": Counter(a=15), "faults": Counter(f=30)}
    assert soak.shortfall(30, ok) == []
    low = {"actions": Counter(a=14), "faults": Counter(f=29)}
    assert len(soak.shortfall(30, low)) == 2
    assert soak.shortfall(5, low) == []  # a short run has no minimum


class Sh:
    def __init__(self, tmp: Path) -> None:
        self.sh, self.out = soak.Shared(), tmp
        (tmp / "actions.jsonl").write_text("")


@pytest.mark.parametrize("exc", [ValueError("x"), KeyError("k"), subprocess.TimeoutExpired("c", 1)])
def test_infra_loop_turns_any_error_into_an_i5_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise exc

    monkeypatch.setattr(soak, "run_action", boom)
    s = Sh(tmp_path)
    act = Action(at_s=0, kind="redis_stop", target="", duration_s=1)
    soak.infra_loop([act], time.monotonic(), s.sh, tmp_path)
    r = s.sh.results.get_nowait()
    assert r.name == "I5" and not r.ok and type(exc).__name__ in r.detail


def test_skipped_sample_records_every_table_invariant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*_a: Any) -> Any:
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(collect, "stored_keys", down)
    t = Tally()
    sam = object.__new__(soak.Sampler)
    sam.sh, sam.tally, sam.started = soak.Shared(), t, T
    sam.csv = (tmp_path / "i.csv").open("w")
    sam.writer, sam.quality, sam.violation = csv.writer(sam.csv), None, None
    sam.last_skipped = False
    sam._tables(T, None, T, [])
    assert {k for k, v in t.skipped.items() if v} == {"I1", "I2", "I3", "I7", "I8"}
    assert sam.last_skipped
