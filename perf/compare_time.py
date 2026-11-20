"""EXT-02 / ATP-E2 (D-EXT-11): timed `GET /api/compare` calls, as fc1, against the stack that is
up, once per source: sessions whose data is still hot and sessions whose data is archived. The
sessions are given (ids of two finished sessions per source); the time is recorded for the
reference-machine verdict (A-R2R-8), never asserted here. Writes docs/perf/compare-<UTC date>.csv.
`uv run python -m perf.compare_time <hot ids> <parquet ids> [samples]`, ids like `12,13`
(a few samples is for a dry run only)."""

import http.cookiejar
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime

from perf.run_n import REPO, read_env
from perf.stats import write_csv

SAMPLES = 60  # EX-18
PARAMS = "cabin_temp_c"


def timed(
    env: dict[str, str], sources: dict[str, str], samples: int, params: str
) -> list[tuple[str, datetime, float, int]]:
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
    for source, ids in sources.items():
        for _ in range(samples):
            t = time.perf_counter()
            body = opener.open(f"{base}/api/compare?sessions={ids}&params={params}", timeout=120)
            points = sum(len(s["buckets"]) for s in json.loads(body.read())["series"])
            out.append((source, datetime.now(UTC), time.perf_counter() - t, points))
    return out


def main(hot: str, parquet: str, samples: int = SAMPLES, params: str = PARAMS) -> None:
    rows = timed(read_env(), {"hot": hot, "parquet": parquet}, samples, params)
    path = REPO / "docs" / "perf" / f"compare-{datetime.now(UTC):%Y-%m-%d}.csv"
    write_csv(path, ["source", "at", "seconds", "buckets"], rows)
    for src in ("hot", "parquet"):
        xs = sorted(s for k, _, s, _ in rows if k == src)
        print(f"{src}: n={len(xs)} median {xs[len(xs) // 2]:.3f} s max {xs[-1]:.3f} s")
    print(f"wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else SAMPLES)
