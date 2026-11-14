# R16 – Archive and replay integrity: what the literature changes

**Drives:** GS-DAT-04, GS-DAT-05, GS-DAT-06   **Answers review Q:** 4, 12

PaperLab workspace: **Nyx ground segment design**. Builds on [R7](R7-archive-replay.md) and D-ARC-01..10 / D-REP-01..06. Passages were found with `plab.py find` in the full text unless marked UNVERIFIED.

## Question

D-ARC verifies the **bytes** that go to MinIO (sha256 on PUT, full re-read, sidecar). Two things are left open: does the archive hold the **same rows** as the hot table it replaces, and does it **stay** correct after the hot rows are deleted, when it is the only copy?

## Findings

### F1 – The row-count delete check can't tell two full hours apart (gap, medium-high)

- Today's checks: the re-read compares the sha256 and `num_rows` (R7 §54). The hot delete commits only if the deleted row count equals `archive_object.rows` (D-ARC-05). Nothing compares the **contents** or the **time span** of the archived rows with the rows being deleted.
- At Nyx's normal load, every full vehicle-hour has exactly **36,000 rows** (10 Hz × 3600 s, R7 spike data). Suppose the export and the delete disagree on the hour boundary: an off-by-one hour, local time versus UTC in `time_bucket`, or `[h, h+1)` versus `(h, h+1]`. The count check then still passes, and hour H+1's hot rows are deleted after hour H was archived. That data is lost, and nothing reports it until someone replays H+1.
- The end-to-end argument (Saltzer, Reed & Clark 1984) says that checks at lower layers, such as a transport checksum, can't replace a check at the endpoints: here, the rows replay will read against the rows being deleted. Krioukov et al. (FAST 2008) found that layered protections still miss combinations of faults. **VERIFIED**: Saltzer et al. p. 2 ("can completely and correctly be implemented only with the knowledge and help of the application"), p. 3 (careful file transfer needs "an end-to-end reliability guarantee – in this case, a checksum"); Krioukov et al. p. 2 ("We find holes in all of the schemes examined").
- **Change (two small checks):**
  1. **At verify (D-ARC-03), compare the content.** Decode the re-read Parquet, fetch the hot rows of the same `(vehicle, hour)` with the same schema, sort both by `(t_onboard, seq)`, and compare with `pyarrow.Table.equals`. That is 36 k rows, a few ms. Store `t_min` and `t_max` in `archive_object` (columns, not footer metadata, so SR-05 stands).
  2. **At hot delete (D-ARC-05), check the range.** In the same transaction, check `count`, `min(t_onboard)` and `max(t_onboard)` of the deleted rows against the row, and roll back on any mismatch. Use one shared `hour_bounds(vehicle, hour)` helper for export and delete.
- **Test:** `test_delete_wrong_hour_refused`: archive hour H, then call the delete with H+1's bounds, both holding 36,000 rows. Expected: rollback, an alert, and H+1 still hot. Today this test would delete H+1.

### F2 – After the hot delete, nothing re-checks the archive until a replay (gap, low-medium; detection only)

- After `HOT_RETENTION_H` (25 h), the single-node MinIO object is the only copy. D-REP-01 checks its hash when a replay reads it. So the first time anyone learns about a corrupted object is when someone replays that window, possibly in front of the review board.
- Bairavasundaram et al. (FAST 2008), a 41-month field study: silent data corruptions "cannot be detected or repaired by the disk drive itself. Detecting and recovering from data corruption requires protection techniques beyond those provided by the disk drive" (p. 1). The DuckDB authors draw the same lesson: "We have learned to distrust the hardware the database is running on" (Raasveldt & Mühleisen 2019, p. 4).
- Proactive scrubbing, so latent faults are found before they combine with another fault, is the recommendation of Schroeder et al. (FAST 2010) and Baker et al. (EuroSys 2006). **VERIFIED** in part: Baker et al. model latent faults and their detection time (p. 12); Schroeder et al. evaluate scrubbing policies on field data (p. 10). No numbers are quoted here.
- **Change:** a **rolling scrub** in the existing `gs-archiver` loop (D-ARC-07). Each pass re-hashes the K objects with the oldest `last_checked_at` (new column in `archive_object`). A mismatch raises an `ARCHIVE_CORRUPT` event and marks `/healthz` `degraded` (D-ARC-10).
  - Cost: about 1 MB and 3 ms per object (R7 S2). A month for two vehicles is about 1,440 objects, so a full pass takes seconds.
  - Repair stays out of scope: there is one copy, and it belongs on the §4 "not production-ready" list next to Redis/Postgres replication.
