# Run N, run-n-2026-11-17

2 vehicles x 10 Hz, 600 s, `step` of cabin_temp_c to 31 for 1 s every 5 s (120 RED transitions recorded).

## Result
- **ingest ING-09 (both vehicles pooled)**: n = 11869, p95 = 19.3 ms (95% CI [19.2, 19.5]) against 200 ms: **PASS** (first 10 s excluded: 200 samples)
- **alarm ALM-05, first 20**: n = 20, p95 = 22.1 ms (n too small for a two-sided CI) against 300 ms: **PASS**
- **alarm ALM-05, all steps**: n = 120, p95 = 22.3 ms (95% CI [20.5, 25.0]) against 300 ms: **PASS**
- **screen UI-01 (age + paint)**: n = 12009, p95 = 50.0 ms (95% CI [49.7, 50.2]) against 1000 ms: **PASS**
- **query DAT-03 (1 h range)**: n = 60, p95 = 24.1 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 2.52, 3.52, 5.79 / 3.73, 3.14, 4.40
- Image digests:
  - nyx-ingest-2-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-archiver-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-simulator-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-ingest-1-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-api-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-migrate-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
- The load driver (this script and the browser) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- The 10 s warmup exclusion applies to the ingest series only; the other series have no warmup rule.
- Every p95 carries its rank-based 95% confidence interval (D-PERF-10, Le Boudec ranks) from the pooled raw samples; the CI exists from n = 120, smaller series say so. MARGINAL: the limit lies inside the CI. A pass is the point p95 under the limit.
- Not in this run, Gate 4: the takeover-window samples (D-PERF-07).
- Screen latency is `age_ms` (API, VM clock) plus `paint_ms` (browser clock, double rAF heuristic); localhost socket transit is left out (D-PERF-02).
- Reproduce: `make perf`. Raw samples: ingest.csv, alarm.csv, ui.csv, query.csv.
