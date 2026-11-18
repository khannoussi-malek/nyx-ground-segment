# Run N, run-n-2026-11-18

**INCOMPLETE RUN: TimeoutError: timed out**

2 vehicles x 10 Hz, 600 s, `step` of cabin_temp_c to 31 for 1 s every 5 s (0 RED transitions recorded).

## Result
- **ingest ING-09 (both vehicles pooled)**: NO SAMPLES (FAIL)
- **alarm ALM-05, first 20**: NO SAMPLES (FAIL)
- **alarm ALM-05, all steps**: NO SAMPLES (FAIL)
- **screen UI-01 (age + paint)**: NO SAMPLES (FAIL)
- **query DAT-03 (1 h range)**: NO SAMPLES (FAIL)

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 3.55, 11.29, 14.45 / 12.51, 9.06, 12.14
- Image digests:
  - nyx-accept-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx-accept-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-ingest-1-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-simulator-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-accept-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-ingest-2-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-api-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-archiver-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-migrate-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
- The load driver (this script and the browser) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- The 10 s warmup exclusion applies to the ingest series only; the other series have no warmup rule.
- Every p95 carries its rank-based 95% confidence interval (D-PERF-10, Le Boudec ranks) from the pooled raw samples; the CI exists from n = 120, smaller series say so. MARGINAL: the limit lies inside the CI. A pass is the point p95 under the limit.
- Not in this run, Gate 4: the takeover-window samples (D-PERF-07).
- Screen latency is `age_ms` (API, VM clock) plus `paint_ms` (browser clock, double rAF heuristic); localhost socket transit is left out (D-PERF-02).
- Reproduce: `make perf`. Raw samples: ingest.csv, alarm.csv, ui.csv, query.csv.
