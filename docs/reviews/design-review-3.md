# Design review 3 – architecture point of view at Gate 4

2026-11-16 · reviewer session (reads only; nothing in `src/`, `docs/design.md` or the plan was changed)

Scope: the spec, `docs/design.md` §1–§5, the Gate 4 plan and its progress ledger, and the code paths named below. The core design stands: one writer per vehicle with the lock on the writing session, commit then ack with an idempotent key, engine state in the frame transaction, replay as recorded truth, command outcomes from stored evidence. This note lists five findings and proposes three tasks for Track Z. Each proposal needs an owner ruling, typed in the coordinator session, before it is built.

## Findings

| ID | Finding | Evidence | Serves |
| --- | --- | --- | --- |
| R3-01 | **Everything except telemetry has one copy.** `events`, `alarm_events`, `alarms`, `commands`, `audit_log`, `test_session`, `session_report`, `limits_versions` and `archive_object` live in one Postgres volume. The archiver exports telemetry only, and replay reads its alarm and event rows from Postgres. Losing that volume leaves Parquet objects with no alarms, no audit log and no index of which objects were verified. | `src/gs/replay/rows.py:18`, `:125`; no table but `telemetry` is read in `src/gs/archiver/parquet.py`; no backup anywhere in the design | DAT-06, CMD-04, review Q4, Q12, Q14 |
| R3-02 | **`make reset` deletes the archive.** It runs `docker compose down -v`, which drops the MinIO volume with the database, and the design names `make reset` as the way to change the schema. | `Makefile:16`, D-ENG-12 | DAT-04, DAT-05 |
| R3-03 | **A ground rejection looks like a link loss.** Frames rejected for `t_onboard_range` (a bench clock more than 5 min ahead or 7 days behind) go to `tm:dlq` and never count as a new frame, so after 2 s the console shows LOSS OF SIGNAL. The operator is sent to check the link while the ground is discarding every frame. The FRAMES REJECTED banner was cut by SR-02 for having no pass condition. | `src/gs/ingest/worker.py:554` (`last_new` moves only on a new frame), A-E1-9, ICD-B-02 | UI-02, review Q1, spec §3 "swapping the simulator for real bench hardware" |
| R3-04 | **A command's timeout waits for telemetry and the console does not say so.** The tracker applies the 5 s and 15 s timeouts only when the owner has caught up on its backlog (D-CMD-05). During a backlog a command stays SENT or RECEIVED past its limit with no explanation. Nothing in `src/gs/api` or `ui/src` carries the backlog state. | `grep -ri 'caught_up\|backlog' src/gs/api ui/src` returns nothing | CMD-02, UI-06, review Q3 |
| R3-05 | **The ownership protocol has no single picture, and `design.md` is a register, not a design document.** Lock, hand-back, steal, fence, reclaim, watchdog and heartbeat are spread over D-ING-09 to D-ING-26; the defects reviews found were in their interplay (G23, G24, R2-05, R2-06). `design.md` is 290 KB with table cells of several KB. Spec §6 asks for a diagram, data flow, key decisions and trade-offs, and §7 expects each answer in under a minute. | `docs/design.md` §2.2 | ENG-08, review Q8–Q10 |

## Correction to the first verbal version of R3-01

The first proposal was a second Parquet object per hour holding that hour's event rows, with replay reading archived hours from MinIO alone. That is wrong for this design: rows keep arriving for an hour after it is archived (`ARCHIVE_CORRUPT` is keyed to the hour start, `LATE_VERIFICATION` and late ACKs are written later), and D-DB-09 never prunes these tables, so a per-hour object would go stale and replay would need two read paths. A periodic snapshot of the small tables is smaller, covers the audit log and commands as well, and leaves replay untouched.

## Proposed tasks (Track Z, before Task 25 so the ATP runs include them)

### Task 28: State snapshot to object storage (R3-01, R3-02; proposed D-ARC-12)

**Files:** `src/gs/archiver/snapshot.py` (new), `src/gs/archiver/main.py` (one call per loop pass, at most one snapshot per `SNAPSHOT_EVERY_H`, default 24), `scripts/restore_state.py` (new), `Makefile` (`reset` guard), `docs/operator-guide.md`, `README.md` limitations, `tests/integration/test_snapshot.py`, `tests/unit/test_restore_state.py`, `tests/meta/test_compose.py`.

