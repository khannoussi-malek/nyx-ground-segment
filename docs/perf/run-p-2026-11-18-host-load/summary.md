# Run P, run-p-2026-11-18

2 vehicles x 100 Hz, 300 s, `step` faults of cabin_temp_c alongside.

## Result
- **ING-10 lag**: max time lag 19.564 s against 1 s: **FAIL**
- **ingest ING-09 (informative)**: n = 58012, p95 = 11126.2 ms (95% CI [10862.9, 11540.9]) against 200 ms: **FAIL**
- **alarm ALM-05 (informative)**: n = 59, p95 = 8438.4 ms (n too small for a two-sided CI) against 300 ms: **FAIL**
- **query DAT-03 (informative)**: n = 60, p95 = 90.1 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Totals
```json
{
  "cpu_pct": {
    "ingest-1": {
      "mean": 91.2682,
      "max": 3356.25
    },
    "ingest-2": {
      "mean": 44.159099999999995,
      "max": 406.78
    }
  },
  "redis_used_memory": {
    "before": 77913168,
    "after": 105445448,
    "per_vehicle_hour": 165193680.0
  },
  "ingest_ms": {
    "p50": 12.471,
    "p95": 11126.191
  },
  "frames_stored": 60012
}
```

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 28.08, 12.95, 13.47 / 6.81, 10.59, 12.44
- Image digests:
  - nyx-accept-archiver-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-ingest-2-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-simulator-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-accept-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-accept-ingest-1-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-migrate-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-api-1: nyx-gs:local sha256:54bac2a817c81de4085a2228fb425c7dac4f883b9524ee28818127080c589dc8
  - nyx-accept-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
- This is a shared laptop: other worktrees' stacks and tools may run during the sample.
- The load driver (this script) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.
- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, totals.json.
