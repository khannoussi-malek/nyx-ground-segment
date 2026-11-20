"""EXT-02 / EX-02 (D-EXT-13): UI-01 while a 24 h CSV export runs. The screen-latency spec of run N
runs for PERF_DURATION_S against the stack that is up, while this driver exports the last 24 h of
NYX-TA1 as CSV, again and again, as fc1. If the export held the API's event loop, `hb` would stop
and the latency samples would show it. Writes docs/perf/ui-during-export-<UTC date>.csv (the spec's
samples) and prints the verdict; the pass/fail call is made on the reference machine (A-R2R-8).
`uv run python -m perf.ui_latency_during_export [duration_s]` (a few seconds is for a dry run)."""

import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gs.common.time import iso_z
from perf.run_n import LIMITS_MS, REPO, _ui_total_ms, read_env, verdict

DURATION_S = 600
VEHICLE = "NYX-TA1"


def opener(env: dict[str, str], base: str) -> urllib.request.OpenerDirector:
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.request.Request(
        f"{base}/api/login",
        json.dumps({"user": "fc1", "password": env["DEMO_PW_FC1"]}).encode(),
        {"content-type": "application/json", "X-GS-Client": "console"},
    )
    o.open(login, timeout=10).close()
    return o


def export_until(ui: subprocess.Popen[bytes], env: dict[str, str]) -> list[float]:
    """Seconds of each 24 h export, one after the other, until the UI run ends."""
    base = f"http://127.0.0.1:{env['API_HOST_PORT']}"
    o, seconds = opener(env, base), []
    while ui.poll() is None:
        now = datetime.now(UTC)
        q = f"vehicle={VEHICLE}&from={iso_z(now - timedelta(hours=24))}&to={iso_z(now)}&format=csv"
        t = time.perf_counter()
        try:
            o.open(f"{base}/api/export?{q}", timeout=600).read()
            seconds.append(time.perf_counter() - t)
        except urllib.error.HTTPError as e:
            print(f"export answered {e.code}", file=sys.stderr)
            time.sleep(1)
    return seconds


def main(duration_s: int = DURATION_S) -> None:
    env = read_env()
    out = Path(tempfile.mkdtemp(prefix="nyx-ui-export-"))
    try:
        ui = subprocess.Popen(
            ["npx", "--prefix", "e2e", "playwright", "test", "-c", "perf/playwright.config.ts"],
            cwd=REPO, env={**os.environ, "PERF_OUT": str(out), "PERF_DURATION_S": str(duration_s)},
        )  # fmt: skip
        seconds = export_until(ui, env)
        rc = ui.wait()
        dest = REPO / "docs" / "perf" / f"ui-during-export-{datetime.now(UTC):%Y-%m-%d}.csv"
        if (out / "ui.csv").exists():
            shutil.copy(out / "ui.csv", dest)
        print(verdict("screen UI-01 during exports", _ui_total_ms(out / "ui.csv"), LIMITS_MS["ui"]))
        print(f"{len(seconds)} exports, seconds: {[round(s, 1) for s in seconds]}; ui exit {rc}")
        print(f"wrote {dest.relative_to(REPO)}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DURATION_S)
