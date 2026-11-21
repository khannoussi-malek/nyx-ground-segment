# Run P, run-p-2026-11-21

2 vehicles x 100 Hz, 300 s, `step` faults of cabin_temp_c alongside.

## Result
- **ING-10 lag**: max time lag 1.865 s against 1 s: **FAIL**
- **ingest ING-09 (informative)**: n = 58006, p95 = 475.9 ms (95% CI [458.3, 494.2]) against 200 ms: **FAIL**
- **alarm ALM-05 (informative)**: n = 60, p95 = 642.8 ms (n too small for a two-sided CI) against 300 ms: **FAIL**
- **query DAT-03 (informative)**: n = 60, p95 = 12.0 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Totals
```json
{
  "cpu_pct": {
    "ingest-1": {
      "mean": 22.820866666666667,
      "max": 31.33
    },
    "ingest-2": {
      "mean": 22.3393,
      "max": 31.19
    }
  },
  "redis_used_memory": {
    "before": 3228424,
    "after": 31676016,
    "per_vehicle_hour": 170685552.0
  },
  "ingest_ms": {
    "p50": 7.073,
    "p95": 475.894
  },
  "frames_stored": 60006
}
```

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 3.04, 3.64, 3.87 / 4.89, 3.92, 3.90
- Image digests:
  - nyx-base-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-base-simulator-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-ingest-2-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-archiver-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-migrate-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-api-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx-base-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-base-ingest-1-1: nyx-gs:local sha256:4d04692912ad0f78bf12635104568235983d6a0c3414bf59241e9a3d1cd242a1
  - nyx-base-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
- This is a shared laptop: other worktrees' stacks and tools may run during the sample.
- The load driver (this script) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.
- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, totals.json.
