# Run P, run-p-2026-11-17

2 vehicles x 100 Hz, 300 s, `step` faults of cabin_temp_c alongside.

## Result
- **ING-10 lag**: max time lag 0.858 s against 1 s: **PASS**
- **ingest ING-09 (informative)**: n = 58004, p95 = 16.7 ms (95% CI [16.4, 17.1]) against 200 ms: **PASS**
- **alarm ALM-05 (informative)**: n = 60, p95 = 66.5 ms (n too small for a two-sided CI) against 300 ms: **PASS**
- **query DAT-03 (informative)**: n = 60, p95 = 34.2 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Totals
```json
{
  "cpu_pct": {
    "ingest-1": {
      "mean": 22.946266666666666,
      "max": 41.19
    },
    "ingest-2": {
      "mean": 22.631733333333333,
      "max": 35.98
    }
  },
  "redis_used_memory": {
    "before": 366606928,
    "after": 366594008,
    "per_vehicle_hour": -77520.0
  },
  "ingest_ms": {
    "p50": 7.488,
    "p95": 16.694
  },
  "frames_stored": 60004
}
```

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 3.73, 3.14, 4.40 / 7.57, 5.53, 5.09
- Image digests:
  - nyx-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-archiver-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-simulator-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-ingest-2-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx-ingest-1-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-api-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-migrate-1: nyx-gs:local sha256:3a278639f8b1b3ed2cf9cf579614e8d56f6d09afacc6c44005d015c54f4e5906
  - nyx-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
- This is a shared laptop: other worktrees' stacks and tools may run during the sample.
- The load driver (this script) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.
- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, totals.json.
