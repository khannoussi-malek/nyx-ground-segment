# A/B run P on EXT-01 c7dc345 (run-p-2026-11-21-ab-ext01)

2 vehicles x 100 Hz, 300 s, `step` faults of cabin_temp_c alongside.

## Result
- **ING-10 lag**: max time lag 1.171 s against 1 s: **FAIL**
- **ingest ING-09 (informative)**: n = 58010, p95 = 671.6 ms (95% CI [664.2, 678.4]) against 200 ms: **FAIL**
- **alarm ALM-05 (informative)**: n = 60, p95 = 353.0 ms (n too small for a two-sided CI) against 300 ms: **FAIL**
- **query DAT-03 (informative)**: n = 60, p95 = 49.8 ms (n too small for a two-sided CI) against 300 ms: **PASS**

## Totals
```json
{
  "cpu_pct": {
    "ingest-1": {
      "mean": 20.8069,
      "max": 31.61
    },
    "ingest-2": {
      "mean": 20.230966666666667,
      "max": 31.08
    }
  },
  "redis_used_memory": {
    "before": 134807256,
    "after": 154852592,
    "per_vehicle_hour": 120272016.0
  },
  "ingest_ms": {
    "p50": 8.882,
    "p95": 671.592
  },
  "frames_stored": 60010
}
```

## Context
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 4.81, 4.10, 3.97 / 5.71, 4.48, 4.10
- Image digests:
  - nyx-ext01-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx-ext01-ingest-1-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-api-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-ingest-2-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-ext01-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx-ext01-migrate-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-archiver-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-simulator-1: nyx-gs:local sha256:b626502f784135d9b86e9401187b371d388dc7cf20f4c20169beb19e5b5bf597
  - nyx-ext01-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
- This is a shared laptop: other worktrees' stacks and tools may run during the sample.
- The load driver (this script) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- Only ING-10 is a pass/fail here (A-R10-6); the other rows are informative, each p95 with its CI (D-PERF-10); the first 10 s are excluded from the ingest series.
- Reproduce: `make perf-p`. Raw samples: lag.csv, ingest.csv, alarm.csv, query.csv, totals.json.
