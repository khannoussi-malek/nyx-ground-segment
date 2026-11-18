# Pre-Gate-5 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps found at the end of Gate 4 before the Gate 5 acceptance run: right-sized Redis streams with a memory warning, tests that clean up after themselves, a daily snapshot of every non-telemetry table, a FRAMES REJECTED banner instead of a false LOSS OF SIGNAL, the known limitations written down, and the Gate 4 backlog.

**Architecture:** No new service and no new dependency. The snapshot is one more step in the archiver loop and reuses its S3 put/re-read path; FRAMES REJECTED rides in the LOS event's free-form `details` (no interface change) and only changes the console's text for the existing LOS condition; Redis sizing is one constant plus one health check.

**Tech Stack:** Python 3.14 / uv, psycopg 3, redis-py, minio, FastAPI; React/Vite/Vitest/Playwright; docker compose.

**Spec:** `docs/design.md` (D-ING-06, D-ING-07, D-ARC-*, D-UI-04/15/21) and `docs/reviews/design-review-3.md` (R3-01..R3-04, Tasks 28-29). Owner decisions 2026-11-18 (coordinator session): build R3-01/R3-02 (snapshot + `make reset` guard) and R3-03 (FRAMES REJECTED); R3-04 is a known limitation; the `design.md` split waits for the Gate 5 docs.

## Global Constraints

- Every change ships with its test in the same commit; write the test first and see it fail (D-ENG-14/15).
- Conventional commit subjects: `feat|fix|refactor|docs|test|chore|perf|ci`; never `style:`; no Co-Authored-By and no Claude/AI mention (CI `scripts/check_commits.py` enforces both).
- `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src tests scripts perf` clean; coverage stays ≥ 80 % and the `after_coverage` except-handler check passes (every new `except` is executed by a test or allowlisted with a reason).
- UI: `cd ui && npx vitest run && npx tsc --noEmit && npm run build`.
- No new runtime dependency. No `pg_dump`, no new image.
- Never `docker compose down -v` or `make reset` on a shared stack; never touch the paperlab stack. System and e2e runs go through `sh /Users/malek/try/nyx/.superpowers/syslock.sh <cmd>`.
- Redis cleanup in tests trims with `approximate=False` (redis-py defaults to approximate).
- Live vehicles are `NYX-TA1`, `NYX-TA2`; `NYX-TA9` is recorded-only. Test vehicles come from `tests.support.env.vid()` = `"NYX-T" + 8 upper-case hex`.
- A change to a `D-*` decision updates its row in `docs/design.md` in the same commit; `tests/meta` keeps the docs honest.

## Review Focus

1. A snapshot taken while ingest and the API are writing must be one consistent view (a row committed after the snapshot transaction began is absent) — Task 3 `test_snapshot_is_one_consistent_view`.
2. MinIO failing during the snapshot PUT must never fail or delay the archive pass or a hot delete, and leaves no `.sha256` sidecar — Task 3 `test_snapshot_failure_never_blocks_the_pass`, `test_killed_snapshot_leaves_no_sidecar`.
3. A plain dropout (nothing rejected) must still read LOSS OF SIGNAL, and frames that resume after a rejection must clear the banner with the AOS — Task 4 `test_plain_dropout_has_no_rejected`, `banners.test.ts`.
4. Test cleanup must never delete a key of `NYX-TA1`, `NYX-TA2` or `NYX-TA9` — Task 2 `test_purge_matches_only_test_vehicles`.
5. Redis above 80 % of `maxmemory` must show `degraded` on ingest `/healthz` before writes start failing, and `INFO` failing stays `degraded` — Task 1 `test_ingest_status_memory_warning`.

---

### Task 1: Right-size the Redis streams and warn at 80 % memory

