"""Run P (D-PERF-05/06/08, ING-10): 2 vehicles x 100 Hz for 5 minutes. The simulator is restarted
at SIM_RATE_HZ=100, the ingest lag is sampled at 1 Hz (XINFO GROUPS lag + pending, and the time
lag `clock_timestamp() - max(t_onboard)` per vehicle), the other metrics are informative.
`make perf-p` runs it against the stack that is up and restores the 10 Hz simulator."""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import redis

from perf.run_n import (
    ALARM_SQL,
    INGEST_SQL,
    LIMITS_MS,
    REPO,
    STEP_PARAM,
    STEP_VEHICLE,
    VEHICLES,
    clock,
    digests,
    drive_steps,
    load,
    query_times,
    read_env,
    samples,
    spec,
    verdict,
)
from perf.stats import Verdict, after_warmup, nearest_rank, write_csv

log = logging.getLogger(__name__)
DURATION_S, RATE_HZ, SAMPLE_EVERY_S, LAG_LIMIT_S = 300, 100, 1.0, 1.0
MIN_SAMPLE_FRAC = 0.8
WORKERS = ("ingest-1", "ingest-2")
GROUP = "ingest"
LagRow = tuple[datetime, int, int, float]  # (t, entries lag, pending, time lag in s)
TIME_LAG_SQL = (
    "SELECT extract(epoch FROM clock_timestamp() - max(t_onboard)) FROM telemetry"
    " WHERE vehicle = %s AND t_onboard > clock_timestamp() - interval '1 minute'"
)


def lag_row(groups: Sequence[Mapping[str, Any]], time_lag_s: float, t: datetime) -> LagRow:
    """One sample of one vehicle: the ingest group's undelivered entries and pending count."""
    for g in groups:
        if g.get("name") in (GROUP, GROUP.encode()):
            return (t, int(g.get("lag") or 0), int(g.get("pending") or 0), time_lag_s)
    return (t, 0, 0, time_lag_s)


def lag_verdict(rows: Sequence[LagRow]) -> Verdict:
    """ING-10: the maximum time lag stays under 1 s. `p95` holds the maximum; no CI (a maximum)."""
    worst = max((r[3] for r in rows), default=float("inf"))
    return Verdict(worst, None, LAG_LIMIT_S, worst < LAG_LIMIT_S, False)


def totals(
    cpu: Mapping[str, Sequence[float]],
    mem_before: int | None,
    mem_after: int | None,
    ingest_ms: Sequence[float],
    frames: int,
    vehicles: int,
    duration_s: float,
) -> dict[str, Any]:
    hours = vehicles * duration_s / 3600
    return {
        "cpu_pct": {w: {"mean": sum(xs) / len(xs), "max": max(xs)} for w, xs in cpu.items() if xs},
        "redis_used_memory": {
            "before": mem_before,
            "after": mem_after,
            "per_vehicle_hour": (
                None
                if mem_before is None or mem_after is None
                else (mem_after - mem_before) / hours
            ),
        },  # fmt: skip
        "ingest_ms": {
            "p50": nearest_rank(ingest_ms, 0.5) if ingest_ms else None,
            "p95": nearest_rank(ingest_ms, 0.95) if ingest_ms else None,
        },  # t_received - t_onboard, informative
        "frames_stored": frames,
    }


def _compose(*args: str, rate: int) -> None:
    subprocess.run(
        ["docker", "compose", *args], cwd=REPO, check=True,
        env={**os.environ, "SIM_RATE_HZ": str(rate)},
    )  # fmt: skip


def set_sim_rate(rate: int) -> None:
    _compose("up", "-d", "--force-recreate", "--wait", "simulator", rate=rate)


def parse_cpu(stdout: str, ids: Mapping[str, str]) -> dict[str, float]:
    """`<id> <cpu%>` lines per worker; a bad line ("--", blank, mid-restart) is skipped."""
    pct: dict[str, float] = {}
    for ln in stdout.splitlines():
        try:
            i, p = ln.split()
            pct[i[:12]] = float(p.rstrip("%"))
        except ValueError:
            continue
    return {w: pct[i[:12]] for w, i in ids.items() if i[:12] in pct}


