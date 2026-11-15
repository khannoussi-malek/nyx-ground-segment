"""Run N (D-PERF-04/05): 2 vehicles x 10 Hz for 10 minutes, `step` faults on cabin_temp_c every
5 s, the screen-latency spec alongside, raw CSVs and a summary in docs/perf/run-n-<UTC date>/.
`make perf` runs it against the stack that is up; the load driver shares the machine with the
system under test (D-PERF-08: one laptop cannot keep them apart), which the summary says."""

import csv
import http.cookiejar
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from gs.common.time import iso_z
from perf.stats import after_warmup, p95, write_csv

DURATION_S = 600
STEP_EVERY_S, STEP_S, STEP_VALUE = 5.0, 1.0, 31.0  # cabin_temp_c red_high is 30
STEP_PARAM, STEP_VEHICLE = "cabin_temp_c", "NYX-TA1"
VEHICLES = ("NYX-TA1", "NYX-TA2")
QUERIES, QUERY_PARAM = 60, "cabin_pressure_kpa"
LIMITS_MS = {
    "ingest": 200.0,
    "alarm": 300.0,
    "ui": 1000.0,
    "query": 300.0,
}  # ING-09, ALM-05, UI-01, DAT-03
REPO = Path(__file__).resolve().parent.parent


N_STEPS = int(DURATION_S / STEP_EVERY_S)  # 120 alarms (D-PERF-04)


def step_schedule(t0: float) -> list[float]:
    """Start times of the 120 one-second steps, 5 s apart; the last ends inside the run."""
    return [t0 + i * STEP_EVERY_S for i in range(N_STEPS)]


def verdict(
    name: str,
    samples_ms: Sequence[float],
    limit_ms: float,
    excluded: int | None = None,
    expected_n: int | None = None,
) -> str:
    """`excluded`: samples dropped as warmup (only the ingest series has a warmup);
    `expected_n`: the sample count the run must produce, any other count fails (D-PERF-04)."""
    head = f"- **{name}**: "
    if not samples_ms:
        return head + "NO SAMPLES (FAIL)"
    p = p95(samples_ms)
    ok = p <= limit_ms and (expected_n is None or len(samples_ms) == expected_n)
    text = f"{head}n = {len(samples_ms)}, p95 = {p:.1f} ms against {limit_ms:g} ms: "
    text += "**PASS**" if ok else "**FAIL**"
    if expected_n is not None and len(samples_ms) != expected_n:
        text += f" (expected {expected_n} samples, got {len(samples_ms)}: INCOMPLETE series)"
    if excluded is not None:
        text += f" (first 10 s excluded: {excluded} samples)"
    return text


def _env() -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in (REPO / ".env").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    return {k: v for k, v in pairs}


