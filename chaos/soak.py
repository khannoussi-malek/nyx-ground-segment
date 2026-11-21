"""Chaos soak (D-EXT-15, design 11.5): both vehicles at 10 Hz, a seeded simulator-fault schedule
and a seeded infrastructure-action schedule, operator traffic, invariants I1-I3 and I5-I8 sampled
every 5 s and at the end. Stops at the first violation and prints seed, time and the action log.
I4 (console never shows OK for old data) is the Playwright watcher's, not this script's.

    make chaos SEED=<n> MINUTES=<m>        (default 30)
    uv run python -m chaos.soak --seed <n> --minutes <m>   (against `make chaos`'s stack)
"""

import argparse
import csv
import json
import os
import queue
import random
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import redis

from chaos import collect
from chaos.actions import RECOVERY_S, restore_stack, run_action, wait_recovered
from chaos.collect import LEDGER
from chaos.invariants import (
    Result,
    check_i1,
    check_i2,
    check_i3_paths,
    check_i3_recompute,
    check_i6,
    check_i7,
    check_i8,
    cutoff,
    fault_window,
)
from chaos.operator import Operator
from chaos.recompute import recompute_all
from chaos.report import Tally, render
from chaos.schedule import QUIET_S, VEHICLES, Action, Fault, fault_schedule, infra_schedule
from gs.alarms.quality import FaultWindow, Quality
from tests.support.env import REPO
from tests.support.sim import post_fault