def _cpu_once(ids: Mapping[str, str]) -> dict[str, float]:
    r = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.ID}} {{.CPUPerc}}", *ids.values()],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    return parse_cpu(r.stdout, ids)


def _cpu_sampler(stop: threading.Event, out: dict[str, list[float]]) -> None:
    """One `docker stats` per second on a fixed grid; each call (1-2 s long) runs in a pool so a
    slow call does not push the next tick back."""
    ids = {
        w: subprocess.run(
            ["docker", "compose", "ps", "-q", w], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
        for w in WORKERS
    }
    ids = {w: i for w, i in ids.items() if i}
    nxt, futures = time.monotonic(), []
    with ThreadPoolExecutor(max_workers=4) as pool:
        while not stop.is_set():
            futures.append(pool.submit(_cpu_once, ids))
            nxt += SAMPLE_EVERY_S
            stop.wait(max(0.0, nxt - time.monotonic()))
    for f in futures:
        try:  # a failed call loses one sample; incomplete() judges the count
            got = f.result()
        except OSError as e:
            log.warning("cpu sample lost: %s", e)
            continue
        for w, v in got.items():
            out[w].append(v)


def _background(errors: list[str], fn: Callable[..., object], *args: object) -> threading.Thread:
    """Run `fn` in a thread; an exception is recorded in `errors` instead of dying silently."""

    def run() -> None:
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - recorded, surfaces as the INCOMPLETE banner
            errors.append(f"{getattr(fn, '__name__', 'thread')}: {type(e).__name__}: {e}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def incomplete(
    lag: Mapping[str, Sequence[object]],
    cpu: Mapping[str, Sequence[float]],
    ingest_n: int,
    duration_s: float,
) -> list[str]:
    """The series the run failed to fill: 1 Hz lag per vehicle and CPU per worker need
    MIN_SAMPLE_FRAC of the expected count, ingest needs any rows."""
    need = int(MIN_SAMPLE_FRAC * duration_s / SAMPLE_EVERY_S)
    out = [f"lag {v}: {len(rs)} of ~{need}" for v, rs in lag.items() if len(rs) < need]
    out += [
        f"cpu {w}: {len(cpu.get(w, []))} of ~{need}" for w in WORKERS if len(cpu.get(w, [])) < need
    ]
    return out + ([] if ingest_n else ["ingest: no frames stored"])


def _used_memory(r: redis.Redis) -> int:
    return int(r.info("memory")["used_memory"])


def _sample_lag(
    r: redis.Redis, db: psycopg.Connection, rows: dict[str, list[LagRow]], until: float
) -> None:
    nxt = time.monotonic()
    while nxt < until:
        time.sleep(max(0.0, nxt - time.monotonic()))
        nxt += SAMPLE_EVERY_S
        for v in VEHICLES:
            row = db.execute(TIME_LAG_SQL, (v,)).fetchone()
            groups: Any = r.xinfo_groups(f"tm:{v}")
            lag = float(row[0]) if row and row[0] is not None else float("inf")
            rows[v].append(lag_row(groups, lag, datetime.now(UTC)))


def _summary(
    out: Path,
    ctx: Mapping[str, str],
    lines: Sequence[str],
    v: Verdict,
    tot: Mapping[str, Any],
    error: str | None,
) -> str:
    banner = f"**INCOMPLETE RUN: {error}**\n\n" if error else ""
    return (
        f"# Run P, {out.name}\n\n{banner}"
        f"{len(VEHICLES)} vehicles x {RATE_HZ} Hz, {DURATION_S} s, `step` faults of {STEP_PARAM} "
        f"alongside.\n\n## Result\n"
        f"- **ING-10 lag**: max time lag {v.p95:.3f} s against {LAG_LIMIT_S:g} s: "
        f"**{'PASS' if v.passed and error is None else 'FAIL'}**\n"
        + "\n".join(lines)
        + f"\n\n## Totals\n```json\n{json.dumps(tot, indent=2)}\n```\n\n"
        f"## Context\n- Machine: {ctx['spec']}\n"
        f"- Load average before / after: {ctx['load_before']} / {ctx['load_after']}\n"
        f"- Image digests:\n{ctx['digests']}\n"
        "- This is a shared laptop: other worktrees' stacks and tools may run during the sample.\n"
        "- The load driver (this script) runs on the machine under test; one laptop cannot keep "
        "them apart (D-PERF-08).\n"
        "- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 "
        "with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.\n"
        "- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, "
        "totals.json.\n"
    )


def main() -> None:
    env = read_env()
    dsn = f"postgresql://gs_app:{env['GS_APP_PASSWORD']}@127.0.0.1:{env['DB_HOST_PORT']}/gs"
    out = REPO / "docs" / "perf" / f"run-p-{datetime.now(UTC):%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    ctx = {"spec": spec(), "digests": digests(), "load_before": load()}
    r = redis.Redis(
        host="127.0.0.1", port=int(env["REDIS_HOST_PORT"]), username="ingest",
        password=env["REDIS_INGEST_PW"], decode_responses=True,
    )  # fmt: skip
    lag: dict[str, list[LagRow]] = {v: [] for v in VEHICLES}
    cpu: dict[str, list[float]] = {w: [] for w in WORKERS}
    errors: list[str] = []
    stop, queries, ing, alarms = threading.Event(), [], [], []
    mem0 = mem1 = None
    t0 = datetime.now(UTC)
    try:
        set_sim_rate(RATE_HZ)
        with psycopg.connect(dsn, autocommit=True) as db:
            t0, mem0 = clock(db), _used_memory(r)
            cpu_t = _background(errors, _cpu_sampler, stop, cpu)
            steps = _background(errors, drive_steps, env["SIM_HOST_PORT"], DURATION_S)
            _sample_lag(r, db, lag, time.monotonic() + DURATION_S)
            steps.join(timeout=30)
            end, mem1 = clock(db), _used_memory(r)
            stop.set()
            cpu_t.join(timeout=30)
            for name, t in (("step driver", steps), ("cpu sampler", cpu_t)):
                if t.is_alive():
                    errors.append(f"{name} did not finish")
            queries = query_times(env, end)
            ing = samples(db, INGEST_SQL, (list(VEHICLES), t0, end))
            alarms = samples(db, ALARM_SQL, (STEP_VEHICLE, STEP_PARAM, t0))
    except Exception as e:  # noqa: BLE001 - the artifacts are written whatever failed
        errors.append(f"{type(e).__name__}: {e}")
    finally:
        stop.set()
        try:
            set_sim_rate(10)  # restore 10 Hz
        except Exception as e:  # noqa: BLE001
            errors.append(
                f"restoring 10 Hz failed, the simulator may still run at {RATE_HZ} Hz: {e}"
            )
    errors += incomplete(lag, cpu, len(ing), DURATION_S) if not errors else []
    error = "; ".join(errors) or None
    ctx["load_after"] = load()

    rows = [(v, *row) for v, rs in lag.items() for row in rs]
    write_csv(out / "lag.csv", ["vehicle", "t", "lag", "pending", "time_lag_s"], rows)
    write_csv(out / "ingest.csv", ["t_onboard", "latency_ms"], ing)
    write_csv(out / "alarm.csv", ["processed_at", "latency_ms"], alarms)
    write_csv(out / "query.csv", ["at", "ms"], queries)
    ingest_ms = [v for _, v in after_warmup(ing, t0)]  # the first 10 s are excluded
    tot = totals(cpu, mem0, mem1, ingest_ms, len(ing), len(VEHICLES), DURATION_S)
    (out / "totals.json").write_text(json.dumps(tot, indent=2))
    pooled_rows = [row for rs in lag.values() for row in rs]
    lines = [
        verdict("ingest ING-09 (informative)", ingest_ms, LIMITS_MS["ingest"]),
        verdict("alarm ALM-05 (informative)", [v for _, v in alarms], LIMITS_MS["alarm"]),
        verdict("query DAT-03 (informative)", [v for _, v in queries], LIMITS_MS["query"]),
    ]
    (out / "summary.md").write_text(_summary(out, ctx, lines, lag_verdict(pooled_rows), tot, error))
    print((out / "summary.md").read_text())
    if error is not None:
        sys.exit(f"run P INCOMPLETE: {error}")
    if not lag_verdict(pooled_rows).passed:
        sys.exit("run P: ING-10 FAIL")


if __name__ == "__main__":
    main()
