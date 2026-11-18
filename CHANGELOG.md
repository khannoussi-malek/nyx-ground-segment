# Changelog

## [1.0.0] - 2026-11-18
Accepted at Gate 5 from a fresh clone: [ATP report](docs/atp-report.md), [NCR-01](docs/ncr/NCR-01.md), [demo](docs/demo/README.md).

### What's in
- **Gate 2, thin slice**: simulator, ingest with ownership and exactly-once storage, limit alarms, REST and SSE API, first console.
- **Gate 3a**: mission database (`mission/`), alarm engine with hysteresis and persistence, API on the mission files.
- **Gate 3b**: console and commanding (ACK, hazardous-command confirmation, command tracking), test sessions with frozen reports, data views, UI-07 reconnect speed, a first performance run (`docs/perf/run-n-*`).
- **Gate 4, test readiness**: archiver (hourly verified Parquet, content and range checks, rolling scrub, late-frame handling), replay of archived hours with the recorded alarms and a live alarm counter, session report re-freeze, alarm-quality metrics, test hooks, the NYX-TA9 backfill vehicle, the README and ATP procedures, requirement-trace and commit-message checks.
- **Gate 5 preparation**: UI-07 reconnect path shortened (lazy archive imports in the API), daily state snapshot with a reset guard, FRAMES REJECTED on the console, Redis write-cost test judged on its best of three batches.
- **Gate 5, acceptance**: every ATP run from a fresh clone; fixes it found: README clone URL, MinIO client pool size, `make perf`/`make e2e` install the e2e packages, ATP psql commands run without a TTY, ATP-08 starts on a quiet machine.

### Known limitations
- One MinIO copy of every archived hour: corruption is detected, not repaired.
- Not production-ready: no roles, no TLS, secrets in `.env`, single-node Redis and Postgres, audit not tamper-evident.
- Stream interfaces are not versioned; restart all services together after a change.
- No in-place schema migrations; a schema change means `make reset`.
- Performance numbers come from one shared laptop, not a reference machine.
- ING-10 (lag < 1 s at 2 × 100 Hz) holds only on a host that is not CPU-oversubscribed: at load average 28 on 12 cores one stall reached 19.6 s, no frame lost ([NCR-01](docs/ncr/NCR-01.md)).
- At 10 vehicles × 100 Hz a restarted ingest worker does not catch up its backlog ([scaling](docs/perf/scaling.md)).
- A command's timeouts start only once ground processing has caught up (R3-04); the console does not say a verdict is delayed.
Full list: [README](README.md#limitations).