def _digests() -> str:
    r = subprocess.run(
        ["docker", "compose", "images", "--format", "json"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if r.returncode != 0:
        return f"unavailable: {r.stderr.strip()[:200]}"
    text = r.stdout.strip()
    items = json.loads(text) if text.startswith("[") else [json.loads(x) for x in text.splitlines()]
    return "\n".join(
        f"  - {i.get('ContainerName', '?')}: {i.get('Repository')}:{i.get('Tag')} {i.get('ID')}"
        for i in items
    )


def _spec() -> str:
    return (
        f"{platform.platform()}, {platform.machine()}, {os.cpu_count()} CPUs, "
        f"Python {platform.python_version()}"
    )


def _load() -> str:
    return ", ".join(f"{x:.2f}" for x in os.getloadavg())


def _post_step(sim_port: str) -> None:
    body = {
        "vehicle": STEP_VEHICLE, "fault": "step",
        "params": {"param": STEP_PARAM, "value": STEP_VALUE, "duration_s": STEP_S},
    }  # fmt: skip
    req = urllib.request.Request(
        f"http://127.0.0.1:{sim_port}/sim/fault", json.dumps(body).encode(),
        {"content-type": "application/json"}, method="POST",
    )  # fmt: skip
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 204, r.status


def _drive_steps(sim_port: str) -> None:
    for at in step_schedule(time.monotonic()):
        time.sleep(max(0.0, at - time.monotonic()))
        _post_step(sim_port)
    time.sleep(max(0.0, STEP_EVERY_S))  # the last step's alarm settles


def _query_times(env: dict[str, str], now: datetime) -> list[tuple[datetime, float]]:
    """QUERIES timed 1 h range queries through the REST route, as fc1 (DAT-03)."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    base = f"http://127.0.0.1:{env['API_HOST_PORT']}"
    login = urllib.request.Request(
        f"{base}/api/login",
        json.dumps({"user": "fc1", "password": env["DEMO_PW_FC1"]}).encode(),
        {"content-type": "application/json", "X-GS-Client": "console"},
    )
    opener.open(login, timeout=10).close()
    url = (
        f"{base}/api/telemetry/{STEP_VEHICLE}/{QUERY_PARAM}"
        f"?from={iso_z(now - timedelta(hours=1))}&to={iso_z(now)}&width=1500"
    )
    out = []
    for _ in range(QUERIES):
        t = time.perf_counter()
        opener.open(url, timeout=30).read()
        out.append((datetime.now(UTC), (time.perf_counter() - t) * 1000.0))
    return out


def _samples(
    db: psycopg.Connection, query: str, args: tuple[object, ...]
) -> list[tuple[datetime, float]]:
    return [(r[0], float(r[1])) for r in db.execute(query, args)]


INGEST_SQL = (
    "SELECT t_onboard, 1000 * extract(epoch FROM t_received - t_onboard) FROM telemetry"
    " WHERE vehicle = ANY(%s) AND t_onboard >= %s AND t_onboard < %s ORDER BY t_onboard"
)
ALARM_SQL = (
    "SELECT processed_at, 1000 * extract(epoch FROM processed_at - trigger_t_onboard)"
    " FROM alarm_events WHERE vehicle = %s AND param = %s AND level LIKE 'RED%%'"
    " AND kind IN ('RAISE', 'ESCALATE') AND processed_at >= %s ORDER BY id"
)  # a transition to RED of the stepped parameter: processed_at - the triggering frame's t_onboard


def _clock(db: psycopg.Connection) -> datetime:
    row = db.execute("SELECT clock_timestamp()").fetchone()  # the VM clock (D-PERF-01)
    assert row is not None
    now: datetime = row[0]
    return now


def _summary(
    out: Path, ctx: dict[str, str], results: list[str], n_alarms: int, incomplete: str | None
) -> str:
    banner = f"**INCOMPLETE RUN: {incomplete}**\n\n" if incomplete else ""
    return (
        f"# Run N, {out.name}\n\n{banner}"
        f"2 vehicles x 10 Hz, {DURATION_S} s, `step` of {STEP_PARAM} to {STEP_VALUE:g} for "
        f"{STEP_S:g} s every {STEP_EVERY_S:g} s ({n_alarms} RED transitions recorded).\n\n"
        "## Result\n" + "\n".join(results) + "\n\n"
        f"## Context\n- Machine: {ctx['spec']}\n"
        f"- Load average before / after: {ctx['load_before']} / {ctx['load_after']}\n"
        f"- Image digests:\n{ctx['digests']}\n"
        "- The load driver (this script and the browser) runs on the machine under test; one "
        "laptop cannot keep them apart (D-PERF-08).\n"
        "- The 10 s warmup exclusion applies to the ingest series only; the other series have no "
        "warmup rule.\n"
        "- Not in this run, Gate 4: the p95 confidence interval from pooled samples (D-PERF-10) "
        "and the takeover-window samples (D-PERF-07). Percentiles here are point values.\n"
        "- Screen latency is `age_ms` (API, VM clock) plus `paint_ms` (browser clock, double "
        "rAF heuristic); localhost socket transit is left out (D-PERF-02).\n"
        "- Reproduce: `make perf`. Raw samples: ingest.csv, alarm.csv, ui.csv, query.csv.\n"
    )


def main() -> None:
    env = _env()
    dsn = f"postgresql://gs_app:{env['GS_APP_PASSWORD']}@127.0.0.1:{env['DB_HOST_PORT']}/gs"
    out = REPO / "docs" / "perf" / f"run-n-{datetime.now(UTC):%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    ctx = {"spec": _spec(), "digests": _digests(), "load_before": _load()}
    with psycopg.connect(dsn, autocommit=True) as db:
        t0 = _clock(db)
        ui = subprocess.Popen(
            ["npx", "--prefix", "e2e", "playwright", "test", "-c", "perf/playwright.config.ts"],
            cwd=REPO, env={**os.environ, "PERF_OUT": str(out), "PERF_DURATION_S": str(DURATION_S)},
        )  # fmt: skip
        error: str | None = None
        try:
            _drive_steps(env["SIM_HOST_PORT"])
        except (
            OSError,
            AssertionError,
        ) as e:  # a partial summary is still written, marked INCOMPLETE
            error = f"{type(e).__name__}: {e}"
        finally:
            if error is not None and ui.poll() is None:
                ui.terminate()  # the browser must not keep running after the driver failed
                try:
                    ui.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    ui.kill()
                    ui.wait()
        ui_rc = ui.wait()
        end = _clock(db)
        queries = _query_times(env, end)
        ingest = _samples(db, INGEST_SQL, (list(VEHICLES), t0, end))
        alarm = _samples(db, ALARM_SQL, (STEP_VEHICLE, STEP_PARAM, t0))
    ctx["load_after"] = _load()

    write_csv(out / "ingest.csv", ["t_onboard", "latency_ms"], ingest)
    write_csv(out / "alarm.csv", ["processed_at", "latency_ms"], alarm)
    write_csv(out / "query.csv", ["at", "ms"], queries)
    ing = after_warmup(ingest, t0)
    ms = [v for _, v in alarm]
    results = [
        verdict("ingest ING-09 (both vehicles pooled)", [v for _, v in ing],
                LIMITS_MS["ingest"], excluded=len(ingest) - len(ing)),
        verdict("alarm ALM-05, first 20", ms[:20], LIMITS_MS["alarm"]),
        verdict("alarm ALM-05, all steps", ms, LIMITS_MS["alarm"], expected_n=N_STEPS),
        verdict("screen UI-01 (age + paint)", _ui_total_ms(out / "ui.csv"), LIMITS_MS["ui"]),
        verdict("query DAT-03 (1 h range)", [v for _, v in queries], LIMITS_MS["query"]),
    ]  # fmt: skip
    if error is None and ui_rc != 0:
        error = f"ui latency spec failed (exit {ui_rc})"
    (out / "summary.md").write_text(_summary(out, ctx, results, len(alarm), error))
    print((out / "summary.md").read_text())
    if error is not None:
        sys.exit(f"run N INCOMPLETE: {error}")


def _ui_total_ms(path: Path) -> list[float]:
    if not path.exists():
        return []
    rows = csv.DictReader(path.open())
    return [float(r["total_ms"]) for r in rows if r["total_ms"]]


if __name__ == "__main__":
    main()