**Files:**
- Modify: `src/gs/simulator/main.py:26` (`MAXLEN`), `src/gs/ingest/liveness.py` (`ingest_status`, `refresh_memory`), `src/gs/ingest/worker.py:87` (`Ctx`), `src/gs/ingest/main.py:118`, `perf/soak_count.py:161` (docstring), `docs/design.md` (D-ING-06, D-ING-07 rows)
- Test: `tests/unit/test_ingest_status.py` (create if absent, else extend), `tests/unit/test_sim_maxlen.py`

**Interfaces:**
- Produces: `MAXLEN = 108_000` in `gs.simulator.main`; `MEMORY_WARN = 0.8` in `gs.ingest.liveness`; `Ctx.max_memory: list[int | None]`; `ingest_status(seen, owned, now, used_memory, counters, max_memory: int | None = None)`.

Why 108 000: the soak counter reads `tm:<v>` as the record of what was sent, so a stream must hold the 2 h soak window plus margin (3 h at 10 Hz). Measured 2026-11-17: 360 000 entries = 150-157 MB per stream (≈ 440 B/entry), not the ~40 MB D-ING-06 states; 108 000 ≈ 47 MB.

- [ ] **Step 1: Failing tests**

```python
def test_maxlen_holds_the_soak_window_with_margin() -> None:
    from gs.simulator.main import MAXLEN
    assert MAXLEN == 108_000 and MAXLEN >= 2 * 3600 * 10 * 1.25  # 2 h soak at 10 Hz + 25 %

def test_ingest_status_memory_warning() -> None:
    ok = ingest_status({}, set(), 0.0, 400, {}, max_memory=1000)
    hot = ingest_status({}, set(), 0.0, 800, {}, max_memory=1000)
    assert ok[0] == "ok" and ok[1]["redis_memory_pct"] == 40
    assert hot[0] == "degraded" and hot[1]["redis_memory_pct"] == 80
    assert ingest_status({}, set(), 0.0, None, {}, max_memory=1000)[0] == "degraded"
    assert ingest_status({}, set(), 0.0, 900, {}, max_memory=0)[1]["redis_memory_pct"] is None  # no maxmemory set
```

- [ ] **Step 2:** `uv run pytest --no-cov -q tests/unit/test_sim_maxlen.py tests/unit/test_ingest_status.py` → FAIL.
- [ ] **Step 3:** Set `MAXLEN = 108_000  # 3 h at 10 Hz: the 2 h soak window + margin; ~47 MB per vehicle (D-ING-06)`. `refresh_memory` also stores `int(info["maxmemory"])` in `ctx.max_memory[0]` (None on failure). `ingest_status` adds check `redis_memory_pct` = `round(100 * used / max)` (None when either is None or max is 0) and returns `degraded` when it is ≥ `MEMORY_WARN * 100`. Wire `ctx.max_memory[0]` in `ingest/main.py`. Update the soak_count docstring ("108k entries are kept, 3 h at 10 Hz").
- [ ] **Step 4:** Update D-ING-06 (value, "3 h at 10 Hz", "~47 MB per vehicle measured 2026-11-17, ≈ 440 B/entry; holds the 2 h soak window") and D-ING-07 (ingest `/healthz` degraded at ≥ 80 % of `maxmemory`).
- [ ] **Step 5:** Tests pass; `uv run pytest --no-cov -q tests/unit tests/meta`; integration `tests/integration/test_liveness.py` through syslock.
- [ ] **Step 6: Commit** `fix: size tm streams for the soak window and warn at 80 % Redis memory`

---

### Task 2: Tests clean up their Redis keys; the replay fixture trims NYX-TA9

**Files:**
- Modify: `tests/support/env.py` (`vid`), `tests/integration/conftest.py`, `tests/system/conftest.py`, `tests/support/replay_fixture.py`, `docs/atp/ATP-07.md`
- Create: `tests/support/keys.py`
- Test: `tests/unit/test_test_keys.py`

