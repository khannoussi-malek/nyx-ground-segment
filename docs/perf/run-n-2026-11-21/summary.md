# Run N, run-n-2026-11-21

2 vehicles x 10 Hz, 600 s, `step` of cabin_temp_c to 31 for 1 s every 5 s (120 RED transitions recorded).

## Result
- **ingest ING-09 (both vehicles pooled)**: n = 11868, p95 = 14.4 ms (95% CI [14.3, 14.5]) against 200 ms: **PASS** (first 10 s excluded: 200 samples)
- **alarm ALM-05, first 20**: n = 20, p95 = 15.0 ms (n too small for a two-sided CI) against 300 ms: **PASS**
- **alarm ALM-05, all steps**: n = 120, p95 = 15.6 ms (95% CI [15.0, 29.6]) against 300 ms: **PASS**
- **screen UI-01 (age + paint)**: n = 12008, p95 = 49.0 ms (95% CI [48.7, 49.3]) against 1000 ms: **PASS**
- **query DAT-03 (1 h range)**: n = 60, p95 = 14.9 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 2.48, 3.13, 3.58 / 3.33, 3.30, 3.46
- Image digests:
  - nyx-ext01-migrate-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-api-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-ext01-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx-ext01-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-ext01-archiver-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-simulator-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-ingest-2-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-ingest-1-1: nyx-gs:local sha256:53a0bdba7c85fde0623ae64861324135f7748e04acc64e644caffccc510e8000
  - nyx-ext01-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
- The load driver (this script and the browser) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- The 10 s warmup exclusion applies to the ingest series only; the other series have no warmup rule.
- Every p95 carries its rank-based 95% confidence interval (D-PERF-10, Le Boudec ranks) from the pooled raw samples; the CI exists from n = 120, smaller series say so. MARGINAL: the limit lies inside the CI. A pass is the point p95 under the limit.
- Not in this run, Gate 4: the takeover-window samples (D-PERF-07).
- Screen latency is `age_ms` (API, VM clock) plus `paint_ms` (browser clock, double rAF heuristic); localhost socket transit is left out (D-PERF-02).
- Reproduce: `make perf`. Raw samples: ingest.csv, alarm.csv, ui.csv, query.csv.
