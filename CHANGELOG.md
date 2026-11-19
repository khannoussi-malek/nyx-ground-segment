# Changelog

## [Unreleased]
### Fixed
- A bad `ui:events` entry met during a Redis failure was skipped with no DLQ record; the API reader now re-reads it once Redis is back (D-ING-27).
- A database failure on the command and alarm routes is now the same 503 `UNAVAILABLE` within 3 s as on the other routes (it waited up to 5 s there); a bad query or path value that the database rejects is 422 `VALIDATION_ERROR`, not a 503 (A-G3b-P27).
- Creating a replay no longer holds a database connection while it hash-checks the archived hours against MinIO; a few parallel creates of 24 h windows could take most of the pool and make every other route answer 503.

## [1.0.1] - 2026-11-19
### Fixed
- A Redis broker outage could be followed by a false LOSS OF SIGNAL: ingest reconnected before the simulator resumed sending and counted that silence as loss. The LOS clock now holds for 5 s after ingest reconnects; a source that stays silent still gets its LOS about 7 s after the reconnect (up to 8 s with one blocked read), in that post-outage window only; values still turn STALE at 2 s ([NCR-04](docs/ncr/NCR-04.md), D-ING-08, D-UI-15).

## [1.0.0] - 2026-11-18
Accepted at Gate 5 from a fresh clone: [ATP report](docs/atp-report.md), [NCR-01](docs/ncr/NCR-01.md), [NCR-03](docs/ncr/NCR-03.md), [demo](docs/demo/README.md).

### What's in
- **Gate 2, thin slice**: simulator, ingest with ownership and exactly-once storage, limit alarms, REST and SSE API, first console.
- **Gate 3a**: mission database (`mission/`), alarm engine with hysteresis and persistence, API on the mission files.
- **Gate 3b**: console and commanding (ACK, hazardous-command confirmation, command tracking), test sessions with frozen reports, data views, UI-07 reconnect speed, a first performance run (`docs/perf/run-n-*`).
- **Gate 4, test readiness**: archiver (hourly verified Parquet, content and range checks, rolling scrub, late-frame handling), replay of archived hours with the recorded alarms and a live alarm counter, session report re-freeze, alarm-quality metrics, test hooks, the NYX-TA9 backfill vehicle, the README and ATP procedures, requirement-trace and commit-message checks.
- **Gate 5 preparation**: UI-07 reconnect path shortened (lazy archive imports in the API), daily state snapshot with a reset guard, FRAMES REJECTED on the console, Redis write-cost test judged on its best of three batches.
- **Gate 5, acceptance**: every ATP run from a fresh clone; fixes it found: README clone URL, MinIO client pool size, `make perf`/`make e2e` install the e2e packages, ATP psql commands run without a TTY, ATP-08 starts on a quiet machine ([NCR-03](docs/ncr/NCR-03.md)).

### Known limitations
- One MinIO copy of every archived hour: corruption is detected, not repaired.
- Not production-ready: no roles, no TLS, secrets in `.env`, single-node Redis and Postgres, audit not tamper-evident.
- Stream interfaces are not versioned; restart all services together after a change.
- No in-place schema migrations; a schema change means `make reset`.
- Performance numbers come from one shared laptop, not a reference machine.
- ING-10 (lag < 1 s at 2 × 100 Hz) holds only on a host that is not CPU-oversubscribed: at load average 28 on 12 cores stalls reached 19.6 s, no frame lost, and a later 2 h soak showed a 2.2 s LOSS OF SIGNAL on both vehicles from a host stall ([NCR-01](docs/ncr/NCR-01.md)).
- Deployment: give the stack dedicated cores. The scaling path (more ingest workers) does not help when the host itself is starved.
- At 10 vehicles × 100 Hz a restarted ingest worker does not catch up its backlog ([scaling](docs/perf/scaling.md)).
- A command's timeouts start only once ground processing has caught up (R3-04); the console does not say a verdict is delayed.
Full list: [README](README.md#limitations).