**Interfaces:**
- Produces: `TEST_VEHICLE = re.compile(r"^NYX-T[0-9A-F]{8}$")`; `test_keys(r: redis.Redis) -> list[str]` (every key whose vehicle part matches `TEST_VEHICLE`: `tm:`, `tc:`, `tcack:`, `hb:veh:`, `cmdlock:<v>:*`); `purge_test_keys(r: redis.Redis) -> int` (DEL in batches of 500, returns the count).

- [ ] **Step 1: Failing test** `test_purge_matches_only_test_vehicles`: against a fake key list `["tm:NYX-TA1", "tm:NYX-TA2", "tm:NYX-TA9", "hb:veh:NYX-TA1", "tm:NYX-TDE036594", "tcack:NYX-T5D3E73E4", "cmdlock:NYX-T6145D806:isolate_valve", "tm:dlq", "ui:events", "tm:NYX-TDE03659"]`, the selection is exactly the three `NYX-T<8 hex>` keys (`tm:NYX-TDE03659` has 7 hex digits and stays).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `tests/support/keys.py` with `SCAN MATCH` per prefix and the regex on the vehicle part. A session-scoped autouse fixture in `tests/integration/conftest.py` and `tests/system/conftest.py` calls `purge_test_keys(client("admin"))` once at session start (clears earlier runs' leftovers, ~1 790 on the main stack) and once at session end.
- [ ] **Step 4:** `replay_fixture.main()`: after the archived-count wait, when `XPENDING tm:NYX-TA9 ingest` is 0 and the group lag is 0, `XTRIM tm:NYX-TA9 MAXLEN 0` with `approximate=False`; otherwise leave it and print a warning to stderr. Add to ATP-07: "Run on a fresh stack or before e2e: the replay e2e fixture leaves an open ARCHIVE_CORRUPT on NYX-TA9 hour B, so the archiver `/healthz` reads `degraded` afterwards."
- [ ] **Step 5:** Unit test passes; one integration module and one system module run through syslock, then `redis-cli SCAN 0 MATCH 'tm:NYX-T*'` via the admin client returns only `tm:NYX-TA1`, `tm:NYX-TA2`, `tm:NYX-TA9`.
- [ ] **Step 6: Commit** `test: purge throwaway vehicle keys and trim NYX-TA9 after the replay fixture`

---

### Task 3: Daily state snapshot to object storage and a `make reset` guard (R3-01, R3-02; D-ARC-12)

**Files:**
- Create: `src/gs/archiver/snapshot.py`, `scripts/restore_state.py`, `scripts/reset_guard.py`
- Modify: `src/gs/archiver/main.py` (one call per loop pass), `src/gs/archiver/status.py` (`archiver_status`), `Makefile` (`reset`), `docs/design.md` (new D-ARC-12 row), `docs/operator-guide.md`, `README.md` (Limitations)
- Test: `tests/integration/test_snapshot.py`, `tests/unit/test_restore_state.py`, `tests/unit/test_archiver_status.py` (extend), `tests/unit/test_reset_guard.py`

**Interfaces:**
- Consumes: `gs.archiver.s3.put(c, bucket, key, data) -> str` (sha256 hex, server-verified), `gs.archiver.s3.get(c, bucket, key) -> bytes`, `ArchiverCtx`.
- Produces: `TABLES: tuple[str, ...]` = `("limits_versions", "test_session", "commands", "alarms", "alarm_events", "alarm_ack_request", "events", "seq_state", "engine_state", "session_report", "archive_object", "audit_log")` — every table but `telemetry`, in foreign-key order (verify against `migrations/*.sql` and reorder if a FK requires it; the round-trip test is the proof); `snapshot_key(t: datetime) -> str` = `state/YYYY/MM/DD/HHMM.tar.gz`; `async def take_snapshot(ctx: ArchiverCtx, conn: psycopg.AsyncConnection, now: datetime) -> str` (returns the key); `SNAPSHOT_EVERY_H` (env, default 24, > 0); `archiver_status(..., last_snapshot: datetime | None = None, snapshot_every_h: float = 24)`.

- [ ] **Step 1: Failing tests** (integration on a throwaway database, as the archiver tests do):
  - `test_snapshot_roundtrip`: seed one row in every table of `TABLES`, `take_snapshot`, restore with `scripts/restore_state.py` into a second empty throwaway database, row sets equal per table; the audit trigger still refuses `UPDATE audit_log`; inserting a new `test_session` after restore gets an id above the restored ones (identity sequences reset with `setval`).
  - `test_snapshot_is_one_consistent_view`: a row committed by another connection after the snapshot's `REPEATABLE READ` transaction took its first read is absent from the archive.
  - `test_killed_snapshot_leaves_no_sidecar`: a failing PUT of the tarball (monkeypatched `s3.put`) leaves no `<key>.sha256`.
  - `test_snapshot_failure_never_blocks_the_pass`: with `take_snapshot` raising, `run_pass` still archives and deletes as before and logs one ERROR.
  - `test_restore_refuses_non_empty_target` (unit, fake connection): any `TABLES` row present → exit code 2, message `restore_state: target database is not empty (<table> has rows)`.
  - `test_reset_refuses_with_archive_rows` (unit): `reset_guard.main(rows=3, force=False)` returns 1 and prints `make reset deletes the database, Redis and the MinIO archive (3 archived hours). Run FORCE=1 make reset to confirm.`; `rows=0` or `force=True` returns 0.
  - `test_archiver_status_snapshot_age` (unit): `last_snapshot` older than `2 * snapshot_every_h` → `degraded`, check `last_state_snapshot` is its ISO-Z time; `None` before the first pass is not degraded; `None` after a completed pass is degraded.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `take_snapshot`: one `REPEATABLE READ` read-only transaction; per table `COPY (SELECT * FROM <t> ORDER BY <primary key>) TO STDOUT (FORMAT csv, HEADER)` into an in-memory `tar.gz` (stdlib `tarfile`, one `<table>.csv` member each); `s3.put` the tarball, `s3.get` it back and compare sha256, then PUT `<key>.sha256` last. The archiver calls it at most once per `SNAPSHOT_EVERY_H`, after the pass's deletes, inside its own `try/except Exception` that logs and never fails the pass; the time of the last good snapshot goes into the health box.
- [ ] **Step 4:** `scripts/restore_state.py <key>`: reads `.env` for the owner DSN and MinIO archiver credentials, verifies the sidecar hash, refuses a non-empty target, `COPY … FROM STDIN` in `TABLES` order as `gs_owner`, then `setval` every identity sequence to its column's max.
- [ ] **Step 5:** `Makefile` `reset:` runs `uv run python scripts/reset_guard.py` first (counts `archive_object` rows through the `db` container; `FORCE=1` skips the refusal). D-ARC-12 row; operator guide section "Restore the state"; README limitation "non-telemetry history is lost back to the last snapshot (≤ SNAPSHOT_EVERY_H)".
- [ ] **Step 6:** All new tests pass; `tests/integration/test_archiver*.py` and `tests/integration/test_health.py` through syslock; `tests/meta`.
- [ ] **Step 7: Commit** `feat: snapshot every non-telemetry table to the archive daily and guard make reset`

---

### Task 4: FRAMES REJECTED instead of a false LOSS OF SIGNAL (R3-03; D-UI-25)

**Files:**
- Modify: `src/gs/ingest/worker.py` (`process_batch`, `_run_vehicle`, `_silence`), `src/gs/ingest/link.py` (`on_silence`, `_los`), `src/gs/ingest/worker.py` `Ctx`, `ui/src/banners.ts`, the component that renders the vehicle summary text (find the `vehicleCondition` caller), `docs/operator-guide.md` (banner precedence list), `docs/design.md` (new D-UI-25 row; note it overrules SR-02 for this banner)
- Test: `tests/unit/test_link.py` (extend), `tests/integration/test_los.py`, `ui/src/banners.test.ts`, `e2e/atp04-los.spec.ts` (one step)

**Interfaces:**
- Produces: `Ctx.rejected: dict[str, Counter[str]]` (vehicle → DLQ reason counts since the vehicle's last new frame); `on_silence(s, vehicle, prev, rejected: Mapping[str, int] | None = None)`; LOS event `details["rejected"] = {"count": int, "reason": str}` only when the count is > 0, reason = the most frequent, ties broken by `DlqReason` order; UI `rejectedText(count: number, reason: string): string`.

- [ ] **Step 1: Failing tests**
  - `tests/unit/test_link.py::test_los_carries_rejected` / `::test_los_without_rejected_has_no_key`.
  - `tests/integration/test_los.py::test_los_names_rejected_frames`: stream frames whose `t_onboard` is 10 min ahead → one LOS whose details have `rejected.count >= 1` and `rejected.reason == "t_onboard_range"`; `::test_plain_dropout_has_no_rejected`; `::test_rejected_count_resets_on_a_new_frame` (rejected, then a good frame, then a dropout → that LOS has no `rejected`).
  - `ui/src/banners.test.ts`: `rejectedText(1, "t_onboard_range") === "FRAMES REJECTED – 1 frame, t_onboard_range – check tm:dlq"`, `rejectedText(12, "schema") === "FRAMES REJECTED – 12 frames, schema – check tm:dlq"`; the vehicle summary shows that text when the condition is `LOSS OF SIGNAL` and the vehicle's newest link event is a LOS with `details.rejected`; NO LIVE CONNECTION and both GROUND PROCESSING DOWN banners still outrank it; after an AOS the summary has neither text.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Server: count each DLQ record's `reason` into `ctx.rejected[vehicle]` in `process_batch`; clear it where `_run_vehicle` sets `last_new` on a new frame; `_silence` passes it to `on_silence`, `_los` adds the `rejected` detail. No new event kind, timer or interface (`EventView.details` is free-form JSON).
- [ ] **Step 4:** Console: the condition stays `"LOSS OF SIGNAL"` (same precedence, tone and key, D-UI-21); only the displayed text becomes `rejectedText(...)` when the store's newest link event for the vehicle is a LOS with `details.rejected`.
- [ ] **Step 5:** One Playwright step in `atp04-los.spec.ts` (or a new short spec): start a `dropout` fault on `NYX-TA1` (`duration_s: 8`, so the live simulator sends nothing), XADD three frames with `t_onboard` 10 min ahead to `tm:NYX-TA1` through a helper using the admin client, wait for the banner text `FRAMES REJECTED – 3 frames, t_onboard_range – check tm:dlq` within 5 s of the LOS, then let the dropout end and see the text clear with the AOS; `afterEach` clears the fault.
- [ ] **Step 6:** Unit, vitest, tsc, build; `tests/integration/test_los.py` and the e2e spec through syslock.
- [ ] **Step 7: Commit** `feat: name rejected frames on a loss of signal (FRAMES REJECTED)`

---

### Task 5: Known limitations and Gate 4 doc fixes (docs only; light review)

**Files:**
- Modify: `docs/operator-guide.md`, `README.md`, `CHANGELOG.md`, `docs/perf/scaling.md` and `perf/scaling.py` (its text), `docs/design.md` (D-ARC-09 limitation text)
- Test: `tests/meta` (existing), `tests/unit/test_scaling.py` (extend for the text change)

- [ ] **Step 1:** Operator guide + README limitation (R3-04): "A command's 5 s / 15 s timeouts start only once ground processing has caught up with its backlog (D-CMD-05). While it is behind, a command can stay SENT or RECEIVED past its limit with no note on screen; the GROUND PROCESSING banner and the `/healthz` backlog say why."
- [ ] **Step 2:** Remove the stale "landing in this gate" lines (`README.md:8`, `CHANGELOG.md:10`).
- [ ] **Step 3:** `perf/scaling.py`: the Redis growth line prints `not measurable (streams capped at MAXLEN)` when the before/after delta is ≤ 0; the headroom line states the per-worker share (vehicles per worker) next to the fleet total. Test first in `tests/unit/test_scaling.py`; regenerate `docs/perf/scaling.md` with `uv run python -m perf.scaling docs/perf/run-p-2026-11-17`.
- [ ] **Step 4:** D-ARC-09 limitation: "no delete is not no overwrite: the archiver's policy allows PutObject on `archive/*`; the code never overwrites (version keys, pre-PUT refusal); MinIO object locking is the later answer."
- [ ] **Step 5:** `uv run pytest --no-cov -q tests/meta tests/unit/test_scaling.py`.
- [ ] **Step 6: Commit** `docs: record the known limitations and fix the Gate 4 leftovers`

---

### Task 6: Gate 4 code backlog batch

**Files:** as named per item; tests next to the code they pin.

Each item: failing test first, then the fix, one commit per item (`fix:` or `test:`).

- [ ] **6a** `ui/src/ReplayPage.tsx`: the EventSource `error` path clears `run.current` (test: an `error` after open, then Start creates a second replay); and `busy` returns to false when `createReplay` throws instead of returning `ok: false` (test with a rejecting fetch).
- [ ] **6b** G4-19 everywhere: `GET /api/alarms` and `GET /api/commands` without `vehicle` exclude non-live vehicles (`NYX-TA9`), as the snapshot does (`src/gs/api/alarms.py:39`, `src/gs/api/commands.py:371`); integration tests with a TA9 row present.
- [ ] **6c** G4-21: `perf/soak_count.py` fails the count when a stored in-window frame is missing from `sent` (set difference), message `soak: N stored frames are missing from tm:<v> (deleted from the stream?)`; unit test plus an integration test that XDELs one entry.
- [ ] **6d** G4-28 follow-ups in `src/gs/alarms/checks/roc.py`: the span test gets the same `(1 - _EPS)` tolerance as the count (`span < (need - 1) * s.period_s * (1 - _EPS)`); strengthen the burst test's second assertion to "the first judged reading after the burst is NOMINAL and comes within 1 s of normal cadence".
- [ ] **6e** Flaky `tests/integration/test_alarm_ack_api.py::test_reraised_after_request_superseded` (Redis socket timeout under load): use superpowers:systematic-debugging; fix the root cause (not a longer timeout unless the cause is the client's own socket timeout being below one BLOCK_MS read) and prove it by 20 consecutive passes through syslock while `tests/integration/test_hub.py` runs alongside.
- [ ] **6f** Replay concurrency (Task 13 M4): at most 2 open replays per user; a third `POST /api/replays` gets 429 `TOO_MANY_REPLAYS` with message `at most 2 replays at once; stop one first`; integration test; operator guide line; the limit goes into the replay decision row in `docs/design.md` (new decision, spec silent: one console user rarely needs more, each replay holds a Postgres connection for up to hours).
- [ ] **6g** FRAMES REJECTED follow-ups (Task 4 review): rejected entries that come after the batch's last stored frame (entry order) still count after the clear, so a batch "good frames, then rejected ones" names them on the following LOS (integration test with one mixed batch); a vitest that fails if `bannerText` drops its `kind === "LOS"` check (newest link an AOS carrying a stale `rejected` → no FRAMES REJECTED text).
- [ ] **6h** `tests/system/test_json_logs.py::test_every_service_logs_json` reads only lines written since the test started (it failed after test_ownership's deliberate watchdog dump); a test-order run proves it.
- [ ] **Verification:** unit + meta, the touched integration modules through syslock, vitest/tsc/build for 6a and 6g.

---

## After the tasks

Final whole-branch review (opus, per `CLAUDE.local.md`), one fix wave, PR to `main` (owner approves push and merge), CI green.
