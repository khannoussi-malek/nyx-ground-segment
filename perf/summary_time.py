"""EXT-05 / ATP-E5 (D-EXT-21): 20 timed `GET /api/summary` calls over a 12 h window, as fc1,
against the stack that is up; p95 must stay under 2 s. Writes docs/perf/summary-<UTC date>.csv.
`uv run python -m perf.summary_time [window_h]` (a shorter window is for a dry run only)."""

import http.cookiejar
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta

from gs.common.time import iso_z
from perf.run_n import REPO, read_env
from perf.stats import ci_text, verdict_of, write_csv

CALLS, WINDOW_H, LIMIT_S = 20, 12, 2.0
VEHICLE = "NYX-TA1"


def timed_calls(env: dict[str, str], window_h: float) -> list[tuple[datetime, float, int]]:
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
    out = []
    for _ in range(CALLS):
        since = iso_z(datetime.now(UTC) - timedelta(hours=window_h))
        t = time.perf_counter()
        body = opener.open(f"{base}/api/summary?vehicle={VEHICLE}&since={since}", timeout=30).read()
        out.append((datetime.now(UTC), time.perf_counter() - t, len(json.loads(body)["items"])))
    return out


def main(window_h: float = WINDOW_H) -> None:
    now = datetime.now(UTC)
    rows = timed_calls(read_env(), window_h)
    path = REPO / "docs" / "perf" / f"summary-{now:%Y-%m-%d}.csv"
    write_csv(path, ["at", "seconds", "items"], rows)
    v = verdict_of([s for _, s, _ in rows], LIMIT_S)
    print(f"summary over {window_h:g} h: p95 {v.p95:.3f} s (limit {LIMIT_S:g} s), {ci_text(v)}")
    print(f"items per call: min {min(n for _, _, n in rows)} (0 means the window was empty)")
    print(f"wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else WINDOW_H)