- One object `state/<YYYY>/<MM>/<DD>/<HHMM>.tar.gz` plus `.sha256`: one CSV per table from `COPY (SELECT … ORDER BY <key>) TO STDOUT (FORMAT csv, HEADER)` over psycopg, all inside one `REPEATABLE READ` transaction so the tables agree with each other. No `pg_dump`, no new image, no new dependency.
- Same integrity path as the hourly object: single PUT with `x-amz-checksum-sha256`, full re-read, hash compared, sidecar last. Reuse the archiver's existing put and re-read helpers.
- `/healthz` reports `last_state_snapshot`; older than 2 × `SNAPSHOT_EVERY_H` → `degraded`.
- `scripts/restore_state.py <object>`: verifies the hash, then `COPY … FROM STDIN` into an **empty** database as `gs_owner` (the app role cannot write `audit_log` history). Refuses a non-empty target.
- `make reset` refuses when `archive_object` has rows unless `FORCE=1`, and says what will be deleted.
- Tests: `test_snapshot_roundtrip` (seed rows in every table, snapshot, restore into a throwaway database, row sets equal, audit triggers still refuse UPDATE); `test_snapshot_is_one_consistent_view` (a row committed after the snapshot transaction began is absent); `test_killed_snapshot_leaves_no_sidecar`; `test_restore_refuses_non_empty_target`; `test_reset_refuses_with_archive_rows`.
- Not built: point-in-time recovery, WAL shipping, a second MinIO copy. README limitations says the loss window is one snapshot interval for non-telemetry rows.

### Task 29: Say why there are no frames (R3-03; proposed D-UI-25, overrules SR-02 for this banner)

**Files:** `src/gs/ingest/worker.py` (count DLQ'd entries per vehicle since the last new frame), the LOS event details model and `Link` message (one optional field `rejected: {count, reason}`), `interfaces/**` and `ui/src/api/schema.d.ts` via `make gen`, `ui/src/banners.ts`, `docs/operator-guide.md` precedence list, tests.

- The owner already decides LOS when no **new** frame arrived for 2 s. When that decision is taken and at least one entry of the vehicle was DLQ'd in the same window, the LOS event carries `rejected.count` and the most frequent `rejected.reason`. No new event kind, no new timer, no change to the alarm engine or to replay ordering.
- Console: a LOS whose event has `rejected` reads **FRAMES REJECTED – n frames, `<reason>` – check `tm:dlq`** in the LOS position of the precedence function (NO LIVE CONNECTION > GROUND PROCESSING DOWN > FRAMES REJECTED = LOS > FROZEN > STALE). Same tone, same key as LOS (D-UI-21).
- This changes a frozen generated interface, so it needs a ruling and runs alone, like Task 11.
- Tests: `integration/test_los.py::test_los_names_rejected_frames` (stream frames with `t_onboard` 10 min ahead → one LOS with `rejected.count ≥ 1`, reason `t_onboard_range`); `::test_plain_dropout_has_no_rejected`; `ui/src/banners.test.ts` (text and precedence); one Playwright step in the dropout spec.

### Task 30: Show a delayed command verdict (R3-04; proposed D-UI-26)

**Files:** `src/gs/ingest/worker.py` (publish `caught_up` in the existing `hb:veh:<v>` value instead of `1`), `src/gs/api/hub.py` (`hb.processing` already carries per-vehicle presence; add the flag), `ui/src/CommandPanel.tsx`, tests.

- While a vehicle's owner is not caught up, an open command past its window shows **VERDICT DELAYED – ground processing behind** instead of a bare SENT or RECEIVED. Display only: the tracker and `decide()` are unchanged.
- Also an interface change (`hb` message), so it shares Task 29's ruling and `make gen` run.
- Tests: `integration/test_hub.py::test_hb_carries_caught_up`; `ui/src/commands.test.tsx` (open command past 5 s with `caught_up=false` → the text; with `true` → unchanged).

### Folded into Task 27 (docs only, R3-05)

- `docs/design.md` gets a new §1.1: the ownership state diagram (waiting → owner → hand-back / stolen / exit) and one table of its timers (name, value, who runs it, what it triggers, test).
- Move §7, §9, §10, §12 and §13 (review dispositions) to `docs/design-history.md` and keep `design.md` for the current design. No decision text changes.

## Order

Task 29 and 30 first, together and alone (they regenerate interfaces), then Task 28, then Tasks 24–27 as planned. Task 26's 2 h soak then runs with the snapshot loop active, which is the only measurement it could affect.

## Rulings requested from the owner

1. Build Task 28 (state snapshot + `make reset` guard)? Default if not: README limitation "non-telemetry history has one copy".
2. Overrule SR-02 for the FRAMES REJECTED text (Task 29)?
3. Build Task 30, or record R3-04 as a known limitation in the operator guide?
4. Split `design.md` as described under Task 27?
