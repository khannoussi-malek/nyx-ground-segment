# R7 – Archive and replay

**Drives:** GS-DAT-04, GS-DAT-05, GS-DAT-06, GS-UI-04 (window source), ATP-07, OPS-6   **Answers review Q:** 4, 12

Handoffs: hot table DDL, the per-(vehicle, hour) DELETE with count check, `drop_chunks` → [R6](R6-timeseries-store.md). Event keys, epoch, "replay must not write live tables" → [R5](R5-idempotency-gaps-resets.md). Pure `step()` core, `LEVEL` rebuild, `limits_versions` / `LIMITS_APPLIED` → [R8](R8-alarm-logic.md). Replay through the same processor as live (Yamcs) → [R1](R1-prior-art-mission-control.md). WebSocket transport and REPLAY banner rendering → R9.

## Question

1. Parquet layout for one vehicle-hour: schema, codec, row groups, metadata. Can a re-run produce the same bytes?
2. Upload integrity on MinIO: single PUT vs multipart, atomicity, `Content-MD5` / `x-amz-checksum-sha256`, ETag caveats, re-read verify, sidecar. Which Python client?
3. Crash safety for DAT-05 ("kill job mid-upload → nothing lost"): job state machine, idempotent re-run, late frames into archived hours.
4. Scheduling: asyncio loop or APScheduler?
5. Replay for DAT-06 / OPS-6: reader, same alarm core, same limits version, 1×/10× pacing, separate output, and a test that proves "same alarms as live".
6. GS-UI-04: is the 24 h chart always hot?

## Findings

### Spike S2 (measured 2026-11-13; scripts in the session scratchpad `r7/spike/`)

Machine: Apple M4 Pro, host load average 4–10 from other jobs. pyarrow 26.0.1, duckdb 2.0.0, minio-py 7.2.20, boto3 1.44.2. MinIO = `bitnamilegacy/minio:2025.5.24` (see the MinIO finding below). Data: 1 vehicle-hour = 36,000 rows at 10 Hz, wide schema (below), 8 float params with Gaussian noise. Synthetic noise compresses worse than real telemetry, so sizes are an upper bound (**STILL UNVERIFIED**: needs real telemetry).

| Measurement | Result |
| --- | --- |
| File size: zstd / snappy / none (1 row group) | **0.91 MB** / 1.13 MB / 1.43 MB. Write time 10 / 17 / 7 ms |
| Hot size of the same data (R6 S1: 455 MB per 24 h × 2 vehicles) | ≈ 9.5 MB per vehicle-hour including indexes → Parquet zstd is ≈ 10× smaller |
| Same table written twice, separate processes, same pyarrow | identical sha256 (`2a2a9727…`) |
| `created_by` in the footer | `parquet-cpp-arrow version 26.0.1`, so a pyarrow upgrade changes the bytes |
| Read a 20 min window (12,000 rows), local bytes, `filters=` on `t_onboard` | 0.9–2 ms warm, 30 ms cold |
| Same via `pyarrow.fs.S3FileSystem` → MinIO | 54 ms cold, 14 ms warm |
| Same via DuckDB `read_parquet('s3://…')` | 29 ms cold, 2 ms warm. **`INSTALL httpfs` downloaded the extension at runtime: 18.8 s, needs network** |
| PUT with correct `x-amz-checksum-sha256` (via minio-py `metadata=`) | accepted. Single-PUT ETag == MD5 of the body |
| PUT with wrong `x-amz-checksum-sha256` | **rejected: `XAmzContentChecksumMismatch`**, no object |
| `stat_object` with `x-amz-checksum-mode: ENABLED` | returns the stored SHA-256 (base64), equal to ours |
| Full re-read + sha256 of 0.91 MB | 3 ms, match |
| Single PUT killed with SIGKILL at ~1 MB of 4 MB (curl, presigned URL) | **no object visible** |
| Multipart (5 MiB parts) killed during part 2 | **no object visible**. 1 incomplete upload left, listed only when `prefix` = the exact key |

