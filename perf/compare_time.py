"""EXT-02 / ATP-E2 (a) (D-EXT-11): timed `GET /api/compare` calls, as fc1, against the stack that
is up, for ONE source per run: run it with `hot` for two finished sessions while their rows are
still hot, archive them (`HOT_RETENTION_H=0`), and run it again with `parquet` for the same two
sessions. Writes docs/perf/compare-<source>-<UTC date>.csv and prints n, p95 and max (the pass
condition is p95 < 2 s, A-R2R-8: judged on the reference machine, never asserted here).
The chart paint time is not measured here: it comes from the `e2e/compare.spec.ts` log and is
reported next to this p95 (the coordinator's ruling), not added per sample.
`uv run python -m perf.compare_time <hot|parquet> <ids> [samples]`, ids like `12,13`
(a few samples is for a dry run only)."""

import http.cookiejar
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime

from perf.run_n import REPO, read_env
from perf.stats import ci_text, verdict_of, write_csv

SAMPLES = 60  # EX-18
LIMIT_S = 2.0
SOURCES = ("hot", "parquet")
PARAMS = "cabin_temp_c"


def timed(
    env: dict[str, str], ids: str, samples: int, params: str
) -> list[tuple[datetime, float, int]]:
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
    for _ in range(samples):
        t = time.perf_counter()
        body = opener.open(f"{base}/api/compare?sessions={ids}&params={params}", timeout=120)
        points = sum(len(x["buckets"]) for x in json.loads(body.read())["series"])
        out.append((datetime.now(UTC), time.perf_counter() - t, points))
    return out


def main(source: str, ids: str, samples: int = SAMPLES, params: str = PARAMS) -> None:
    if source not in SOURCES:
        raise SystemExit(f"source must be one of {SOURCES}, got {source!r}")
    rows = timed(read_env(), ids, samples, params)
    path = REPO / "docs" / "perf" / f"compare-{source}-{datetime.now(UTC):%Y-%m-%d}.csv"
    write_csv(path, ["at", "seconds", "buckets"], rows)
    xs = [s for _, s, _ in rows]
    v = verdict_of(xs, LIMIT_S)
    print(
        f"{source}: n={len(xs)} p95 {v.p95:.3f} s (limit {LIMIT_S:g} s), {ci_text(v)}, "
        f"max {max(xs):.3f} s"
    )
    print(f"wrote {path.relative_to(REPO)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else SAMPLES)
