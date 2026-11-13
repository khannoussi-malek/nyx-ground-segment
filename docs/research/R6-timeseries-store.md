# R6 – Hot time-series store

**Drives:** GS-DAT-02, GS-DAT-03, GS-DAT-05, GS-UI-04, GS-CMD-04, GS-ENG-06, GS-ENG-09, GS-ING-10   **Answers review Q:** 5, 11, 12

## Question

Which store holds 24 h of hot telemetry (2 vehicles × 10 Hz nominal, 2 × 100 Hz for 5 min peak) and meets these targets: 1 h of one parameter in < 300 ms (DAT-03), a 24 h chart with a min/max band in < 2 s (UI-04), and min/max/avg per parameter for the session report (DAT-02)? Hot rows may be deleted only after the archive is verified (DAT-05). Can the same database also hold alarms, commands, sessions and an append-only audit log (CMD-04)? Which table layout lets someone add a parameter without changing core code (ENG-06)? Which Python driver should we use?

Out of scope (handoffs): Parquet/MinIO mechanics and the verify step → **R7**. Dedup key semantics (`seq` reset, boot epoch) → **R5**.

## Findings

### Spike S1 (measured 2026-11-13, raw numbers)

Machine: Apple M4 Pro, 12 cores, 24 GB RAM. Docker VM had 8 GB and 12 CPUs. Image `timescale/timescaledb:latest-pg17` (TimescaleDB 2.32.0, PostgreSQL 17.11, `shared_buffers` auto-set to 1959 MB). **Caveat:** the host load average was 14–23 during the comparison runs because other jobs were running. Treat those numbers as pessimistic. The first run, with only the wide table loaded and a lower load, was 5–8× faster.
Data: 24 h × 2 vehicles × 10 Hz = 1,728,000 rows, made with `generate_series(0, 863999)` × 2 vehicles at 100 ms steps (script in the DDL below).

| Measurement | Wide (column per param) | JSONB `params` | Narrow (vehicle, param, t, value) |
| --- | --- | --- | --- |
| Size for 24 h (heap + indexes) | **436–455 MB** (200 MB of it indexes; avg row 144 B) | 679 MB | 2,073 MB |
| 1 h of one param, 1 vehicle (36,000 rows), DAT-03 | **1.8–3.7 ms** (low load); 13.5 / 17.2 / 24.1 ms min/median/max (loaded, n=5) | 42.9 / 52.3 / 88.3 ms | not run |
| 24 h band, 60 s buckets (1,380–1,440 rows), UI-04 | **58–64 ms** (low load); 224 / 243 / 424 ms (loaded) | **2,291 / 2,683 / 6,097 ms: fails the 2 s target** | 508 / 823 / 875 ms |
| 6 h session report, 4 params × min/max/avg, DAT-02 | 15 ms (low load); 23 / 31 / 32 ms (loaded) | 294 ms (all params via `jsonb_each_text`) | not run |
| Controlled DELETE of 1 vehicle-hour (36,000 rows) | 476 ms first run, 10 ms second run | | |
| `drop_chunks(older_than => min(t_onboard))` after deleting hour 0 | dropped exactly 1 empty chunk (24 → 23) | | |
| `ALTER TABLE … ADD COLUMN` (nullable, no default) on the 1.7 M-row hypertable | 2 ms | | |
| Server-side `INSERT … SELECT` of 1.728 M rows | 9.3 s (≈185 k rows/s). This is the database, not a driver; it shows about 1000× headroom over 200 rows/s | | |

Only the wide table was in memory for the low-load runs. In the loaded runs, all three tables together (3.2 GB) exceeded `shared_buffers`, which penalizes JSONB and narrow somewhat. The ranking was the same in every run.

### 1. TimescaleDB