OUT_ROOT = REPO / "chaos" / "out"
COMPOSE_FILES = "docker-compose.yml:docker-compose.chaos.yml"
SAMPLE_S, ACK_S, ISOLATE_S = 5.0, 2.0, 60.0
FINAL_WAIT_S = 150.0


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Shared:
    """What the infrastructure thread and the main loop both touch."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    infra_end: datetime | None = None
    in_flight: bool = False
    windows: list[FaultWindow] = field(default_factory=list)
    counts: dict[str, Counter[str]] = field(
        default_factory=lambda: {"faults": Counter(), "actions": Counter()}
    )
    recoveries: list[float] = field(default_factory=list)
    results: queue.Queue[Result] = field(default_factory=queue.Queue)  # I5, from the thread
    log: list[dict[str, Any]] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)

    def note(self, out: Path, **entry: Any) -> None:
        entry = {"time": now().isoformat(), **entry}
        with self.lock:
            self.log.append(entry)
            with (out / "actions.jsonl").open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

    def infra_edge(self) -> datetime | None:
        """The `infra_end` the checks use: now while an action is in flight."""
        with self.lock:
            return now() if self.in_flight else self.infra_end


def infra_loop(acts: list[Action], t0: float, sh: Shared, out: Path) -> None:
    ready = t0
    for a in acts:
        while time.monotonic() < max(t0 + a.at_s, ready):  # the schedule, then the quiet period
            if sh.stop.wait(0.5):
                return
        start = now()
        with sh.lock:
            sh.in_flight = True
            sh.counts["actions"][a.kind] += 1
        try:
            run_action(a, sleep=sh.stop.wait)  # a stop ends the wait; the undo still runs
        except (subprocess.CalledProcessError, psycopg.Error, OSError, AssertionError) as e:
            sh.results.put(Result("I5", False, f"action {a.kind} {a.target} failed: {e}"))
            return
        end = now()
        if sh.stop.is_set():  # undone; the soak is over, nobody reads a recovery time
            return
        with sh.lock:
            sh.in_flight, sh.infra_end = False, end
        took = wait_recovered()
        sh.note(out, action=a.kind, target=a.target, duration_s=a.duration_s, start=start,
                end=end, recovered_s=took)  # fmt: skip
        if took is None:
            sh.results.put(Result("I5", False, f"{a.kind} {a.target}: not recovered in 60 s"))
            return
        sh.recoveries.append(took)
        sh.results.put(Result("I5", True, f"{a.kind} {took:.1f} s"))
        ready = time.monotonic() + QUIET_S


def fire(f: Fault, sh: Shared, out: Path) -> None:
    start = now()
    try:
        status = post_fault(f.vehicle, f.fault, **f.params)
    except OSError:  # the simulator is restarting
        status = -1
    sh.note(out, fault=f.fault, vehicle=f.vehicle, params=f.params, status=status)
    if status != 204:
        return
    with sh.lock:
        sh.counts["faults"][f.fault] += 1
        d = float(f.params.get("duration_s", 0))
        if w := fault_window(f.vehicle, f.fault, start, now() + timedelta(seconds=d)):
            sh.windows.append(w)


class Sampler:
    def __init__(self, sh: Shared, out: Path, tally: Tally) -> None:
        self.sh, self.tally = sh, tally
        # the window starts where the tail does: frames between `started` and here are unread
        self.tail = collect.LedgerTail(LEDGER, now())
        self.started = self.tail.since
        self.csv = (out / "invariants.csv").open("w", newline="")
        self.writer = csv.writer(self.csv)
        self.writer.writerow(["time", "invariant", "value", "ok"])
        self.quality: Quality | None = None
        self.violation: str | None = None

    def record(self, r: Result, skipped: bool = False) -> None:
        self.tally.add(r, skipped)
        value = ("skipped: " if skipped else "") + r.detail
        self.writer.writerow([now().isoformat(), r.name, value, r.ok or skipped])
        if not r.ok and not skipped and self.violation is None:
            self.violation = f"{r.name}: {r.detail}"

    def sample(self) -> None:
        t, edge = now(), self.sh.infra_edge()
        due = cutoff(t, edge)
        ledger = self.tail.read()
        self._tables(t, edge, due, ledger)
        try:
            self._dlq(due, ledger)
        except redis.RedisError as e:  # the broker is down: nothing to read, not a violation
            self.record(Result("I6", True, type(e).__name__), skipped=True)

    def _tables(
        self, t: datetime, edge: datetime | None, due: datetime | None, ledger: Any
    ) -> None:
        try:
            stored = collect.stored_keys(self.started)
            if due is not None:
                self.record(check_i1(ledger, stored, collect.gap_events(self.started), due))
                self.record(check_i2(ledger, stored, due))
            self.record(check_i3_paths(collect.alarm_rows(self.started)))
            self.record(check_i7(collect.commands(self.started), t, edge))
            with self.sh.lock:
                windows = list(self.sh.windows)
            r8, self.quality = check_i8(collect.alarm_qevents(self.started), windows)
            self.record(r8)
        except psycopg.Error as e:  # a probe that fails is a skipped sample
            self.record(Result("I1", True, type(e).__name__), skipped=True)

    def _dlq(self, due: datetime | None, ledger: Any) -> None:
        if due is not None:
            self.record(check_i6(collect.dlq(self.started), ledger, due))

    def drain_i5(self) -> None:
        while not self.sh.results.empty():
            self.record(self.sh.results.get())


def print_failure(seed: int, s: Sampler, sh: Shared) -> None:
    print(f"\nCHAOS FAIL seed={seed} at {now().isoformat()}: {s.violation}", file=sys.stderr)
    print("action log:", file=sys.stderr)
    for e in sh.log:
        print("  " + json.dumps(e, default=str), file=sys.stderr)


def run(seed: int, minutes: float, out_root: Path = OUT_ROOT) -> int:
    os.environ["COMPOSE_FILE"] = COMPOSE_FILES
    if not LEDGER.exists():
        sys.exit(
            "stack not up with the chaos overlay: run `make chaos` (see docker-compose.chaos.yml)"
        )
    # a crashed earlier run may have left Redis stopped, a network cut or Postgres read-only
    for f in restore_stack():
        print(f"restore at start: {f}", file=sys.stderr)
    if wait_recovered() is None:
        sys.exit("stack not healthy after restore: check `docker compose ps`")
    started = now()
    out = out_root / f"{started:%Y-%m-%d}-{seed}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "actions.jsonl").write_text("")
    print(f"chaos soak seed={seed} minutes={minutes:g} out={out}", flush=True)

    sh, tally = Shared(), Tally()
    s = Sampler(sh, out, tally)
    faults, acts = fault_schedule(seed, minutes), infra_schedule(seed, minutes)
    t0, total = time.monotonic(), minutes * 60
    thread = threading.Thread(target=infra_loop, args=(acts, t0, sh, out), daemon=True)
    op = Operator()
    fi, next_sample, next_ack, next_iso, minute = 0, SAMPLE_S, ACK_S, ISOLATE_S / 2, 0
    try:
        thread.start()
        while s.violation is None and (time.monotonic() - t0 < total or thread.is_alive()):
            el = time.monotonic() - t0
            while fi < len(faults) and faults[fi].at_s <= el:
                fire(faults[fi], sh, out)
                fi += 1
            if el >= next_iso and el < total:
                op.isolate(VEHICLES[int(next_iso // (ISOLATE_S / 2)) % 2])
                next_iso += ISOLATE_S / 2  # one vehicle every 30 s: each every 60 s
            if el >= next_ack:
                op.ack_reds()
                next_ack += ACK_S
            if el >= next_sample:
                s.drain_i5()
                s.sample()
                next_sample += SAMPLE_S
            if el // 60 > minute:
                minute = int(el // 60)
                nf, na = sum(sh.counts["faults"].values()), sum(sh.counts["actions"].values())
                print(
                    f"  {minute} min: faults {nf}, actions {na}, violation: {s.violation}",
                    flush=True,
                )
            time.sleep(0.25)
        if s.violation is None:
            final(s, sh)
    finally:
        sh.stop.set()
        thread.join(timeout=RECOVERY_S + 30)  # its action ends and undoes itself
        for f in restore_stack():  # whatever is still pending (idempotent, also after a PASS)
            print(f"restore at end: {f}", file=sys.stderr)
        s.csv.close()
    counts = {k: dict(v) for k, v in sh.counts.items()}
    counts["operator"] = {"isolate_valve sent": op.sent, "acks": op.acked, "failed": op.failed}
    (out / "summary.md").write_text(
        render(seed, minutes, started, tally, counts, s.quality, s.violation, sh.recoveries)
    )
    if s.violation:
        print_failure(seed, s, sh)
        return 1
    print(f"chaos soak PASS seed={seed}: summary in {out / 'summary.md'}")
    return 0


def final(s: Sampler, sh: Shared) -> None:
    """Everything the simulator sent before the end is due: wait for it, then one last pass."""
    end = now()
    deadline = time.monotonic() + FINAL_WAIT_S
    while time.monotonic() < deadline:
        d = cutoff(now(), sh.infra_edge())
        if d is not None and d >= end:
            break
        time.sleep(2)
    s.drain_i5()
    s.sample()
    if s.violation is None:  # I3 end of run (D-REP-05 b): the whole history, from the initial state
        try:
            s.record(check_i3_recompute(*recompute_all()))
        except (psycopg.Error, OSError, AssertionError, KeyError) as e:
            s.record(Result("I3", False, f"recompute failed: {type(e).__name__}: {e}"))


def main() -> None:
    def term(*_: object) -> None:
        raise KeyboardInterrupt  # SIGTERM runs the `finally` that undoes the pending action

    signal.signal(signal.SIGTERM, term)
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=int(os.environ.get("CHAOS_SEED") or 0) or None)
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("CHAOS_MINUTES") or 30))
    a = ap.parse_args()
    sys.exit(
        run(a.seed if a.seed is not None else random.SystemRandom().randrange(1, 2**31), a.minutes)
    )


if __name__ == "__main__":
    main()