### 1. Parquet

- `write_table` defaults: `compression='snappy'`, valid values include `'ZSTD'`. `row_group_size=None` means "the minimum of the Table size (in rows) and 1024 * 1024", so a 36 k-row hour is one row group. `store_schema=True` stores the Arrow schema "in the 'ARROW:schema' key". There is no `created_by` parameter — [pyarrow write_table](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html)
- Parquet recommends "large row groups (512MB - 1GB)" — [Parquet configurations](https://parquet.apache.org/docs/file-format/configurations/). Our whole file is ~1 MB, so one row group is right. Splitting gains nothing because replay reads ≤ 2 objects.
- The footer holds "Optional key/value metadata" and `created_by` = "<Application> version <App Version> (build …)" — [parquet.thrift](https://github.com/apache/parquet-format/blob/master/src/main/thrift/parquet.thrift). Custom keys survive a round trip (S2: `nyx.*` keys read back next to `ARROW:schema`).
- **Determinism:** S2 shows byte-identical output for the same rows with the same pyarrow version. `created_by` embeds the version, so bytes change after an upgrade. **Consequence:** do not rely on reproducible bytes. Hash the bytes we upload, store the hash in Postgres, verify against that. A re-run that produces different bytes is fine as long as it has not reached VERIFIED (state machine below).
- **Schema (schema_version 1)**, derived at export time from the hot table's columns (so ENG-06 parameters appear automatically): `vehicle string`, `t_onboard timestamp[us, UTC]`, `seq int64`, `t_received timestamp[us, UTC]`, `mode dictionary<int8, string>`, `last_cmd_id string` (UUID text, nullable), then one `float64` (nullable) per registry parameter. Rows sorted by `(t_onboard, seq)`. Postgres `timestamptz` has µs resolution, so `us` loses nothing — [PG datetime](https://www.postgresql.org/docs/current/datatype-datetime.html).
- **Footer metadata:** `nyx.schema_version`, `nyx.vehicle`, `nyx.hour` (ISO UTC), `nyx.version` (archive version, see §3), `nyx.row_count`, `nyx.t_min/t_max`, `nyx.seq_min/seq_max`, `nyx.limits_timeline` (JSON list of `LIMITS_APPLIED` `{epoch, first_seq, sha256}` in force during the hour, R8). The file then documents which limits applied without our database. The sha256 of the file cannot be inside the file, so it lives in the sidecar and in `archive_object`.

### 2. Upload integrity (MinIO / S3 API)

- **Atomicity.** "Amazon S3 never adds partial objects; if you receive a success response, Amazon S3 added the entire object." For simultaneous writes "it overwrites all but the last object written" — [PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html). Multipart: "Upon receiving this request, Amazon S3 concatenates all the parts … to create a new object" — [CompleteMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html). S2 confirms on MinIO: killed single PUT and killed multipart leave no visible object.
- **Size → single PUT.** Multipart parts must be ≥ 5 MB except the last ([CompleteMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html), `EntityTooSmall`). minio-py has `MIN_PART_SIZE = 5 MiB` ([helpers.py](https://github.com/minio/minio-py/blob/master/minio/helpers.py)). One vehicle-hour is ~1 MB (≤ ~2 MB with the 100 Hz peak), so it is always a single PUT. No orphaned parts and no multipart ETag questions.
- **Checksums.** S3 "validates it with the provided value before storing the object". On mismatch "the request is rejected" — [integrity on upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html). SHA-256 is supported for single PUT (full object) but for multipart **only as a composite** checksum (a checksum of part checksums); full-object multipart checksums are CRC-only — same page. That is another reason to stay on single PUT: the stored SHA-256 then equals `sha256sum` of the file. S2: MinIO enforces `x-amz-checksum-sha256` and returns it with `x-amz-checksum-mode: ENABLED`. **VERIFIED**: since RELEASE.2022-09-01T23-53-36Z (PR #15433, "For now only added to PutObject").
- **ETag caveat.** For multipart "the object's ETag is not an MD5 digest, regardless of the method of encryption", and it ends in `-<parts>` — [integrity on upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html). Never use ETag as the content hash. We store our own SHA-256.
- **`Content-MD5`** makes S3 check the body and "if they do not match, Amazon S3 returns an error" — [PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html). minio-py adds it on HTTPS. On plain HTTP it signs the real body SHA-256 (`x-amz-content-sha256`), so the server checks the body anyway — [api.py `_build_headers`](https://github.com/minio/minio-py/blob/master/minio/api.py) (read in 7.2.20).
- **Verify = full re-read.** `GET` the object, sha256 the bytes, compare to the hash stored *before* upload, and parse the footer (`num_rows == row_count`). It costs 3 ms per object (S2). Comparing server-reported checksums alone would trust the server's bookkeeping. The re-read proves the object is readable end to end from the store, which is what DAT-05 asks.
- **Sidecar** `<HH>.parquet.sha256` with content `<hex>  <HH>.parquet\n` (GNU `sha256sum` format, so `sha256sum -c` works after download in ATP-07). Written only after the data object verifies, also with `x-amz-checksum-sha256`. The authoritative hash is in Postgres. The sidecar is for humans and other tools.
- **Python client: `minio` (minio-py).** It ships `py.typed` (checked in 7.2.20), so mypy strict works without stubs. Headers that start with `x-amz-` pass through `metadata=` unprefixed ([helpers.py `normalize_headers`](https://github.com/minio/minio-py/blob/master/minio/helpers.py)), which is how S2 sent the checksum. It is sync-only, so call it via `asyncio.to_thread`. boto3 1.44.2 ships no `py.typed` (checked). It needs `types-boto3[s3]` — [types-boto3](https://pypi.org/project/types-boto3/). Newer AWS SDKs also choose a checksum algorithm on their own ("the SDK chooses a checksum algorithm for you" — [integrity on upload](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)). That is one more behaviour to test against a non-AWS server.
- **MinIO distribution changed (important for compose).** The repo README says "THIS REPOSITORY IS NO LONGER MAINTAINED" and "The MinIO community edition is now distributed as source code only". The repo was archived on 2026-04-25 — [github.com/minio/minio](https://github.com/minio/minio). S2: `docker pull minio/minio` → "repository does not exist", and `quay.io/minio/minio` → 401. Press coverage: [linuxiac](https://linuxiac.com/minio-ends-active-development/). Pullable today (S2): `bitnamilegacy/minio:2025.5.24` (frozen, built 2025-06-06) and `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` (community rebuild; provenance **VERIFIED**: the Pigsty project (Vonng); renamed `pgsty/silo` on 2026-08-06). The minio-py repo is not archived — [minio-py](https://github.com/minio/minio-py). Its future maintenance is **STILL UNVERIFIED**: no release since 7.2.20 (2025-11-27), but commits continue in 2026 (e.g. 2026-09-29); not archived.

### 3. Crash safety and late frames

- Hot rows are the only copy until VERIFIED. Every step is either idempotent or guarded by a row in `archive_object` that is written **before** the side effect it describes. Postgres is the source of truth. MinIO holds immutable objects per version.
- **Eligibility (hour H of vehicle v):** `H + 1h + 2 min ≤ seq_state.last_t` (vehicle time has moved past H; per-vehicle order from R4 means in-order frames of H are in), **or** `H + 1h + 30 min ≤ now()` (vehicle silent or test ended). Using a data watermark instead of a pure wall-clock grace means a Redis backlog (A-R3-1) does not cause re-versions.
- **Late frames.** Invariant: *the latest VERIFIED version of (vehicle, hour) is the complete hour.* Readers only ever open that one object. Keys: v1 = `archive/<v>/<YYYY>/<MM>/<DD>/<HH>.parquet` (spec name), vN = `<HH>.v<N>.parquet`. Older versions are never overwritten, so a reader or verifier never sees data and sidecar disagree.
  - Late rows **before** hot delete: R6's delete compares the deleted count with `hot_rows` and rolls back on mismatch. The next run exports the full hour again from hot as vN+1.
  - Late rows **after** hot delete (more than 24 h late, rare): the sweep finds hot rows in an hour whose latest version is HOT_DELETED. It writes vN+1 = rows of vN (read from MinIO, sha-checked) ∪ new hot rows, sorted. `hot_rows` = only the new rows, which are then deleted.
  - Sweep query, every run: `SELECT vehicle, time_bucket('1 hour', t_onboard) h, count(*) FROM telemetry WHERE t_onboard < watermark GROUP BY 1,2`, compared with `archive_object`. It scans ≤ 24 h, which R6 S1 measured at 58–424 ms for a similar full scan.
- **R6 refinement:** the DELETE count check compares against `archive_object.hot_rows` (rows exported from hot for this version), not `row_count`. For v1 they are equal.
- **Single runner:** `pg_try_advisory_lock(hashtext('archiver'))` at the start of each run. A second instance is a no-op (same mechanism as R4).

```
               eligible (computed, no row)                      hour_end ≤ now − 25 h
PENDING ──INSERT row: sha256,row_count,hot_rows──► WRITTEN ──PUT(+x-amz-checksum-sha256)──► UPLOADED
   ▲                                           │ crash: re-export, overwrite row, re-PUT     │ crash: re-verify
   │                                           ▼                                             ▼
   │                         HOT_DELETED ◄──R6 DELETE txn, count == hot_rows── VERIFIED ◄──GET, sha256 == row, footer rows ok, PUT sidecar
   │                              │   count ≠ hot_rows → ROLLBACK, new version (late rows)      │ mismatch → status stays UPLOADED,
   └──── new hot rows in hour ────┘                                                           last_error, alert; hot never touched
```

```sql
CREATE TABLE archive_object (
  vehicle        text        NOT NULL,
  hour           timestamptz NOT NULL CHECK (extract(epoch FROM hour)::bigint % 3600 = 0),
  version        int         NOT NULL DEFAULT 1 CHECK (version >= 1),
  status         text        NOT NULL CHECK (status IN ('WRITTEN','UPLOADED','VERIFIED','HOT_DELETED')),
  object_key     text        NOT NULL UNIQUE,         -- archive/<v>/<YYYY>/<MM>/<DD>/<HH>[.v<N>].parquet
  sha256         char(64)    NOT NULL,                -- of the bytes we PUT; authoritative
  size_bytes     bigint      NOT NULL,
  row_count      bigint      NOT NULL,                -- rows in the object
  hot_rows       bigint      NOT NULL,                -- rows taken from hot for this version (R6 delete check)
  t_min timestamptz, t_max timestamptz, seq_min bigint, seq_max bigint,
  schema_version int         NOT NULL,
  writer         text        NOT NULL,                -- footer created_by, e.g. 'parquet-cpp-arrow version 26.0.1'
  etag           text,                                -- informational only
  attempts       int         NOT NULL DEFAULT 0,
  last_error     text,
  written_at timestamptz NOT NULL DEFAULT now(), uploaded_at timestamptz,
  verified_at timestamptz, hot_deleted_at timestamptz,
  PRIMARY KEY (vehicle, hour, version)
);
-- reader: latest complete version per hour
-- SELECT DISTINCT ON (vehicle, hour) * FROM archive_object
--  WHERE status IN ('VERIFIED','HOT_DELETED') ORDER BY vehicle, hour, version DESC;
```

The export runs in one `REPEATABLE READ` transaction, so `SELECT … ORDER BY t_onboard, seq` and `count(*)` see the same snapshot. Bytes are built in memory (~1 MB). There is no local temp file, so there is no file to clean up after a crash. WRITTEN therefore means "hash and count recorded, PUT may or may not have happened". Re-running from WRITTEN re-exports and re-PUTs. That is safe because nobody reads non-VERIFIED keys.

**ATP-07 kill test.** Env hook `NYX_ARCHIVE_SLOW_UPLOAD=1` streams the body slowly, and the test SIGKILLs the archiver. Assert: no object or a complete one, hot row count unchanged, `archive_object` in WRITTEN. Restart → VERIFIED → `sha256sum -c` on the downloaded pair passes → after the retention age, HOT_DELETED with zero rows left for that hour.

### 4. Scheduling

- APScheduler stable is 3.11.3 (2026-06-28). 4.0 is still "Pre-release" (4.0.0a6, 2025-04-27) — [PyPI](https://pypi.org/project/APScheduler/). One periodic job does not justify a scheduler dependency or the 3→4 migration risk.
- **Chosen:** an `archiver` service with `while True: run_once(); await asyncio.sleep(300)`, plus `run_once()` at start-up. That start-up run is the crash recovery path. "Hourly" (DAT-04) is met because an hour becomes eligible ≤ 32 min after it ends and is picked up within 5 min. A CLI `python -m nyx.archiver --once` lets the ATP and tests drive it.

### 5. Replay

- **Prior art:** Yamcs creates a separate processor "on demand for replays" running the same pipeline as realtime, but with the *current* MDB limits — R1 §4, R8 §7–8. We pin limits by hash.
- **Reader:** per hour in the window, use the latest VERIFIED object (`get_object` → sha256 check against `archive_object` → `pq.read_table(pa.BufferReader(b), filters=[t_onboard range])`). If the hour has no verified version yet (current hour), read it from the hot table. A 20 min window is 1–2 objects at 3 ms GET + ~1 ms parse (S2). DuckDB rejected: an extra dependency whose S3 support downloads `httpfs` at runtime (18.8 s and network in S2; offline CI would need pre-baking). `pyarrow.fs.S3FileSystem` works (S2) but duplicates the MinIO config and skips our hash check.
- **Seed** (R8 rebuild): read from `t_from − WARMUP` (10 s ≥ K frames at 10 Hz). The initial state comes from Postgres event tables: last `SEQ_RESET`/epoch ≤ start (R5), `LEVEL` rows per check (R8), open alarm instances, and the `LIMITS_APPLIED` timeline with content from `limits_versions` by sha256. **These tables must never be pruned** (closes A-R8-10: they are small, and replay of any archived hour depends on them).
- **Limits switch** is fed into the core at the same `(epoch, first_seq)` as live, so the persistence-ring reset of R8 §7 happens at the same frame.
- **ACKs (A-R8-11):** escalation after an ack depends on `acked_sev` (R8 life-cycle table), so ACKs must be replayed or transitions can differ. Proposal for R8: each ACK row stores `after_seq` (the last seq that the owner had processed when the ack was applied). Replay injects the ACK after that frame. Replay UI itself cannot ack.
- **Pacing** uses `t_onboard` deltas divided by speed against `time.monotonic()`. When behind, it emits without sleeping. Gaps stay visible (a 10 s dropout lasts 1 s at 10×). Tests use `speed=None` (no sleep).
- **Output:** a WebSocket topic `replay:<replay_id>` (R9). Every message has `mode: "REPLAY"` and the UI shows the banner from that field. Nothing is written to telemetry, alarm or event tables (A-R5-7). The start and stop of a replay go to `audit_log` (who, window, speed, object keys + sha256).

```python
async def run_replay(req: ReplayRequest, out: ReplaySink) -> list[EventKey]:
    seed = load_seed(req.vehicle, req.t_from - WARMUP)          # epoch, LEVEL rows, open alarms, limits timeline, acks
    state, keys, t0 = seed.state, [], None
    m0 = time.monotonic()
    for f in read_frames(req.vehicle, req.t_from - WARMUP, req.t_to):   # archive (sha-checked) else hot; sorted (t_onboard, seq)
        state = seed.apply_due(state, f)                         # LIMITS_APPLIED / ACK(after_seq) due before f
        state, events = step(state, f)                           # SAME pure core as the live worker (R5 tracker + R8 checks)
        if f.t_onboard < req.t_from:
            continue                                             # warm-up: state only, nothing emitted
        t0 = t0 or f.t_onboard
        if req.speed:                                            # 1 or 10; None in tests
            await asyncio.sleep(max(0.0, m0 + (f.t_onboard - t0).total_seconds() / req.speed - time.monotonic()))
        await out.send(replay_id=req.id, mode="REPLAY", frame=f, events=events)
        keys += [e.key for e in events]                          # (vehicle, param, epoch, trigger_seq, transition, limits_sha256)
    return keys
```

- **Proof of "same alarms as live"** (integration test, also run in ATP-07). Drive the simulator through overheat, leak (ROC), `dropout 10 s`, a simulator restart (SEQ_RESET) and a `limits.yaml` change mid-window, with one operator ACK in between. Archive. Then assert:
  1. `run_replay(window, speed=None)` == `SELECT key FROM alarm_events WHERE <window> AND kind <> 'ACK' ORDER BY t_onboard, trigger_seq` (ordered list equality, including `limits_sha256`);
  2. replay from Parquet == replay from hot (the same window before deletion);
  3. two replays are identical (R5 T9);
  4. a window that starts *inside* an active RED alarm reproduces the next transitions (this tests the seed).
- STALE is clock-driven on the live side (R9). Replay can derive it from the archived `t_received` deltas. It is not part of the equality test until R9 decides whether STALE is a keyed event (open question).

### 6. GS-UI-04 window source

- DAT-03 keeps 24 h hot. Hot delete runs only for hours with `hour_end ≤ now − 25 h`, so the hour that contains `now − 24 h` is always still hot. **The 24 h chart is always served from the hot table** (R6 S1: 58–424 ms). UI-04 never reads the archive. Charts beyond 24 h are out of scope. If they are needed, the same reader plus `time_bucket` in pyarrow or DuckDB comes next.

## Decision candidate

**Chosen:** one Parquet object per vehicle-hour, written by pyarrow (zstd, one row group, schema derived from the hot table, `nyx.*` footer metadata). Uploaded with minio-py as a single PUT carrying `x-amz-checksum-sha256`. Verified by a full re-read against the sha256 recorded in Postgres *before* upload, then a `sha256sum`-format sidecar. Job state is held in `archive_object` (WRITTEN → UPLOADED → VERIFIED → HOT_DELETED, PENDING computed). Late frames create immutable `<HH>.v<N>.parquet` versions, and readers take the latest verified one. Hot delete runs at 25 h age via R6's count-checked transaction on `hot_rows`. An asyncio-loop `archiver` service runs every 5 min. Replay reads verified objects (hot as fallback), seeds from never-pruned event tables, runs the same `step()` with limits by hash and ACKs at `after_seq`, paces on `t_onboard`, and outputs only to `replay:<id>`.

**Because:** atomic single PUT plus server-side SHA-256 enforcement plus our own re-read gives three independent checks before any delete. Each was shown on MinIO in S2, including the kill-mid-upload case. The state machine is restartable from every state without manual cleanup. Replay equality reduces to "same pure function, same inputs", which a list-equality test can check.

**Rejected:**
- Byte-reproducible Parquet as the integrity basis: breaks on a pyarrow upgrade (`created_by`).
- Multipart upload: objects are 1–2 MB, below the 5 MB part minimum. SHA-256 would only be composite, and failed uploads leave parts behind.
- Overwriting `<HH>.parquet` on late frames: there is a window where data and sidecar disagree.
- A separate `late/` object that readers must union: every reader then has to merge.
- ETag as the content hash: not MD5 for multipart or SSE-KMS.
- boto3: needs stubs and has SDK-side checksum defaults to track.
- DuckDB / `S3FileSystem` for replay reads: runtime extension download, and both skip our hash check.
- APScheduler: a dependency for one loop, and 4.x is alpha.
- Replay writing to shadow tables: not needed; in-memory plus WebSocket is enough.

## Risks / what would make us switch

- **MinIO is source-only and archived.** If `bitnamilegacy` disappears or a CVE matters, swap the compose image to another S3 server (e.g. Garage, SeaweedFS). The code uses only PUT/GET/HEAD with standard headers. Re-run S2 against the new server, since checksum enforcement differs between servers (**VERIFIED** for SeaweedFS and Garage v2: both reject a mismatched `x-amz-checksum-sha256`; Garage v1 not checked).
- **10 vehicles × 100 Hz (Q11):** ~20 MB per vehicle-hour, still a single PUT (max 5 GB — [AWS CLI note](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)). In-memory export is fine up to ~100s of MB. Beyond that, stream to a temp file and switch to multipart with a CRC64NVME full-object checksum.
- **Event tables grow forever** (never pruned). At campaign scale this is KB/day. Revisit with a per-campaign export.
- **Replay equality depends on R8 honouring purity** (no wall clock, no randomness, dict ordering stable) and on ACK `after_seq` being added.
- **Incomplete multipart uploads** (only if multipart is ever enabled) are invisible but use disk. MinIO's automatic cleanup: **VERIFIED**, stale uploads cleaned every 6 h with 24 h expiry (`internal/config/api/api.go:129-134`). Add an `AbortIncompleteMultipartUpload` lifecycle rule then.

## Open questions → assumptions.md

- **A-R7-1** (ASSUMED) Sidecar name `<HH>.parquet.sha256`, content in `sha256sum` format. The spec only says "sidecar `.sha256`".
- **A-R7-2** (ASSUMED) Late frames produce `<HH>.v<N>.parquet` (+ sidecar). "One object per vehicle per hour" means one *current* object, and older versions are kept for audit.
- **A-R7-3** (OPEN) MinIO image: `bitnamilegacy/minio:2025.5.24` (frozen, from official source) vs `pgsty/minio` (current, third party) vs building from source. Proposal: bitnamilegacy, pinned by digest, listed under "not production-ready" (Q14).
- **A-R7-4** (ASSUMED) Archive eligibility = vehicle-time watermark + 2 min, or wall clock + 30 min. Hot delete at `hour_end ≤ now − 25 h`. So the 24 h chart is always hot.
- **A-R7-5** (ASSUMED) `alarm_events` (incl. `LEVEL`, `SEQ_RESET`, `LIMITS_APPLIED`) and `limits_versions` are never pruned. Closes A-R8-10.
- **A-R7-6** (OPEN → R8) ACK rows carry `after_seq`, so replay reproduces ack-dependent transitions. Answers A-R8-11: overlay live ACKs, never ack in replay.
- **A-R7-7** (OPEN → R9) Is STALE a keyed event that replay must reproduce, or display-only? This decides whether it is in the equality test.
- **A-R7-8** (ASSUMED) Replay pacing uses `t_onboard` (vehicle time), not `t_received`. A backlog burst seen live is not reproduced as a burst.


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Synthetic sizes are an upper bound | STILL UNVERIFIED | needs real data | D-ARC-01 |
| MinIO release that enforces x-amz-checksum-sha256 | VERIFIED | minio PR #15433 + release compare | D-ARC-03 |
| `pgsty/minio` provenance | VERIFIED | repo README, org | D-ARC-09 |
| minio-py maintained | STILL UNVERIFIED | no 2026 release, commits continue | D-ARC-08 |
| Alternatives enforce the checksum | VERIFIED (SeaweedFS, Garage v2) | source | D-ARC-09 swap path |
| MinIO stale multipart cleanup | VERIFIED | minio source | D-ARC-03 (multipart unused) |

CONTRADICTED: none.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
