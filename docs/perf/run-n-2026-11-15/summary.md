# Run N, run-n-2026-11-15

2 vehicles x 10 Hz, 600 s, `step` of cabin_temp_c to 31 for 1 s every 5 s (120 RED transitions recorded).

## Result
- **ingest ING-09 (both vehicles pooled)**: n = 11874, p95 = 14.6 ms against 200 ms: **PASS** (first 10 s excluded: 200 samples)
- **alarm ALM-05, first 20**: n = 20, p95 = 14.1 ms against 300 ms: **PASS**
- **alarm ALM-05, all steps**: n = 120, p95 = 14.1 ms against 300 ms: **PASS**
- **screen UI-01 (age + paint)**: n = 12010, p95 = 46.2 ms against 1000 ms: **PASS**
- **query DAT-03 (1 h range)**: n = 60, p95 = 16.6 ms against 300 ms: **PASS**

## Context
- Other stacks were running on the machine during the run: the nyx stack (6 containers), the nyx3-z1 stack and PaperLab (5 containers) besides this nyx3-z2 stack, and the load average was 5 to 9 (a shared laptop, not a quiet reference machine).
- Machine: macOS-27.2.1-arm64-arm-64bit-Mach-O, arm64, 12 CPUs, Python 3.14.3
- Load average before / after: 4.92, 6.95, 8.09 / 5.44, 5.03, 6.34
- Image digests:
  - nyx3-z2-ingest-1-1: nyx-gs:g3b-z2 sha256:7e4e429f46d95ea1cecbbf54a98d6596824ded975db31a9f9d4d35100a9803b8
  - nyx3-z2-redis-1: redis:8.10.3 sha256:1f88291b4c57bdf249f02eb085a8669889b2d2954e91d4de5118a5da6b78dd53
  - nyx3-z2-db-1: timescale/timescaledb:2.32.0-pg17 sha256:6a6b7d7a346d7c6be03a310c240706f3bbc6d7adbdf3fbc07823570b34337f5e
  - nyx3-z2-minio-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx3-z2-simulator-1: nyx-gs:g3b-z2 sha256:7e4e429f46d95ea1cecbbf54a98d6596824ded975db31a9f9d4d35100a9803b8
  - nyx3-z2-ingest-2-1: nyx-gs:g3b-z2 sha256:7e4e429f46d95ea1cecbbf54a98d6596824ded975db31a9f9d4d35100a9803b8
  - nyx3-z2-minio-init-1: bitnamilegacy/minio: sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  - nyx3-z2-migrate-1: nyx-gs:g3b-z2 sha256:7e4e429f46d95ea1cecbbf54a98d6596824ded975db31a9f9d4d35100a9803b8
  - nyx3-z2-api-1: nyx-gs:g3b-z2 sha256:7e4e429f46d95ea1cecbbf54a98d6596824ded975db31a9f9d4d35100a9803b8
- The load driver (this script and the browser) runs on the machine under test; one laptop cannot keep them apart (D-PERF-08).
- The 10 s warmup exclusion applies to the ingest series only; the other series have no warmup rule.
- Not in this run, Gate 4: the p95 confidence interval from pooled samples (D-PERF-10) and the takeover-window samples (D-PERF-07). Percentiles here are point values.
- Screen latency is `age_ms` (API, VM clock) plus `paint_ms` (browser clock, double rAF heuristic); localhost socket transit is left out (D-PERF-02).
- Reproduce: `make perf`. Raw samples: ingest.csv, alarm.csv, ui.csv, query.csv.
