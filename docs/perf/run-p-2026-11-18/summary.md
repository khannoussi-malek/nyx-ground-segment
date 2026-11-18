# Run P, run-p-2026-11-18

2 vehicles x 100 Hz, 300 s, `step` faults of cabin_temp_c alongside.

## Result
- **ING-10 lag**: max time lag 0.648 s against 1 s: **PASS**
- **ingest ING-09 (informative)**: n = 58006, p95 = 49.9 ms (95% CI [49.4, 50.4]) against 200 ms: **PASS**
- **alarm ALM-05 (informative)**: n = 60, p95 = 54.9 ms (n too small for a two-sided CI) against 300 ms: **PASS**
- **query DAT-03 (informative)**: n = 60, p95 = 31.9 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Totals
```json
{
  "cpu_pct": {
    "ingest-1": {
      "mean": 11.556366666666666,
      "max": 18.66
    },
    "ingest-2": {
      "mean": 11.1121,
      "max": 20.09
    }
  },
  "redis_used_memory": {
    "before": 152322792,
    "after": 152292312,
    "per_vehicle_hour": -182880.0
  },
  "ingest_ms": {
    "p50": 17.957,
    "p95": 49.864
  },
  "frames_stored": 60005
}
```

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 3.81, 3.17, 3.02 / 4.29, 3.82, 3.38
- Image digests:
  - nyx-accept-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-migrate-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-ingest-1-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-ingest-2-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-simulator-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-accept-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-api-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-archiver-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
- This is a shared laptop: other worktrees' stacks and tools may run during the sample.
- The load driver (this script) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.
- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, totals.json.