- A hypertable is partitioned into chunks by time. The default chunk interval is 7 days. Docs rule: "the indexes of chunks currently being ingested into fit within 25% of main memory". Very small chunks cost planning time, because "the planner must evaluate each chunk", and they compress worse — [sizing chunks](https://www.tigerdata.com/docs/learn/hypertables/sizing-hypertable-chunks)
- This load is ≈ 19 MB/h (455 MB / 24 h, S1). A 1 h chunk keeps the active index at ≈ 8 MB, far below 25 % of 1959 MB. About 24–48 live chunks is a small planning cost. A 1 h chunk also matches the hourly archive object (§3 of the spec). S1 shows 24 chunks for 24 h.
- A unique index or PK "must contain all the partitioning columns", so "you cannot create a unique index without `time`" — [unique indexes](https://www.tigerdata.com/docs/use-timescale/latest/hypertables/hypertables-and-unique-indexes). As a result, the dedup key (R5) must include `t_onboard`. Plain PG partitioning has the same rule — [PG partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html)
- The current docs recommend `CREATE TABLE … WITH (tsdb.hypertable …)` and keep `create_hypertable()` "for backward compatibility". By default a descending index on time is created — [create_hypertable](https://www.tigerdata.com/docs/api/latest/hypertable/create_hypertable). S1 used `create_hypertable(by_range(...))` on 2.32 and it works.
- `time_bucket(bucket_width, ts, [offset, origin, timezone])` returns the bucket start. Use it with `GROUP BY` and `min/max/avg` — [time_bucket](https://www.tigerdata.com/docs/api/latest/hyperfunctions/time_bucket). Plain PG has an equivalent, `date_bin(stride, source, origin)` — [PG datetime functions](https://www.postgresql.org/docs/current/functions-datetime.html)
- **Continuous aggregates are overkill here.** They are TSL-only — [editions](https://www.tigerdata.com/docs/about/latest/timescaledb-editions). Real-time aggregation is "DISABLED by default" from v2.13, so a cagg without it lags behind the refresh policy — [real-time aggregates](https://www.tigerdata.com/docs/use-timescale/latest/continuous-aggregates/real-time-aggregates). In S1, aggregating the raw 24 h took 58–424 ms against a 2 s budget. Revisit them at 10 vehicles × 100 Hz (see Risks).
- **Compression (columnstore/Hypercore) is TSL-only** — [editions](https://www.tigerdata.com/docs/about/latest/timescaledb-editions). We don't need it, because 24 h of hot data is about 0.45 GB.
- `drop_chunks` removes chunks "if and only if their time range falls fully before" the cut-off. It works on whole chunks only — [drop_chunks](https://www.tigerdata.com/docs/api/latest/hypertable/drop_chunks). Retention by time alone is blind to whether the archive was verified. `add_retention_policy` is TSL — [editions](https://www.tigerdata.com/docs/about/latest/timescaledb-editions). Neither should be used for DAT-05.
- **Licensing.** Hypertables, `time_bucket` and `drop_chunks` are in both the Apache-2 and Community (TSL) editions. Retention policies, columnstore and continuous aggregates are Community only — [editions](https://www.tigerdata.com/docs/about/latest/timescaledb-editions). The default `timescale/timescaledb:latest-pg17` image reports `SHOW timescaledb.license` = `timescale`, i.e. TSL (S1). Docker Hub also lists `-oss` tags such as `latest-pg16-oss` — [tags](https://hub.docker.com/r/timescale/timescaledb/tags). It is UNVERIFIED that `-oss` means Apache-only.

### 2. Table layout

- **Wide (one row per frame, one typed column per param)** is the smallest and fastest layout in S1. It keeps one dedup key per frame, maps one-to-one to a Parquet row for R7 and replay, and makes the report a single `SELECT`. Its cost is DDL for each new parameter. With a nullable column and no default this is metadata-only: 2 ms in S1. For constant defaults the docs say no rows are rewritten — [PG ALTER](https://www.postgresql.org/docs/current/ddl-alter.html)
- **JSONB** is "decomposed binary … significantly faster to process" than `json` — [PG JSON types](https://www.postgresql.org/docs/current/datatype-json.html). Even so, extracting one key from 864 k rows took 2.3–6.1 s in S1, which **fails UI-04**. It also repeats every key in every row (+56 % size).
- **Narrow** needs no DDL to add a parameter, but it is 4.75× the size and has 10× the rows. Frame-level operations need a pivot (dedup, archive, replay, `mode`/`last_cmd_id`), and the 24 h band is 2–4× slower than wide (S1).
- **Way out of the DDL cost (ENG-06):** the parameter list already exists for frame validation and `limits.yaml`. The schema can be derived from that registry. At startup, run `ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS <ident> double precision` for each registry entry. Validate the name against a regex and quote it with `psycopg.sql.Identifier`. Adding a parameter then means adding one YAML entry and restarting. `ALTER TABLE` requires ownership of the table — [PG ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html). That forces the schema step to run as the owner role, not as the app role (see §4).

### 3. Alternatives

- **Plain PG 17 + B-tree on (vehicle, t_onboard)** would meet every target at this size, because S1's queries are index range scans and `date_bin` replaces `time_bucket`. What Timescale adds is automatic hourly chunks (no partition-creation job), `drop_chunks`, and the "time-series DB" bonus point from the job post. Native partitioning gives fast `DROP`/`DETACH` and avoids "VACUUM overhead" of bulk DELETE, but we would have to create partitions ourselves — [PG partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html)
- **BRIN** is "designed for … very large tables in which certain columns have some natural correlation with their physical location" — [PG BRIN](https://www.postgresql.org/docs/current/brin.html). 1.7 M rows/day is not "very large", and our deletes free space at the old end. Our own reasoning (UNVERIFIED) is that new rows reuse that space, which erodes the physical correlation BRIN depends on. A B-tree is the safer default.
- **InfluxDB 3 Core.** Retention can only be set when the database is created. Rows outside it are "filtered out of query results" rather than deleted under our control — [Influx retention](https://docs.influxdata.com/influxdb3/core/reference/internals/data-retention/), [manage DBs](https://docs.influxdata.com/influxdb3/core/admin/databases/). There is a reported default cap of 432 Parquet files (≈72 h) per query — [community thread](https://community.influxdata.com/t/influxdb-3-core-seems-to-ignore-the-72-hour-query-time-range-limit/57443) (UNVERIFIED in official docs). Postgres would still be needed for alarms, commands and audit.
- **QuestDB.** Old data is removed with `DROP PARTITION`, or by TTL once the "whole time range" of a partition expires — [QuestDB retention](https://questdb.com/docs/operations/data-retention/), [TTL](https://questdb.com/docs/concepts/ttl). Row-level deletes are weak, which makes per-vehicle verify-then-delete awkward. It is also a second database.
- **ClickHouse.** DELETE "marks rows as deleted without immediately removing them". Bulk removal should use `DROP PARTITION` — [CH lightweight delete](https://clickhouse.com/docs/guides/developer/lightweight-delete). It is very strong at large-scale aggregation, but it would be a second database to run in 25 h and has no transactional `ON CONFLICT` dedup (UNVERIFIED that its replacing engines would satisfy ING-03).
- **The case for one DB:** one backup, one connection pool, and one transaction that can write a frame batch together with its alarm transitions. That last point helps "no alarm duplicated" (R4/R5 handoff). The session report (DAT-02) becomes one SQL query joining telemetry, alarms, commands and gaps over the session window. Compose already includes Postgres.

### 4. Append-only audit log (CMD-04)

- The table owner always holds all privileges and "can always re-grant" them, and no privileges go to `PUBLIC` on tables by default. `TRUNCATE` is a separate privilege from `DELETE` — [PG privileges](https://www.postgresql.org/docs/current/ddl-priv.html). Therefore the app must **not** connect as the owner.
- A BEFORE row trigger can "abort the entire operation by raising an error" — [PL/pgSQL triggers](https://www.postgresql.org/docs/current/plpgsql-trigger.html). TRUNCATE triggers exist "only FOR EACH STATEMENT" — [CREATE TRIGGER](https://www.postgresql.org/docs/current/sql-createtrigger.html).
- S1 verified both layers. As `gs_app`, UPDATE, DELETE and TRUNCATE each failed with `permission denied`, and the INSERT into the identity PK succeeded with no sequence grant. As the owner, UPDATE and TRUNCATE were blocked by the trigger.
- Limitation: the owner can `ALTER TABLE … DISABLE TRIGGER` (ALTER TABLE requires ownership — [PG ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)), and superusers can do anything. This design stops app bugs and operators. It does not stop a DBA. If tamper-evidence is needed later, a hash chain is the next step.

### 5. Python driver

- psycopg 3 is "checked using … Mypy in `--strict` mode". Rows are typed through `class_row`/`dict_row` — [psycopg typing](https://www.psycopg.org/psycopg3/docs/advanced/typing.html). The 3.3.9 sdist ships `py.typed` (checked).
- asyncpg 0.32.0 ships no `py.typed` (sdist checked; it has `.pyi` files for the Cython modules only). mypy strict therefore needs the third-party [asyncpg-stubs](https://pypi.org/project/asyncpg-stubs/).
- asyncpg claims to be "on average, 5x faster than psycopg3". This is a June 2023 benchmark with unstated hardware and query mix — [asyncpg README](https://github.com/MagicStack/asyncpg). It does not matter at 200 frames/s.
- From 3.1, `executemany()` runs in pipeline mode, with "multiple queries … in a single network roundtrip" (needs libpq ≥ 14) — [psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html). COPY with `write_row()` is faster and also works async — [psycopg COPY](https://www.psycopg.org/psycopg3/docs/basic/copy.html). However, COPY has no `ON CONFLICT`: `ON_ERROR` covers only type-conversion errors — [PG COPY](https://www.postgresql.org/docs/current/sql-copy.html). Idempotent insert (ING-03) would then need a staging table, and at this rate that is not needed.

### 6. Sizing (arithmetic + S1)

- Rows/day, nominal: 2 × 10 Hz × 86,400 s = **1,728,000**. Peak: 2 × 100 Hz × 300 s = 60,000 extra rows (≈ 16 MB).
- Disk: S1 measured 436–455 MB for 1.728 M rows, ≈ 263 B/row including two B-tree indexes. Hot data is therefore ≈ **0.45 GB/day**. If archiving is blocked and retention has to grow to 72 h: ≈ 1.4 GB.
- 1 h of one parameter is 36,000 rows ≈ 5 MB of heap from cache, measured at 2–24 ms. Serializing 36 k points to JSON in the API is the larger cost (UNVERIFIED; measure in ATP-08).
- A 24 h band reads 864,000 rows ≈ 124 MB and returns 1,440 buckets, measured at 58–424 ms. That leaves 5–30× margin against 2 s.
- Q11 (10 vehicles × 100 Hz): 86.4 M rows/day ≈ 22 GB/day. A 24 h band for one vehicle would cover 8.64 M rows, which by linear extrapolation from S1 is ≈ 0.6–4 s (UNVERIFIED). That is the point at which a 1-minute rollup becomes necessary.

## Decision candidate

**Chosen:** a single **TimescaleDB** instance (`timescale/timescaledb:2.32.0-pg17`, pinned) holding:

- a **wide** `telemetry` hypertable whose parameter columns come from the registry, with 1 h chunks and no continuous aggregates and no compression;
- raw `time_bucket` min/max/avg downsampling;
- **controlled per-(vehicle, hour) DELETE with a row-count check**, run after R7 verification, followed by `drop_chunks` of chunks that are already empty;
- alarms, commands, sessions and the audit log as plain tables in the same DB, with the audit log protected by role + trigger;
- **psycopg 3** async, writing one transaction per XREADGROUP batch with `executemany` + `ON CONFLICT DO NOTHING`, then XACK.

Code uses only Apache-2 features, so it runs on the `-oss` image or on plain PG (with `date_bin` instead of `time_bucket`).

**Because:** it meets DAT-03, UI-04 and DAT-02 with 5–100× margin on measured numbers. It needs no extra container, keeps the deletion decision under job control (DAT-05), and lets reports join everything in one SQL query.

**Rejected:**
- JSONB layout: the 24 h band took 2.3–6.1 s and fails UI-04.
- Narrow layout: 4.75× the disk and frame operations need pivots.
- Continuous aggregates and compression: TSL-only and unnecessary at 0.45 GB/day.
- `add_retention_policy`: blind TTL, which violates DAT-05.
- Plain PG + BRIN: works, but we would hand-roll partition management and lose the TSDB bonus point.
- InfluxDB 3, QuestDB, ClickHouse: second store, no row-level verified delete, still need Postgres.
- asyncpg: untyped without third-party stubs, and its speed advantage is irrelevant at 200 rows/s.

```sql
-- Telemetry hypertable (parameter columns are added from the registry at startup)
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE telemetry (
  t_onboard   timestamptz NOT NULL,                 -- partition column; archive hour = date_trunc('hour', t_onboard)
  vehicle     text        NOT NULL,
  seq         bigint      NOT NULL,                 -- uint32 does not fit int4
  t_received  timestamptz NOT NULL DEFAULT clock_timestamp(), -- ING-09; not now() (= txn start, understates latency) — see R10 A-R10-1
  mode        text        NOT NULL CHECK (mode IN ('SAFE','STANDBY','TEST','FAULT')),
  last_cmd_id uuid,
  PRIMARY KEY (vehicle, t_onboard, seq)              -- R5 key (A-X-1); hypertable rule: must include t_onboard; epoch lives on event rows only
);
SELECT create_hypertable('telemetry', by_range('t_onboard', INTERVAL '1 hour'),
                         create_default_indexes => false);
CREATE INDEX telemetry_vehicle_time ON telemetry (vehicle, t_onboard DESC);
-- per registry entry, run as owner role, name validated ^[a-z][a-z0-9_]{0,62}$:
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS cabin_pressure_kpa double precision;

-- UI-04 / DAT-03 downsample; {p} = sql.Identifier(param) after registry check;
-- bucket = greatest((to - from) / 1500, '100 ms'). Empty buckets are absent, so the UI draws a gap (OPS-3).
SELECT time_bucket(%(bucket)s, t_onboard) AS t,
       min({p}) AS lo, max({p}) AS hi, avg({p}) AS mean, count({p}) AS n
FROM telemetry
WHERE vehicle = %(vehicle)s AND t_onboard >= %(from)s AND t_onboard < %(to)s
GROUP BY t ORDER BY t;

-- DAT-05 retention, per (vehicle, hour), only after R7 marks the object verified
BEGIN;
WITH d AS (DELETE FROM telemetry
           WHERE vehicle = %(v)s AND t_onboard >= %(h)s AND t_onboard < %(h)s + interval '1 hour'
           RETURNING 1)
SELECT count(*) FROM d;          -- app: != archive_object.hot_rows (R7; not row_count) → ROLLBACK + alert (also catches late rows)
UPDATE archive_object SET hot_deleted_at = now() WHERE vehicle = %(v)s AND hour = %(h)s;
COMMIT;
-- chunks entirely before the oldest remaining row are empty by construction (skip if NULL)
SELECT drop_chunks('telemetry', older_than => (SELECT min(t_onboard) FROM telemetry));

-- CMD-04 audit log: owned by gs_owner (migrations); the app connects as gs_app
CREATE TABLE audit_log (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at      timestamptz NOT NULL DEFAULT now(),
  actor   text NOT NULL, action text NOT NULL, reason text,
  details jsonb NOT NULL DEFAULT '{}'
);
REVOKE ALL ON audit_log FROM PUBLIC;
GRANT SELECT, INSERT ON audit_log TO gs_app;
CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_log is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
```

Session report (DAT-02): one `SELECT min(p1), max(p1), avg(p1), …, count(*)` over the session window, with the column list built from the registry. It took 15–31 ms for 6 h in S1. The count against the expected `duration × 10 Hz` and the GAP events answer review Q5.

## Risks / what would make us switch

- **10 vehicles × 100 Hz, or charts over more than 24 h** (Q11). Add a 1-minute rollup: either a continuous aggregate (needs TSL, which is free for self-hosting but not DBaaS — [editions](https://www.tigerdata.com/docs/about/latest/timescaledb-editions)) or a rollup table written by the worker. If analytics over weeks are needed, move to ClickHouse.
- **Late frames into an hour that is already archived** (for example a backlog after a Redis restart). The count check rolls back the delete if it runs after them. Rows that arrive *after* the delete stay hot but are never archived. R7 must sweep "rows in archived hours".
- **The dedup key must contain `t_onboard`.** A duplicate with a different `t_onboard` would not be caught. R5 must confirm that duplicates are byte-identical.
- **A bad vehicle clock** (for example `t_onboard` in 1970) creates stray chunks and an out-of-range archive hour. ING-02 validation should bound `t_onboard` to now ± N min.
- **Image/license drift.** `latest` tags move. Pin the version. If the license must be Apache-only, verify that the `-oss` tag reports `timescaledb.license = apache`.
- **Owner role and superuser can bypass the audit protection.** This is acceptable for the test campaign and is listed under "not production-ready" (Q14).
- **S1 was measured on a loaded laptop with synthetic uniform data.** ATP-08 must re-measure end to end through the API, including JSON serialization.

## Open questions → assumptions.md

1. Partition and archive on `t_onboard` (vehicle time) or `t_received`? Assumed `t_onboard`, matching spec §3. R5 and R7 should confirm.
2. Does the DAT-03 "< 300 ms" mean the DB query or the API round trip? Assumed the API round trip, measured in ATP-08.
3. Should a frame field that is not in the parameter registry be ignored and counted, or sent to the DLQ? Assumed ignored, with a counter and a warning, so that a simulator change cannot stop ingest.
4. Can a session be longer than the 24 h hot window? Assumed ≤ 24 h. The report is frozen into a `session_report` row at stop, so it survives retention.
5. Hot retention is "≥ 24 h, and until archived". If MinIO is down for days, the disk grows by ≈ 0.45 GB/day. Assume a health alert above 72 h of hot data.
6. Use the TSL default image or the `-oss` image? Assumed the default pinned image, using Apache-2 features only.