- Test: corrupt one byte of a stored object through the MinIO root user in a test. The next scrub pass raises `ARCHIVE_CORRUPT`, and replay of that hour refuses with a clear error.

### Confirmations (no change)

- **Replay determinism across machines (D-REP-05 b).** The derived-expression grammar (D-EXT-05) allows only `+ − × ÷` on Python floats. IEEE 754 rounds these the same way on every platform, so the recompute gives the same keys on the arm64 Mac and amd64 CI. Keep it that way: functions such as `exp`, `log` or `pow` come from the platform's libm and can differ in the last bit. Adding any of them would need the determinism test on both architectures.
- **Single PUT with `x-amz-checksum-sha256`, full re-read, hash recorded only after verification** (D-ARC-02..04) is a sound transport-level check. F1 adds the missing check at the endpoints.
- **Parquet, zstd, one row group per vehicle-hour** (D-ARC-01): the columnar-format evaluation by Zeng et al. (PVLDB 2023) gives no reason to change format or layout at about 1 MB per object (paper ingesting; no specific claim taken from it).

## Decision candidate

Chosen: F1 (content check at verify, plus a count-and-range check at delete) and F2 (rolling scrub with an event, no repair). Because: F1 closes a data-loss path that the steady 10 Hz rate makes invisible, and F2 moves corruption detection from review-board time to within one scrub cycle. Both reuse the existing archiver loop and table. Rejected:
- Comparing a SQL text digest with a Python digest: float text formatting differs between Postgres and Python, and comparing in Python with pyarrow avoids that.
- A second MinIO or erasure set: more infrastructure than a bench tool needs; it goes on the §4 list.

## Risks / what would make us switch

- If MinIO later runs erasure-coded (multi-drive), its own bitrot healing covers part of F2, and the scrub then only checks our hash.
- Telemetry with gaps makes hour counts unequal, which hides F1 less well. F1 targets the steady state, where hiding is perfect.

## Open questions → assumptions.md

- A-R16-1: How often should the scrub run? Default: K = 20 objects per 5-min loop, which is about 5,760 per day, enough to cover the whole archive daily for a month of data.
- A-R16-2: Should replay of a corrupted hour show the remaining hours with a visible gap, or refuse the whole window? Default: show the gap with an `ARCHIVE_CORRUPT` marker, like a GAP event.

## Sources (PaperLab: Nyx ground segment design)

Saltzer, Reed & Clark 1984 End-to-end arguments in system design (ACM TOCS) · Krioukov et al. 2008 Parity Lost and Parity Regained (FAST) · Bairavasundaram et al. 2008 An analysis of data corruption in the storage stack (FAST; ACM TOS 10.1145/1416944.1416947) · Bairavasundaram et al. 2007 An analysis of latent sector errors in disk drives, 10.1145/1269899.1254917 · Schroeder, Damouras & Gill 2010 Understanding latent sector errors and how to protect against them (FAST; ACM TOS 10.1145/1837915.1837917) · Baker et al. 2006 A fresh look at the reliability of long-term digital storage, 10.1145/1217935.1217957 · Zeng et al. 2023 An Empirical Evaluation of Columnar Storage Formats, 10.14778/3626292.3626298


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| End-to-end argument; layered protection has holes | VERIFIED | Saltzer p. 2–3; Krioukov p. 2 | F1 (D-ARC-03/05) |
| Scrubbing / latent-fault detection recommended | VERIFIED (in part) | Baker p. 12; Schroeder p. 10 | F2 (scrub) |

CONTRADICTED: none.
