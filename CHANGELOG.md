# Changelog

## [Unreleased]
Gate 5 turns this section into `1.0.0`.

### What's in
- **Gate 2, thin slice**: simulator, ingest with ownership and exactly-once storage, limit alarms, REST and SSE API, first console.
- **Gate 3a**: mission database (`mission/`), alarm engine with hysteresis and persistence, API on the mission files.
- **Gate 3b**: console and commanding (ACK, hazardous-command confirmation, command tracking), test sessions with frozen reports, data views, UI-07 reconnect speed, a first performance run (`docs/perf/run-n-*`).
- **Gate 4, test readiness**: archiver (hourly verified Parquet, content and range checks, rolling scrub, late-frame handling), replay of archived hours with the recorded alarms and a live alarm counter, session report re-freeze, alarm-quality metrics, test hooks, the NYX-TA9 backfill vehicle, the README and ATP procedures, requirement-trace and commit-message checks. Run P (100 Hz), the scaling analysis and the 2 h soak count are landing in this gate and are not in the tree yet.

### Known limitations
- One MinIO copy of every archived hour: corruption is detected, not repaired.
- Not production-ready: no roles, no TLS, secrets in `.env`, single-node Redis and Postgres, audit not tamper-evident.
- Stream interfaces are not versioned; restart all services together after a change.
- No in-place schema migrations; a schema change means `make reset`.
- Performance numbers come from one shared laptop, not a reference machine.
Full list: [README](README.md#limitations).
