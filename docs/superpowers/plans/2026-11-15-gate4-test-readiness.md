# Gate 4 – Test Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the design's "4 – Test readiness" row: the archiver with content, range and scrub checks and versioned late objects (D-ARC-01..11), the test controls incl. `HOT_RETENTION_H`, the `archive:*` hooks and backfill into `NYX-TA9` (D-ENG-16), replay of the recorded transitions on replay time with live alarms on the replay page and its two tests (D-REP-01..06, D-UI-16), report refreeze (D-DB-10), the alarm-quality metrics in the soak and the report (D-ALM-15), the tuning study (D-ALM-16 with R20), p95 with its CI from pooled samples (D-PERF-10), run P and the scaling analysis (D-PERF-05/06/08/09), the `except`-coverage gate (D-ENG-21), the remaining spec §6 documents and the ATP-01 2 h soak, so ATP-07 and ATP-08 pass and every requirement has a test or an inspection note.
**Architecture:** A new `gs-archiver` service runs one asyncio loop: a pure planner turns hot-row counts and `archive_object` rows into actions (archive, merge late rows into a new version, delete hot), each action verified by hash and by row content before any delete, then a rolling scrub re-hashes the oldest-checked objects. One window reader (verified Parquet or hot rows, typed NULLs for missing columns, late hot rows merged) feeds the replay: an async generator in the API that merges frames with the recorded `alarm_events` and `events` rows, paces by `t_onboard` and emits a replay clock so the console's separate replay store judges STALE on replay time. Recomputation with `step()` exists only as a test. Measurement code lives in `perf/` and is unit-tested like product code.
**Tech Stack:** Python 3.14, FastAPI 0.162.6, pydantic 2.14.3, psycopg 3.3.9, redis-py 8.1.0, TimescaleDB 2.32.0-pg17, MinIO `bitnamilegacy/minio:2025.5.24@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c`, pytest 9.2.0, React 19.3.1, TypeScript 6.0.3, Vitest 5.1.0, Playwright 1.65.0. New dependencies: `pyarrow==26.0.1`, `minio==7.2.20` (the versions spiked in R7/R11, D-ARC-01/08).
**Spec:** `Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md` (binding; §4.5, §5, §6, §8 gate 4) · `docs/design.md` (§2.6, D-ALM-15/16, D-DB-06/08/09/10, D-ENG-14..16, 19..21, D-PERF-01..10, D-UI-05/16, §3, §3.1, §3.2, §3.6, §3.7, §4, §5.3 Gate 4 row, §13) · `docs/requirements.md` (test names) · `docs/research/R7`, `R16`, `R20`, `R14` (E-1, E-2, E-7), `R15` (E3) · `docs/assumptions.md` (A-R16-1..3, A-R14-1/2/6/7, A-R20-1/2).

## Global Constraints

- **Base and branches.** Gate 4 integrates on `gate4-test-readiness`, created from `main` **after the Gate 3b PR is merged**. Tracks work on `gate4/<track>` in their own worktree and merge back with `git merge --no-ff`. Never commit to `main`. Push with `git push -u origin <branch>`.
- **Startable now (ruling P-pf35):** Track AR and Track AQ branch from `9a0dcef` (Gate 3b Track 0 done) as `gate4/archiver` and `gate4/quality`. They may use only what `9a0dcef` contains (hot `telemetry` / `alarm_events` / `events` tables, `archive_object` as in `02-tables.sql`, MinIO in compose, ingest, the Gate 3a engine, `tests/support/*` of Track 0). Before merging into `gate4-test-readiness` each merges `gate4-test-readiness` into itself, resolves conflicts in the shared files (`pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `scripts/init_env.py`, `tests/meta/test_compose.py`) and re-runs its gate.
- **Waits for the Gate 3b merge:** Tracks G0, RP, SR, PF, DOC and Z. Gate 3b's frozen interfaces (`interfaces/**`, `ui/src/api/schema.d.ts`, `migrations/0[0-4]*`, the REST surface of `.superpowers/sdd/2026-11-15-gate3b-console-commanding/task-3-report.md`) are never changed by Tracks AR/AQ; after the merge, Track G0 (Task 11) is the only Gate 4 task that changes generated files, and they are frozen for the parallel tracks after it.
- **Worktrees (G3, P-pf1).** Each worktree's `.env`: `COMPOSE_PROJECT_NAME=nyx4-<track>`, `GS_IMAGE_TAG=g4-<track>`, every host port (`DB_HOST_PORT REDIS_HOST_PORT MINIO_HOST_PORT SIM_HOST_PORT API_HOST_PORT`) + 100 × index (archiver 6, quality 7, contracts/replay 8, sessions 9, perf 10, docs 11, integration 12); `API_HOST_PORT` base is 8010 (paperlab holds 8000). Rebuild a stack with `docker compose down -v && make up`, **never `make reset`** (it regenerates `.env`). Never stop or `down` another project.
- **Every change ships with its test in the same commit** (D-ENG-14/15); a bug fix starts with a failing test; a real bug a test catches gets a `docs/bug-log.md` row (D-ENG-19). Test lines written as `def test_x(): ...  # comment` are specifications by comment; the implementer writes real assertions. Tests create their own data and use `tests.support.env.vid()` vehicles; `NYX-TA9` itself is used only by system tests and the ATP runs.
- **Test tiers (P-pf32).** Per task: `uv run ruff check && uv run ruff format --check && uv run mypy --strict src tests scripts perf && uv run pytest -q tests/unit <the integration/meta files the task touches>` (+ `cd ui && npx tsc --noEmit && npm test` for `ui/`; + `make gen && git diff --exit-code -- interfaces ui/src/api` when a model or route changes). Once per track before its merge: `uv run pytest --cov=gs --cov-fail-under=80` and `sh /Users/malek/try/nyx/.superpowers/syslock.sh uv run pytest -q tests/system`. Every Docker system-test or perf run on this machine goes through `syslock.sh`.
- **Ponytail.** Every implementer loads `ponytail:ponytail` before writing code; it never removes a required test, validation, data-loss handling, security measure or accessibility basic.
- **Commits:** `<type>: <description>` (`feat fix refactor docs test chore perf ci`), no `Co-Authored-By` trailer.
- **No time limit, no cuts** (design §5 scope rule): D-ARC-06 versioned late objects, report refreeze and live alarms on the replay page are built. A design value that fails is reported to the coordinator, never tuned. **Spec wins over design, design over this plan.**
- **Binding rulings:** Gate 2 R1–R30, Gate 3a G1–G31, Gate 3b P1–P35 (`.superpowers/sdd/2026-11-15-gate3b-console-commanding/progress.md`), in particular R9/R26 (`/healthz` 200 ok / 200 degraded / 503 down through `serve_health`), R16 (a new compose service or Makefile target ships a smoke check), R22 (host ports env-overridable, `127.0.0.1`), R24 (non-root image), R28 (env through `gs.common.config.require*()`, Redis retry off), P-pf8 (per-test users for login), P-pf15 (stored reports read with `model_validate_json(report::text)`), P-pf30 (CSV formula quoting), deviation 2 of task-3-report (body fields typed `UUID`/`datetime` use `Field(strict=False)`).
- **Design values (verbatim; tasks repeat the ones they use).** One Parquet object per vehicle-hour, pyarrow, **zstd, one row group**, schema from the hot table, **no custom footer metadata**; object `archive/<vehicle>/<YYYY>/<MM>/<DD>/<HH>.parquet` + `<HH>.parquet.sha256` in `sha256sum` format, v1+ `<HH>.v<N>.parquet`; **single PUT** with `x-amz-checksum-sha256`, full re-read, content check (`pyarrow.Table.equals` after sorting both by `(t_onboard, seq)`, 36 000 rows per full hour at 10 Hz), sidecar, then the `archive_object` row; **eligible = `hour_end + 10 min ≤ now`**; **hot delete only when `hour_end ≤ now − HOT_RETENTION_H`** (default **25 h**), one transaction, commits only if the deleted rows' **count, `min(t_onboard)`, `max(t_onboard)`** equal `rows`, `t_min`, `t_max`; loop **every 5 min**; scrub **K = 20** objects per loop with the oldest `last_checked_at`; `ARCHIVE_CORRUPT` key `ARCHIVE_CORRUPT/<vehicle>/<hour>`, details expected and found sha256; `/healthz` `degraded` when `oldest_unarchived_hour` is older than **72 h** or an `ARCHIVE_CORRUPT` is unresolved; MinIO unreachable → retry with backoff, never delete; `archiver` MinIO user with a bucket-scoped put/get policy, **no delete**; replay 409 **"archive object failed hash check"**, 404 no data in window; speed **1× / 10×**; STALE in replay at **2.0 s** of vehicle time; frames ordered by `(t_onboard, seq)`, an ACK row directly after the frame `(after_epoch, after_seq)` it names; backfill only into **`NYX-TA9`**, within the **7-day** validity window, oldest first; hooks `archive:after_put · archive:after_reread`; D-ALM-15: Ψ flagged at **≥ 0.05 alarms/s**, short alarms **< 20 s**, peak per **10 min**, flood from **≥ 10 in 10 min** until the rate drops **below 5**; D-ALM-16 grid **N = 1..5 × deadband 2, 4, 6, 8, 10, 12, 15 %** for cabin pressure, ALM-04 margin **≥ 5 s**; R20: `z_N` **4.50 (N = 1), 2.17 (N = 3)**, bench rule **d ≥ 2.2 σ** and **yellow distance ≥ 2.3 σ**, Xu test tolerances **0.01 absolute** on time in alarm and **5 % relative** on raise rate; D-PERF-10 ranks **⌊np − 1.96√(np(1−p))⌋ … ⌈1 + np + 1.96√(np(1−p))⌉**, two-sided CI only from **n ≥ 120** (n = 120 → ranks **109–120**; n = 60 → "n too small for a two-sided CI"); run P = **2 × 100 Hz, 5 min**, ING-10 sampler at **1 Hz**, pass = max time lag **< 1 s**; thresholds ING-09 **200 ms**, ALM-05 **300 ms**, UI-01 **1 s**, DAT-03 **300 ms**, first **10 s** excluded; scaling to **10 vehicles × 100 Hz (1000 frames/s)**; ATP-01 soak **2 h**, expected **0 false raises**; refreeze audited `REPORT_REFROZEN`, 409 `REPORT_COMPLETE`, 410 `HOT_DATA_GONE`; REPLAY purple used for nothing else.

## Review Focus

1. **A database session whose `TimeZone` is not UTC.** Gate 3b already hit this in the report (`test_report_survives_a_non_utc_session_timezone`). An archiver or replay connection in `Asia/Tokyo` must archive, count, delete and read exactly the same rows as in UTC: hour bounds are UTC instants, never local hours (Task 3 `test_hour_bounds_ignore_session_timezone`, Task 6 `test_window_ignores_session_timezone`).
2. **MinIO down for a whole pass while hours are due for deletion.** The operator expects the hot data to stay and the archive to catch up oldest-first when MinIO is back, with `/healthz` saying why (Task 5 `test_minio_down_pass_deletes_nothing`).
3. **A replay window that crosses the hot/archive seam, starts and ends inside an hour, and covers an archived hour that still holds late hot rows.** No duplicate frame, no missing frame, no frame outside `[from, to)` (Task 6 `test_seam_window_no_dup_no_gap`).
4. **A replay that starts inside an alarm acknowledged before `from` that escalates inside the window.** It must start ACKED and show the escalation as a new unacknowledged alarm, exactly as live (Task 12 `test_initial_state_acked_then_escalate`).
5. **The engineer closes the replay tab mid-run.** The generator stops, nothing keeps reading MinIO, the replay is gone from the registry (Task 13 `test_disconnect_stops_replay`).

---

## Parallel tracks

| Track | Tasks | Starts | Consumes | Produces |
| --- | --- | --- | --- | --- |
| AR – archiver | 1–7 | **now**, from `9a0dcef` | Track 0 DDL, MinIO, ingest, hooks, test helpers | `gs-archiver`, `hour_bounds`, planner, verified archive + versions, scrub, `/healthz`, window reader, backfill CLI, `NYX-TA9` in compose |
| AQ – quality and checks | 8–10 | **now**, from `9a0dcef` | Gate 3a engine | `gs.alarms.quality`, tuning study + doc, `except`-coverage checker (gate off) |
| G0 – Gate 4 contracts | 11 | after Gate 3b merge **and** AR + AQ merged | everything above | replay models and routes (stubs), refreeze route (stub), `AlarmQuality` body, `replay` MinIO user, refreeze grant, frozen generated files |
| RP – replay | 12–15 | after G0 | G0, AR (Task 6), Gate 3b hub/ACK path/console | replay core, replay API + TA9 filter, D-REP-05 tests, replay page |
| SR – sessions | 16–17 | after G0 | G0, AQ (Task 8), Gate 3b `report.py` | refreeze, alarm-quality report section |
| PF – performance | 18–21 | after G0 | Gate 3b `perf/stats.py`, `perf/run_n.py` | p95 CI, run P driver + sim rate, scaling generator, soak tooling |
| DOC – documents | 22–23 | after G0 | — | D-ENG-20 checks, trace check, README, CHANGELOG, guides, `docs/atp/` |
| Z – integration and runs | 24–27 | after RP, SR, PF, DOC merged | all | `except` gate on, replay e2e + ATP-07, measurement runs (ATP-01 soak, ATP-08), rulings recorded |

Merge order into `gate4-test-readiness`: **AR → AQ → G0**, then **SR → PF → DOC → RP** as they finish (RP last: it touches the hub and the console), then Z alone.

**File ownership** (a path not listed and not created by a task needs a ruling):

| Track | Owns |
| --- | --- |
| AR | `src/gs/archiver/**`, `src/gs/simulator/{backfill,cli}.py`, `migrations/05-gate4.sql`, `scripts/minio_init.sh`, `src/gs/common/hooks.py` (two points), `src/gs/ingest/events.py` (one Literal member), `tests/support/archive.py`, `tests/unit/{test_archive_hours,test_archive_plan,test_archiver_status,test_backfill}.py`, `tests/integration/{test_archiver,test_window_reader,test_backfill,test_minio}.py`, `tests/system/test_archiver_kill.py`; shared edits listed per task in `pyproject.toml`, `uv.lock`, `docker-compose.yml`, `.env.example`, `scripts/init_env.py`, `tests/meta/test_compose.py`, `tests/unit/test_init_env.py`, `tests/integration/test_initdb.py`, `tests/integration/test_health.py` |
| AQ | `src/gs/alarms/quality.py`, `perf/alarm_tuning.py`, `docs/perf/alarm-tuning.md`, `tests/unit/{test_alarm_quality,test_alarm_tuning}.py`, `tests/meta/{test_except_coverage.py,except_allowlist.txt,fixtures/}` |
| G0 | `src/gs/common/interfaces/replay.py`, `src/gs/api/{bodies,openapi}.py` (additions), `src/gs/api/replays.py` (stubs), `src/gs/api/sessions.py` (refreeze stub), `interfaces/**`, `ui/src/api/schema.d.ts`, `migrations/05-gate4.sql` (append), `scripts/minio_init.sh` (append), compose `api` env |
| RP | `src/gs/replay/**`, `src/gs/api/replays.py`, `src/gs/api/hub.py` (TA9 filter only), `tests/support/recompute.py`, `ui/src/{replay.ts,replay.test.ts,ReplayPage.tsx,replayPage.test.tsx}`, `ui/src/App.tsx` (one toggle), `ui/src/api.ts` (two calls), `tests/unit/test_replay_*.py`, `tests/integration/{test_replay_api,test_replay_equality,test_replay_rows}.py` |
| SR | `src/gs/api/{report,sessions}.py`, `tests/integration/test_sessions.py`, `tests/unit/test_report_csv.py` |
| PF | `perf/**` except `perf/alarm_tuning.py`, `src/gs/simulator/frames.py` + `main.py` (rate only), `tests/unit/{test_perf_stats,test_run_n,test_run_p,test_scaling,test_soak_count}.py`, compose `simulator` env (one variable) |
| DOC | `README.md`, `CHANGELOG.md`, `docs/atp/**`, `docs/img/**`, `docs/operator-guide.md`, `docs/dev-guide.md`, `scripts/check_commits.py`, `tests/meta/{test_readme_sections,test_stack,test_requirements_trace,test_guides}.py`, `tests/unit/test_check_commits.py`, `.github/workflows/ci.yml` (lint steps) |
| Z | anything after the merges; new: `e2e/replay.spec.ts`, `docs/perf/**` results, `docs/design.md` / `docs/assumptions.md` / `docs/requirements.md` rulings |

**Cross-track contract (decided now).**

| Contract | Value |
| --- | --- |
| `archive_object` | §3.2 columns plus `version int NOT NULL DEFAULT 0` (0 = `<HH>.parquet`, N ≥ 1 = `<HH>.v<N>.parquet`); one row per `(vehicle, hour)` naming the latest verified version; older version objects stay in MinIO and are never read |
| Event | `ARCHIVE_CORRUPT`, vehicle = the object's vehicle, `t_onboard` = the hour start, key `ARCHIVE_CORRUPT/<vehicle>/<iso_z(hour)>` (+ `/v<N>` for N ≥ 1), details `{hour, version, object, expected, found}` (`found` null when the object is missing) |
| `NYX-TA9` | not in `mission/` (it has no vehicle list) and not in the simulator's live `SIM_VEHICLES` or the API's `VEHICLES`; in the ingest `VEHICLES` with home `ingest-1`; only `gs-sim backfill` writes it; the API hub drops `ui:events` messages of vehicles outside its `VEHICLES` |
| Hour | `[h, h + 1 h)` in UTC, `h` aligned to the UTC hour, from `gs.archiver.hours.hour_bounds` for export, delete, counts and the window reader |

---

## Track AR – archiver (startable now)

### Task 1: Hour arithmetic and the pass planner

**Files:**
- Create: `src/gs/archiver/__init__.py`, `src/gs/archiver/hours.py`, `src/gs/archiver/plan.py`, `tests/unit/test_archive_hours.py`, `tests/unit/test_archive_plan.py`

**Interfaces:**
- Produces `gs.archiver.hours`: `HOUR = timedelta(hours=1)`, `ELIGIBLE_AFTER = timedelta(minutes=10)`; `@dataclass(frozen=True) class HourWindow(vehicle: str, start: datetime, end: datetime)` with `SQL: ClassVar[str] = "vehicle = %(vehicle)s AND t_onboard >= %(start)s AND t_onboard < %(end)s"` and `params() -> dict[str, object]`; `hour_bounds(vehicle: str, hour: datetime) -> HourWindow` (ValueError unless `hour` is tz-aware and aligned to a UTC hour); `hour_of(t: datetime) -> datetime` (UTC floor); `eligible(hour: datetime, now: datetime) -> bool`; `delete_due(hour: datetime, now: datetime, retention_h: float) -> bool`; `object_key(vehicle: str, hour: datetime, version: int = 0) -> str` (no bucket prefix; bucket is `archive`); `sidecar(key: str, sha_hex: str) -> tuple[str, bytes]` (`key + ".sha256"`, `f"{sha_hex}  {basename(key)}\n"`).
- Produces `gs.archiver.plan`: `@dataclass(frozen=True) class ArchiveRow(vehicle, hour, sha256: str, rows: int, t_min, t_max, version: int, verified_at, hot_deleted_at: datetime | None, last_checked_at: datetime | None)`; actions `Archive(vehicle, hour, version)`, `MergeLate(vehicle, hour, version)`, `DeleteHot(vehicle, hour)`, `Anomaly(vehicle, hour, detail: str)` (frozen dataclasses), `Action = Archive | MergeLate | DeleteHot | Anomaly`; `plan_pass(now: datetime, counts: Mapping[tuple[str, datetime], int], rows: Mapping[tuple[str, datetime], ArchiveRow], retention_h: float) -> list[Action]`; `oldest_unarchived(now, counts, rows) -> datetime | None`.
- Planner rules: an eligible hour with hot rows and no row → `Archive(v, h, 0)`; a row not yet hot-deleted whose hot count > `rows` → `Archive(v, h, row.version + 1)` (late frames, D-ARC-06 v1+; the new version is built from all hot rows); a row hot-deleted whose hot count > 0 → `MergeLate(v, h, row.version + 1)`; a row not hot-deleted, count == `rows`, `delete_due` → `DeleteHot`; a row not hot-deleted with count < `rows` → `Anomaly`. Archive/MergeLate first, oldest hour first, then DeleteHot oldest first.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_archive_hours.py
H = datetime(2026, 11, 14, 13, tzinfo=UTC)
def test_bounds_are_half_open_utc_hour():
    w = hour_bounds("NYX-TA9", H)
    assert (w.start, w.end) == (H, H + HOUR) and "t_onboard < %(end)s" in w.SQL
def test_bounds_reject_unaligned_and_naive(): ...   # H + 1 s, H.replace(tzinfo=None), 14:00+05:30 (not a UTC hour) → ValueError
def test_eligible_at_exactly_ten_minutes():
    assert eligible(H, H + HOUR + ELIGIBLE_AFTER) and not eligible(H, H + HOUR + ELIGIBLE_AFTER - timedelta(microseconds=1))
def test_delete_due_at_retention(): assert delete_due(H, H + HOUR + timedelta(hours=25), 25) and not delete_due(H, H + timedelta(hours=25), 25)
def test_object_keys():
    assert object_key("NYX-TA9", H) == "NYX-TA9/2026/11/14/13.parquet"
    assert object_key("NYX-TA9", H, 2) == "NYX-TA9/2026/11/14/13.v2.parquet"
    assert sidecar("NYX-TA9/2026/11/14/13.parquet", "ab" * 32) == ("NYX-TA9/2026/11/14/13.parquet.sha256", f"{'ab' * 32}  13.parquet\n".encode())

# tests/unit/test_archive_plan.py — hand-built counts/rows, now = H + 30 h
def test_new_eligible_hours_archived_oldest_first(): ...
def test_current_hour_and_ten_minute_window_not_archived(): ...
def test_late_rows_before_delete_give_next_version(): ...   # row v0 rows=36000, count 36001 → Archive(v, H, 1), no DeleteHot
def test_late_rows_after_delete_merge(): ...              # hot_deleted_at set, count 3 → MergeLate(v, H, 1)
def test_delete_only_when_due_and_counts_equal(): ...
def test_missing_hot_rows_is_anomaly(): ...               # count 35999 < rows → Anomaly, never DeleteHot
def test_oldest_unarchived(): ...                         # min eligible hour without a row; None when all archived
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_archive_hours.py tests/unit/test_archive_plan.py -q` → `ModuleNotFoundError: gs.archiver`.
- [ ] **Step 3: Implement** the names in Interfaces (stdlib only).
- [ ] **Step 4: Run the task gate** (Global Constraints, test tiers) → green.
- [ ] **Step 5: Commit** `git commit -m "feat: add archive hour bounds, object keys and the archiver pass planner"`

---

### Task 2: Least-privilege MinIO user for the archiver

**Files:**
- Create: `scripts/minio_init.sh`
- Modify: `docker-compose.yml` (`minio-init` runs the mounted script; env `S3_ARCHIVER_SECRET`), `.env.example` (`S3_ARCHIVER_SECRET=`), `scripts/init_env.py`, `tests/unit/test_init_env.py`, `tests/integration/test_minio.py`, `tests/support/s3.py`

**Interfaces:**
- `scripts/minio_init.sh` (bash, `set -euo pipefail`, run by `minio-init` from the same MinIO image, `MC_CONFIG_DIR=/tmp/mc`): keeps the existing 30 × 1 s retry for `mc alias set`, then `mc mb --ignore-existing s3/archive`, `mc admin user add s3 archiver "$S3_ARCHIVER_SECRET"`, writes policy `nyx-archiver` (`s3:PutObject`, `s3:GetObject` on `arn:aws:s3:::archive/*`; `s3:ListBucket` on `arn:aws:s3:::archive`; nothing else), `mc admin policy create`, and attaches it only if `mc admin policy entities s3 --user archiver` does not list it (idempotent, D-ARC-09, R2-19).
- `scripts/init_env.py`: on an existing `.env`, appends every key of `.env.example` that is missing (secret = `secrets.token_hex(24)`), never rewrites an existing line (so worktrees get `S3_ARCHIVER_SECRET` without `make reset`).
- `tests/support/s3.py` adds `s3_client(user: Literal["root", "archiver"] = "archiver") -> Minio` (host port `MINIO_HOST_PORT`, creds from `.env`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_init_env.py (add)
def test_existing_env_gains_missing_keys_only(tmp_path):
    ...  # .env with POSTGRES_PASSWORD=keep; example has S3_ARCHIVER_SECRET= → appended (48 hex), POSTGRES_PASSWORD still "keep", order of old lines unchanged

# tests/integration/test_minio.py (add; the existing idempotency test stays)
def test_archiver_can_put_and_get():
    c = s3_client("archiver"); key = f"t/{vid()}.bin"
    c.put_object("archive", key, io.BytesIO(b"x"), 1); assert c.get_object("archive", key).read() == b"x"
def test_archiver_cannot_delete(): ...       # remove_object → S3Error code AccessDenied; object still there (root get)
def test_archiver_cannot_use_another_bucket(): ...  # root makes bucket t-<vid>; archiver put → AccessDenied
def test_init_rerun_keeps_policy(): ...      # docker compose run --rm minio-init twice → exit 0, archiver still denied delete
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_init_env.py tests/integration/test_minio.py -q` → missing key / unknown user.
- [ ] **Step 3: Implement**; `make env && docker compose run --rm minio-init`.
- [ ] **Step 4: Run the task gate** plus `uv run pytest tests/meta/test_compose.py -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: give the archiver a bucket-scoped MinIO user without delete"`

---

### Task 3: Archive one hour, verified by bytes and by rows

**Files:**
- Create: `migrations/05-gate4.sql`, `src/gs/archiver/s3.py`, `src/gs/archiver/parquet.py`, `src/gs/archiver/job.py`, `tests/support/archive.py`, `tests/integration/test_archiver.py`
- Modify: `pyproject.toml` + `uv.lock` (`pyarrow==26.0.1`, `minio==7.2.20`; `gs-archiver = "gs.archiver.main:main"` added in Task 5), `src/gs/common/hooks.py` (`POINTS` gains `archive:after_put`, `archive:after_reread`), `tests/unit/test_hooks.py`, `tests/integration/test_initdb.py`

**Interfaces:**
- `migrations/05-gate4.sql`: `SET ROLE gs_owner; ALTER TABLE archive_object ADD COLUMN t_min timestamptz NOT NULL, ADD COLUMN t_max timestamptz NOT NULL, ADD COLUMN last_checked_at timestamptz, ADD COLUMN version int NOT NULL DEFAULT 0, ADD CHECK (t_min <= t_max), ADD CHECK (version >= 0);` (the table is empty at initdb; the result equals §3.2 plus `version`). Stacks: `docker compose down -v && make up`.
- `gs.archiver.s3`: `@dataclass(frozen=True) class S3Settings(endpoint: str, access_key: str, secret_key: str, bucket: str)` with `from_env()` (`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, via `require`); `client(s) -> Minio` (`secure=False`); `async def put(c: Minio, bucket: str, key: str, data: bytes) -> str` — sha256 in memory, header `x-amz-checksum-sha256` = base64 of the digest through `metadata=`, single PUT, returns the hex digest, run via `asyncio.to_thread`; `async def get(c, bucket, key) -> bytes`; `S3_ERRORS: tuple[type[BaseException], ...]` (`S3Error`, `urllib3` connection errors, `OSError`).
- `gs.archiver.parquet`: `PG_TO_ARROW = {"timestamp with time zone": pa.timestamp("us", tz="UTC"), "text": pa.string(), "bigint": pa.int64(), "uuid": pa.string(), "double precision": pa.float64()}`; `async def hot_schema(conn) -> pa.Schema` (telemetry columns in `information_schema` order); `async def hot_table(conn, w: HourWindow, schema: pa.Schema) -> pa.Table` (rows of `w`, sorted `(t_onboard, seq)`); `encode(t: pa.Table) -> bytes` (`compression="zstd"`, `row_group_size=len(t)` so one row group, no extra metadata); `decode(b: bytes) -> pa.Table`; `sort_rows(t) -> pa.Table`; `same_rows(a, b) -> bool` (`sort_rows(a).equals(sort_rows(b))`).
- `gs.archiver.job`: `@dataclass class ArchiverCtx(s3: Minio, bucket: str, hooks: Hooks, retention_h: float, refused: set[tuple[str, datetime]] = field(default_factory=set))`; `async def archive_hour(ctx, conn, vehicle: str, hour: datetime, version: int = 0) -> ArchiveRow | None` — order: read hot rows (None if empty) → `encode` → `put` → `hooks.fire("archive:after_put")` → `get` + sha256 equal → `decode` + `same_rows` against a **second** hot read of the same window → `hooks.fire("archive:after_reread")` → sidecar `put` → upsert the `archive_object` row (`INSERT … ON CONFLICT (vehicle, hour) DO UPDATE` setting version, sha256, rows, t_min, t_max, verified_at, `last_checked_at = NULL`) and return it. Any mismatch → `log.error` with `extra={"vehicle": …}`, no row written, returns None. The archiver connection runs `SET TIME ZONE 'UTC'`.
- `tests/support/archive.py`: `hot_hour(vehicle: str, hour: datetime, n: int = 36_000, rate_hz: float = 10.0, values: Mapping[str, float] | None = None) -> None` (COPY into `telemetry` as `gs_app`, seq 0..n−1 at `hour + i / rate_hz`); `archive_rows(vehicle) -> list[dict]`; `archiver_ctx(**overrides) -> ArchiverCtx` (archiver creds, `NO_HOOKS`); `stored_in(vehicle: str, hour: datetime) -> int`; `app_conn() -> AbstractAsyncContextManager[psycopg.AsyncConnection]` (gs_app, autocommit); `next_ta9_window(hours: int) -> tuple[datetime, datetime]` and `backfill_ta9(t_from, t_to, *args: str) -> None` (added in Task 7).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_archiver.py
H = hour_of(datetime.now(UTC)) - timedelta(hours=30)
async def test_hour_archived_with_sidecar():
    v = vid(); hot_hour(v, H)
    async with app_conn() as c: row = await archive_hour(archiver_ctx(), c, v, H)
    assert row is not None and row.rows == 36_000 and row.version == 0
    data = s3_client("root").get_object("archive", object_key(v, H)).read()
    assert hashlib.sha256(data).hexdigest() == row.sha256 == archive_rows(v)[0]["sha256"]
    side = s3_client("root").get_object("archive", object_key(v, H) + ".sha256").read()
    assert side == f"{row.sha256}  {H:%H}.parquet\n".encode()
    meta = pq.ParquetFile(io.BytesIO(data)).metadata
    assert meta.num_row_groups == 1 and meta.row_group(0).column(0).compression == "ZSTD"
    assert set((meta.metadata or {})) <= {b"ARROW:schema"}   # no custom footer metadata (D-ARC-01)
async def test_verify_compares_content(monkeypatch):
    ...  # patch encode() to change one value in the table before writing → no archive_object row, hot rows all kept, error logged with the vehicle
async def test_t_min_t_max_stored(): ...          # equal to min/max t_onboard of the archived rows
async def test_hour_bounds_ignore_session_timezone(): ...  # conn SET TIME ZONE 'Asia/Tokyo' before archive_hour → same rows, t_min/t_max, sha256 as a UTC run on a copy
async def test_crash_after_put_leaves_no_row(tmp_path): ...  # Hooks(crash_at="archive:after_put", exit=raise SystemExit) → SystemExit, no row; a second archive_hour writes the row (rebuild + re-upload)
async def test_crash_after_reread_leaves_no_row(tmp_path): ...  # same for archive:after_reread
async def test_empty_hour_returns_none(): ...

# tests/integration/test_initdb.py (add)
def test_archive_object_has_range_scrub_and_version_columns(): ...  # throwaway db: t_min/t_max NOT NULL timestamptz, last_checked_at, version default 0, CHECK t_min <= t_max rejects a bad insert

# tests/unit/test_hooks.py (add)
def test_archive_points_accepted(): ...   # CRASH_AT=archive:after_put valid; an unknown archive:x rejected
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/integration/test_archiver.py tests/integration/test_initdb.py tests/unit/test_hooks.py -q` → import error / missing columns.
- [ ] **Step 3: Implement**; `uv add pyarrow==26.0.1 minio==7.2.20`; `docker compose down -v && make up`.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: archive a vehicle-hour to MinIO, verified by hash and by row content"`

---

### Task 4: Hot delete with count and range check; versioned late objects

**Files:**
- Modify: `src/gs/archiver/job.py`, `tests/integration/test_archiver.py`

**Interfaces:**
- Consumes `ArchiveRow`, `HourWindow`, `archive_hour`.
- Produces `async def delete_hot(ctx, conn, row: ArchiveRow, w: HourWindow) -> bool` — one transaction: `WITH d AS (DELETE FROM telemetry WHERE <w.SQL> RETURNING t_onboard) SELECT count(*), min(t_onboard), max(t_onboard) FROM d`; commits with `UPDATE archive_object SET hot_deleted_at = clock_timestamp()` only if the three equal `row.rows`, `row.t_min`, `row.t_max`; else rollback, `log.error(..., extra={"vehicle"})`, add `(vehicle, hour)` to `ctx.refused`, return False. A later successful delete of that hour removes it from `ctx.refused`.
- Produces `async def merge_late(ctx, conn, row: ArchiveRow) -> ArchiveRow | None` (D-ARC-06 v1+, hot rows already deleted): `prev` = `decode(get(row.version key))` with its sha256 checked against `row.sha256` (mismatch → None, the late rows stay hot); `late` = hot rows of the hour; `expected` = `pa.concat_tables([prev, late], promote_options="default")` deduplicated by `(vehicle, t_onboard, seq)`, sorted; PUT `object_key(v, h, row.version + 1)`, re-read, `same_rows(decoded, expected)`, sidecar; then **one transaction**: update the row to the new version (sha256, rows, t_min, t_max, verified_at, `hot_deleted_at = clock_timestamp()`, `last_checked_at = NULL`) and delete the hour's hot rows with the count/min/max check against `late`; any mismatch rolls back both (the new object stays unreferenced; the next pass builds version + 1 again).
- `Archive(v, h, N ≥ 1)` reuses `archive_hour(…, version=N)` (all hot rows are a superset of the old version).

- [ ] **Step 1: Write the failing tests**

```python
async def test_delete_only_after_verify(): ...   # HOT_RETENTION_H=0 ctx: hour with no row → plan gives no DeleteHot and nothing deleted; after archive_hour → delete_hot deletes 36 000, hot_deleted_at set
async def test_row_count_mismatch_rolls_back(): ...   # archive H, insert one more row inside H → delete_hot False, 36 001 rows still hot, (v, H) in ctx.refused
async def test_delete_wrong_hour_refused():
    v = vid(); hot_hour(v, H); hot_hour(v, H + HOUR)
    row = await archive_hour(ctx, c, v, H)
    assert not await delete_hot(ctx, c, row, hour_bounds(v, H + HOUR))   # both 36 000 rows: count equal, range not
    assert stored_in(v, H + HOUR) == 36_000 and (v, H) in ctx.refused
async def test_late_frame_before_delete_gives_v1(): ...   # archive H, add a late row, archive_hour(version=1) → row.version 1, object 13.v1.parquet holds 36 001 rows, v0 object still present; delete_hot then succeeds
async def test_late_frame_after_delete_merged(): ...      # archive + delete H, insert 2 late rows → merge_late → v1 = 36 002 rows, late rows deleted, hot empty
async def test_merge_refused_when_prev_corrupt(): ...     # root overwrites the v0 object → merge_late None, late rows still hot
async def test_refused_cleared_by_later_delete(): ...
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/integration/test_archiver.py -q` → `delete_hot` / `merge_late` undefined.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: delete hot rows only with matching count and time range, and version late frames into new objects"`

---

### Task 5: Rolling scrub, `/healthz` and the archiver service

**Files:**
- Create: `src/gs/archiver/scrub.py`, `src/gs/archiver/status.py`, `src/gs/archiver/main.py`, `tests/unit/test_archiver_status.py`
- Modify: `src/gs/ingest/events.py` (`EventKind` gains `"ARCHIVE_CORRUPT"`; one-line commit of its own, like P-pf31), `pyproject.toml` (`gs-archiver` script), `docker-compose.yml` (service `archiver`), `.env.example` (comments for `HOT_RETENTION_H`, `ARCHIVE_LOOP_S`), `tests/meta/test_compose.py`, `tests/integration/test_archiver.py`, `tests/integration/test_health.py`

**Interfaces:**
- `gs.archiver.scrub`: `SCRUB_K = 20`; `corrupt_key(vehicle: str, hour: datetime, version: int) -> str`; `async def scrub_pass(ctx, conn, k: int = SCRUB_K) -> int` (objects checked) — selects `k` rows `ORDER BY last_checked_at NULLS FIRST, hour`, re-reads and re-hashes each current-version object, sets `last_checked_at = clock_timestamp()`, inserts an `ARCHIVE_CORRUPT` event (contract table) through `insert_events` on a mismatch or a missing object (idempotent by key); S3 unreachable → raise, `last_checked_at` untouched. `async def unresolved_corrupt(conn) -> int` (events whose `details->>'expected'` equals the row's current `sha256`).
- `gs.archiver.status`: `BACKLOG_H = 72`; `archiver_status(now: datetime, db_ok: bool, redis_ok: bool, s3_ok: bool, oldest_unarchived: datetime | None, corrupt: int, refused: int, last_pass: datetime | None, backlog_h: float = BACKLOG_H) -> tuple[State, dict[str, object]]` — `down` if the DB is unreachable; `degraded` if Redis or MinIO is unreachable, `oldest_unarchived` is older than `backlog_h`, `corrupt > 0` or `refused > 0`; checks carry `oldest_unarchived_hour` (`iso_z` or None), `scrub` (`"ok"` or `"<n> corrupt"`), `delete_refused`, `db`, `redis`, `minio`, `last_pass`.
- `gs.archiver.main`: `@dataclass(frozen=True) class ArchiverSettings(database_url, redis_url, s3: S3Settings, retention_h: float = 25.0, loop_s: float = 300.0, bind_host, health_port)` with `from_env()` (`HOT_RETENTION_H`, `ARCHIVE_LOOP_S` optional, the rest required); `async def run_pass(ctx, conn) -> PassResult` (counts per `(vehicle, date_bin('1 hour', t_onboard, TIMESTAMPTZ 'epoch'))` in one query — `ponytail:` full-hot-table count per pass, upgrade to per-chunk counts if the hot table outgrows 24 h × 2 × 100 Hz — then `plan_pass`, then actions; an `S3_ERRORS` exception stops the pass before any further action, **no delete runs in a pass that saw an S3 error**; then `scrub_pass`); `async def run(s: ArchiverSettings, stop: asyncio.Event) -> None` (loop every `loop_s`; after a failed pass back off 1, 2, 4 … capped at `loop_s`; Redis checked with `PING` as the `health` user, retry off; `serve_health` with `archiver_status`); `main()` (`setup_logging("gs-archiver")`).
- Compose `archiver`: `<<: *app`, `command: ["gs-archiver"]`, env `DATABASE_URL` (gs_app), `REDIS_URL: "redis://health:${REDIS_HEALTH_PW}@redis:6379/0"`, `S3_ENDPOINT: minio:9000`, `S3_ACCESS_KEY: archiver`, `S3_SECRET_KEY: "${S3_ARCHIVER_SECRET}"`, `S3_BUCKET: archive`, `HOT_RETENTION_H: "${HOT_RETENTION_H:-25}"`, `ARCHIVE_LOOP_S: "${ARCHIVE_LOOP_S:-300}"`, `BIND_HOST: "0.0.0.0"`, `HEALTH_PORT: "8081"`, `GS_TEST_HOOKS: "${GS_TEST_HOOKS:-0}"`, `CRASH_AT: "${ARCHIVER_CRASH_AT:-}"`, `STOP_AT: "${ARCHIVER_STOP_AT:-}"`; depends on db, redis healthy, `migrate` and `minio-init` completed; python healthcheck like the others; no host port.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_archiver_status.py
def test_ok(): ...
def test_backlog_older_than_72h_degraded(): ...   # exactly 72 h ok, 72 h + 1 s degraded
def test_corrupt_or_refused_degraded(): ...
def test_db_down_is_down_s3_down_degraded(): ...

# tests/integration/test_archiver.py (add)
async def test_scrub_detects_corrupt_object():
    ...  # archive H; root rewrites the object with one byte flipped → scrub_pass writes ARCHIVE_CORRUPT/<v>/<iso_z(H)> with expected = row sha256, found = new sha256; unresolved_corrupt == 1; status degraded; window reader refusal is asserted in Task 6
async def test_scrub_oldest_checked_first(): ...   # 25 rows, k=20 → the 20 with NULL/oldest last_checked_at get it set
async def test_new_version_resolves_corrupt(): ...  # corrupt v0 while hot rows exist + late row → Archive v1 → unresolved_corrupt == 0
async def test_minio_down_pass_deletes_nothing(): ...  # ctx with an unreachable endpoint, a due hour already verified → run_pass returns failed, hour still hot, no new row; with the real endpoint the next pass archives the oldest eligible hour first
async def test_run_pass_end_to_end(): ...   # two eligible hours, retention 0 → both archived oldest first, deleted, scrubbed

# tests/integration/test_health.py (add)
def test_archiver_healthz_degraded_on_archive_corrupt(): ...   # running compose archiver: insert a matching ARCHIVE_CORRUPT event for an archived row → /healthz 200 degraded, checks.scrub "1 corrupt"
# tests/meta/test_compose.py: ENV["archiver"] as in Interfaces; archiver has no host port; depends_on minio-init completed
```

- [ ] **Step 2: Run to verify they fail** → import errors / unknown service.
- [ ] **Step 3: Implement**; `make up` starts `archiver`; smoke: `docker compose ps archiver` healthy (R16).
- [ ] **Step 4: Run the task gate** plus `tests/integration/test_health.py tests/meta -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: run the archiver loop with rolling scrub, ARCHIVE_CORRUPT and healthz"` (and the separate `chore: add the ARCHIVE_CORRUPT event kind` for `events.py`)

---

### Task 6: Window reader (replay read side)

**Files:**
- Create: `src/gs/archiver/window.py`, `tests/integration/test_window_reader.py`

**Interfaces:**
- `class ArchiveCorrupt(Exception)(vehicle: str, hour: datetime, expected: str, found: str | None)`; message "archive object failed hash check".
- `BASE_COLUMNS = ("vehicle", "t_onboard", "seq", "t_received", "mode", "last_cmd_id")`.
- `async def window_hours(conn, s3: Minio, bucket: str, vehicle: str, t_from: datetime, t_to: datetime, params: Sequence[str]) -> AsyncIterator[pa.Table]` — one table per UTC hour touching `[t_from, t_to)`, oldest first: an hour with an `archive_object` row → its current-version object, sha256 checked against the row (`ArchiveCorrupt` on mismatch or missing), plus any hot rows of that hour, deduplicated by `(vehicle, t_onboard, seq)` (EX-29); an hour without a row → hot rows; each projected onto `BASE_COLUMNS + params` with a missing parameter filled by typed NULLs (`float64`, the registry type, EX-19); rows outside `[t_from, t_to)` dropped; sorted `(t_onboard, seq)`; an hour with no rows yields nothing. D-EXT-10's later `read_window` is `pa.concat_tables` over this.
- `async def verify_window(conn, s3, bucket, vehicle, t_from, t_to) -> None` (hash-checks every archived hour of the window, raises `ArchiveCorrupt`); `async def has_data(conn, vehicle, t_from, t_to) -> bool` (hot row or `archive_object` row overlapping the window).

- [ ] **Step 1: Write the failing tests**

```python
async def test_window_from_parquet_equals_hot(): ...     # same window read before and after archive+delete → identical tables
async def test_seam_window_no_dup_no_gap(): ...          # H archived (hot deleted) + 2 late hot rows, H+1 hot only; window H:30..H+1:15 → seq gapless 18 000..(+), no duplicate key, no row outside the window, late rows present once
async def test_window_across_added_parameter(): ...      # archive H with the base parameters, then add a column to a throwaway copy of the telemetry schema path used by test_add_parameter → H's column is all-null float64
async def test_corrupt_hour_raises():                    # Task 5's scenario: window_hours and verify_window raise ArchiveCorrupt(expected, found)
    ...
async def test_window_ignores_session_timezone(): ...    # conn in Asia/Tokyo → same table
async def test_has_data(): ...                           # empty window False; archived-and-deleted hour True
```

- [ ] **Step 2: Run to verify they fail** → import error.
- [ ] **Step 3: Implement** with `parquet.hot_table`, `parquet.decode`, `s3.get`.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: read a replay window from verified Parquet and hot rows with typed nulls and late-row merge"`

---

### Task 7: Backfill into `NYX-TA9` and the kill-mid-upload system test

**Files:**
- Create: `src/gs/simulator/backfill.py`, `src/gs/simulator/cli.py`, `tests/unit/test_backfill.py`, `tests/integration/test_backfill.py`, `tests/system/test_archiver_kill.py`
- Modify: `pyproject.toml` (`gs-sim = "gs.simulator.cli:main"`), `docker-compose.yml` (ingest `VEHICLES: "NYX-TA1,NYX-TA2,NYX-TA9"`, `INGEST_HOMES: "ingest-1=NYX-TA1,ingest-2=NYX-TA2,ingest-1=NYX-TA9"`; `parse_homes` only rejects a vehicle named twice), `tests/meta/test_compose.py`, `tests/support/archive.py` (`backfill_ta9(...)` runs `docker compose run --rm simulator gs-sim backfill …`; `next_ta9_window`)

**Interfaces:**
- Consumes `tick_loop(vehicle, buf, rng, stop, monotonic=, sleep=, now=, faults=)` (keywords only — Gate 3b Track A may add parameters), `FrameBuffer`, `BUFFER_MAX`, `send_loop`, `xadd_tm`, `FaultBoard`, `FaultRequest`, `PERIOD_S`.
- `gs.simulator.backfill`: `BACKFILL_VEHICLE = "NYX-TA9"`; `HIGH_WATER = BUFFER_MAX // 2`; `VALIDITY_MARGIN = timedelta(hours=1)`; `@dataclass(frozen=True) class FaultAt(offset_s: float, request: FaultRequest)`; `parse_fault(spec: str, vehicle: str) -> FaultAt` (`name@offset_s[:<json params>]`, e.g. `dropout@1200:{"duration_s":10}`); `async def backfill(r: redis.asyncio.Redis, vehicle: str, t_from: datetime, t_to: datetime, seed: str, faults: Sequence[FaultAt] = (), pause_at: Sequence[int] = (), resume: Callable[[int], Awaitable[None]] | None = None) -> int` — validates `now − 7 d + VALIDITY_MARGIN ≤ t_from < t_to ≤ now` (ValueError); runs `tick_loop` on a **virtual clock** (monotonic = ticks × `PERIOD_S`, `now` = `t_from` + that, `sleep` advances it, adds due faults to the board, waits while `len(buf) ≥ HIGH_WATER`, and before tick `n ∈ pause_at` waits for the buffer to drain then `await resume(n)`); stops ticking at `t_to`, drains the buffer through `send_loop` + `xadd_tm`, returns frames sent. Same `SIM_SEED` → same frames.
- `backfill.main(argv: Sequence[str]) -> int`: `--vehicle` (must equal `BACKFILL_VEHICLE`, else exit 2 with "backfill writes only NYX-TA9 (D-ENG-16)"), `--from`/`--to` (UTC `Z`), `--fault` (repeatable), `--pause-at-seq` (repeatable; prints `paused at seq <n>: press Enter to continue` and reads one line); `REDIS_URL`, `SIM_SEED` from env.
- `gs.simulator.cli.main() -> None`: `argv[1] == "backfill"` → `raise SystemExit(backfill.main(argv[2:]))`, else `gs.simulator.main.main()`.
- `tests/support/archive.py`: `next_ta9_window(hours: int) -> tuple[datetime, datetime]` — starts at `max(now − 6 d, last stored NYX-TA9 hour + 1 h)`, fails with "NYX-TA9 backfill window exhausted: docker compose down -v && make up" when the end would pass `now − 1 h` (a backfill behind earlier TA9 frames would be DUP_OR_LATE, R2-14).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_backfill.py
def test_parse_fault(): ...                 # "leak@600" and 'dropout@1200:{"duration_s":10}' → FaultAt; bad JSON / unknown fault → ValueError
def test_cli_rejects_other_vehicles(capsys): assert backfill_main(["--vehicle", "NYX-TA1", "--from", F, "--to", T]) == 2
def test_window_outside_validity_rejected(): ...   # t_from older than now − 7 d + 1 h, or t_to in the future → ValueError
async def test_virtual_clock_grid_and_seed(): ...  # fake xadd collecting entries: 10 Hz grid from t_from exactly, seq 0.., same seed → same frames
async def test_pause_waits_for_resume(): ...       # pause_at=[50]: resume called once with 50 after 50 frames were sent, none sent after until it returns
async def test_back_pressure_never_overflows(): ...  # slow fake xadd → buf.overflow == 0

# tests/integration/test_backfill.py — in-process worker on a vid() vehicle (library allows any vehicle; the CLI enforces NYX-TA9)
async def test_backfilled_hour_stored_and_new_to_engine(): ...   # 1 h with leak@600 → 36 000 rows stored, a RAISE recorded, no DUP_OR_LATE
async def test_backfilled_hour_archives_at_once(): ...           # run_pass right after → archive_object row (past hours eligible at once, D-ARC-05)

# tests/system/test_archiver_kill.py — compose stack, NYX-TA9, ARCHIVE_LOOP_S=2, GS_TEST_HOOKS=1
@pytest.mark.parametrize("point", ["archive:after_put", "archive:after_reread"])
def test_kill_mid_upload_then_resume(point):
    ...  # backfill_ta9(next_ta9_window(1)); recreate archiver with ARCHIVER_CRASH_AT=<point> → archiver exits (restart count +1), no archive_object row for the hour while it is down; after restart the hour has a row, the object hash equals the row, the hot rows are intact (HOT_RETENTION_H=25)
def test_backfill_cli_in_compose(): ...   # `docker compose run --rm simulator gs-sim backfill --vehicle NYX-TA9 …` exit 0; --vehicle NYX-TA1 exit 2
```

- [ ] **Step 2: Run to verify they fail** → import errors.
- [ ] **Step 3: Implement**; `make up`.
- [ ] **Step 4: Run the task gate**, then the **track merge gate**: full suite with coverage and `sh /Users/malek/try/nyx/.superpowers/syslock.sh uv run pytest -q tests/system`.
- [ ] **Step 5: Commit** `git commit -m "feat: backfill past hours into NYX-TA9 through the stream path and prove a killed upload loses nothing"`

---

## Track AQ – quality and checks (startable now)

### Task 8: Alarm-quality metrics (D-ALM-15)

**Files:**
- Create: `src/gs/alarms/quality.py`, `tests/unit/test_alarm_quality.py`

**Interfaces:**
- `@dataclass(frozen=True) class QEvent(vehicle: str, param: str, kind: TransitionKind, t: datetime, alarm_id: UUID)` (one recorded `alarm_events` row; `t` = `trigger_t_onboard`, or `processed_at` for an ACK).
- `@dataclass(frozen=True) class FaultWindow(vehicle: str, fault: str, start: datetime, end: datetime, params: frozenset[str])`; `FAULT_PARAMS = {"leak": frozenset({"cabin_pressure_kpa"}), "overheat": frozenset({"cabin_temp_c"})}`; `step` uses its own `param`.
- `@dataclass(frozen=True) class Quality(raises: int, false_raises: int | None, psi: Mapping[str, float], chattering: tuple[str, ...], short_t1: int, short_t0: int, peak_10min: Mapping[str, int], time_in_flood_s: float)`; `PSI_FLAG = 0.05`, `SHORT_S = 20.0`, `WINDOW = timedelta(minutes=10)`, `FLOOD_START = 10`, `FLOOD_END = 5`.
- `quality(events: Sequence[QEvent], fault_windows: Sequence[FaultWindow] | None) -> Quality`:
  - transitions inside a `step` window on its parameter are dropped from every metric (D-PERF-04 alarms are 1 s on purpose);
  - `raises` = RAISE count; `false_raises` = RAISEs on a `(vehicle, param)` no window covers at `t` (inclusive), `None` when `fault_windows is None`;
  - `psi[f"{vehicle}/{param}"]` = Σ_r P(r)/r over run lengths r = seconds between consecutive RAISEs of that parameter (Kondaveeti 2010 p. 3), 0 for fewer than 2 RAISEs; `chattering` = keys with Ψ ≥ `PSI_FLAG` **or** any short alarm (Rule 3a);
  - `short_t1` = alarms whose RAISE → first RTN/CLEAR (condition off) is < `SHORT_S`; `short_t0` = condition-off → next RAISE of the same parameter < `SHORT_S`;
  - `peak_10min` = max RAISEs in any 10 min window, per vehicle and under `"all"`; `time_in_flood_s` = combined time from the RAISE that makes ≥ 10 in the trailing 10 min until the trailing count drops below 5.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_alarm_quality.py`, hand-built lists)

```python
def test_psi_zero_below_two_alarms(): ...
def test_wang_chen_example_low_psi_but_rule_3a():   # 8 RAISEs 116 s apart, each cleared after 5 s
    q = quality(evs, None)
    assert q.psi["NYX-TA1/cabin_temp_c"] == pytest.approx(1 / 116) and q.psi["NYX-TA1/cabin_temp_c"] < 0.05
    assert q.short_t1 == 8 and q.chattering == ("NYX-TA1/cabin_temp_c",)
def test_psi_flags_fast_chatter(): ...        # RAISEs every 10 s → Ψ = 0.1 ≥ 0.05
def test_flood_starts_at_ten_ends_below_five(): ...   # 10 RAISEs in 9 min → flood from the 10th; ends when the trailing 10 min count is 4
def test_peak_per_vehicle_and_combined(): ...
def test_step_alarms_excluded(): ...
def test_false_raises_only_with_a_fault_log(): ...  # None without windows; RAISE inside leak window not false, outside false; ACK never counts
```

- [ ] **Step 2: Run to verify they fail** → import error.
- [ ] **Step 3: Implement** (stdlib).
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: add alarm-quality metrics: false raises, chatter index, short alarms, peak rate and flood time"`

---

### Task 9: Deadband / persistence tuning study (D-ALM-16 with R20)

**Files:**
- Create: `perf/alarm_tuning.py`, `tests/unit/test_alarm_tuning.py`, `docs/perf/alarm-tuning.md` (generated by the script, committed)

**Interfaces:**
- Consumes `gs.alarms.engine` (`CompiledSet`, `initial_state`, `step`), `CHECKS`, `LimitCheck`, `RocCheck`, `gs.alarms.quality`, `gs.simulator.nominals.NOMINALS`, `gs.simulator.faults.LEAK_KPA_PER_S`, `OVERHEAT_C_PER_S`. The study builds `CompiledSet`s directly, so grid cells below the D-ALM-02 floor are measured, never loaded (`ponytail:` comment says why).
- `GRID_N = (1, 2, 3, 4, 5)`; `GRID_DEADBAND_PCT = (2, 4, 6, 8, 10, 12, 15)` (cabin pressure); `GRID_ROC_LIMIT = (0.05, 0.10, 0.15, 0.20, 0.25)` kPa/s (shipped 0.15, ruling); `NOMINAL_HOURS = 8`; `ISOLATE_RATE = 0.5` kPa/s.
- `grid() -> list[tuple[str, int, float]]` (`("deadband", n, pct)` then `("roc", n, limit)`).
- `stationary(n: int, p: float, iters: int = 3000) -> list[float]` and `far_and_raise_rate(n: int, p: float) -> tuple[float, float]` (R20 code, verbatim semantics).
- `z_n(n: int, events_per_shift: float = 1.0, samples: int = 288_000) -> float` (= −Φ⁻¹((1/samples)^(1/n)) via `statistics.NormalDist`).
- `margins(param: str, n: int = 3) -> Margin` (`nuisance_sigma`, `chatter_sigma`, `deadband`, both also × sim σ) — analytic for limit checks (R20 F2).
- `run_cell(kind, n, value, seed: str) -> Cell` (`false_per_h`, `psi`, `leak_delay_s`, `overheat_delay_s`, `roc_margin_s` (ROC RAISE before yellow), `rtn_after_isolate_s`, `spike_rejected: bool`) — Monte Carlo only where Xu's i.i.d. assumption fails (ROC and the fault profiles), seeded.
- `study(seed: str) -> tuple[list[Cell], dict[str, Margin]]` (every grid cell + the margin table); `frontier(points: Sequence[tuple[float, float]]) -> list[int]` (indices not dominated on (false rate, delay), both minimised).
- `render(cells, margins) -> str` (markdown: grid table, R20 margin table for every parameter, frontier, one ROC-style text plot per parameter, the "simulator noise only; re-run on bench noise before the first campaign (§3.5 ICD-B-06; d ≥ 2.2 σ and yellow distance ≥ 2.3 σ; autocorrelation check, Wang 2019)" note, and the acceptance lines).
- `main() -> None` writes `docs/perf/alarm-tuning.md`. Acceptance written by the script (R20 F2 replaces the flat-frontier rule, the grid is still run as specified): N = 3 / 15 % — ALM-03 spike rejected, ALM-04 margin ≥ 5 s, on-delay inside the ALM-05 budget, and its noise margins from the chain; any grid value that dominates N = 3 / 15 % is listed and raises an assumption entry (`A-R20-3`, written in Task 27), never a silent change.

- [ ] **Step 1: Write the failing tests**

```python
XU = [(1, 0.2, 0.2000, 0.16000), (3, 0.2, 0.0298, 0.00626), (3, 0.5, 0.5000, 0.03571), (5, 0.4, 0.1554, 0.00524)]
@pytest.mark.parametrize("n,p,far,rate", XU)
def test_chain_matches_xu_table(n, p, far, rate):
    f, r = far_and_raise_rate(n, p); assert f == pytest.approx(far, abs=0.001) and r == pytest.approx(rate, rel=0.01)
@pytest.mark.parametrize("n,p,far,rate", XU)
def test_engine_matches_xu_chain(n, p, far, rate): ...   # 400 000 Bernoulli(p) samples through the real engine (deadband → 0): time in alarm within 0.01 absolute, raises per sample within 5 % relative
def test_grid_enumerated_as_specified(): assert len(grid()) == 5 * 7 + 5 * 5 and grid()[0] == ("deadband", 1, 2)
def test_z_n_values(): assert z_n(1) == pytest.approx(4.50, abs=0.01) and z_n(3) == pytest.approx(2.17, abs=0.01)
def test_bus_current_chatter_margin(): assert margins("bus_current_a").chatter_sigma == pytest.approx(0.092, abs=0.002)
def test_same_seed_same_table(): assert render(*study("1")) == render(*study("1"))
def test_frontier_on_hand_built_points(): assert frontier([(0, 3), (0, 2), (1, 1), (2, 2)]) == [1, 2]
def test_shipped_cell_meets_acceptance(): ...   # run_cell("deadband", 3, 15) spike rejected, roc_margin_s ≥ 5
```

- [ ] **Step 2: Run to verify they fail** → import error.
- [ ] **Step 3: Implement**; run `uv run python -m perf.alarm_tuning` and commit the generated doc.
- [ ] **Step 4: Run the task gate** (the engine test takes about 10 s) → green.
- [ ] **Step 5: Commit** `git commit -m "perf: add the deadband and persistence tuning study with the Xu chain check and noise margins"`

---

### Task 10: `except`-coverage checker (gate stays off until Task 24)

**Files:**
- Create: `tests/meta/test_except_coverage.py`, `tests/meta/except_allowlist.txt` (empty, header comment), `tests/meta/fixtures/one_unexecuted.py`
- Modify: `pyproject.toml` (marker `after_coverage`; `addopts` deselects it next to `spike`)

**Interfaces:**
- `handlers(path: Path) -> list[Handler]` (`Handler(path, qualname, exc: str, first_line: int)`; `exc` = `ast.unparse` of the handler type or `"bare"`; `qualname` of the enclosing function, `<module>` at top level).
- `unexecuted(src: Sequence[Path], executed: Mapping[str, set[int]], allow: set[str]) -> list[str]` — a handler counts as executed when its body's first line is in `executed[path]`; allowlist key `"<path>::<qualname>::<exc>"`, one per line, `  # reason` required.
- `@pytest.mark.after_coverage def test_every_handler_executed()` reads `coverage.json` at the repo root (fails with "run pytest --cov=gs --cov-report=json first" when missing) over `src/gs/{ingest,archiver,api}/**/*.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_detects_unexecuted_handler():
    fx = FIXTURES / "one_unexecuted.py"
    assert unexecuted([fx], {str(fx): lines_of_first_handler(fx)}, set())  # test-local helper: every line but the second handler's body == [f"{fx}::g::ValueError"]
def test_allowlist_needs_a_reason(): ...   # a key without "# reason" → ValueError
def test_allowlisted_handler_passes(): ...
```

- [ ] **Step 2: Run to verify they fail** → names undefined.
- [ ] **Step 3: Implement** (stdlib `ast`, `json`).
- [ ] **Step 4: Run the task gate** (the `after_coverage` test is deselected), then the **track merge gate**.
- [ ] **Step 5: Commit** `git commit -m "test: add the except-coverage checker with its own negative control"`

---

## Track G0 – Gate 4 contracts (after the Gate 3b merge, alone)

### Task 11: Replay models and routes, refreeze route, alarm-quality body, replay MinIO user

**Files:**
- Create: `src/gs/common/interfaces/replay.py`, `src/gs/api/replays.py` (stubs)
- Modify: `src/gs/api/bodies.py`, `src/gs/api/sessions.py` (refreeze stub), `src/gs/api/app.py` (include the router), `src/gs/common/interfaces/{__init__,export}.py`, `src/gs/api/openapi.py` (replay SSE models into the components the way Gate 3b Task 1 put the live SSE models into `schema.d.ts`), `migrations/05-gate4.sql` (append the grant), `scripts/minio_init.sh` (append the `replay` user), `docker-compose.yml` (`api` env `S3_ENDPOINT: minio:9000`, `S3_ACCESS_KEY: replay`, `S3_SECRET_KEY: "${S3_REPLAY_SECRET}"`, `S3_BUCKET: archive`), `.env.example` (`S3_REPLAY_SECRET=`), `src/gs/api/settings.py` (`s3: S3Settings`), `tests/integration/{test_openapi,test_minio,test_initdb}.py`, `tests/unit/test_interface_models_strict.py`, `tests/meta/test_compose.py`, `interfaces/**`, `ui/src/api/schema.d.ts`

**Interfaces:**
- `gs.common.interfaces.replay` (every model `replay: Literal[True] = True`, `extra="forbid"`): `ReplayStart(kind="replay_start", replay_id: UUID, vehicle: VehicleId, t_from: UtcTime, t_to: UtcTime, speed: Literal[1, 10], alarms: list[AlarmView], los_open: bool)`; `ReplayTm(Tm)` + `severity: dict[str, Literal["YELLOW", "RED"]]` (parameters with an open recorded alarm; absent = NOMINAL); `ReplayAlarm(Alarm)`; `ReplayLink(Link)`; `ReplayClock(kind="clock", t: UtcTime)`; `ReplayEnd(kind="replay_end", reason: Literal["done", "stopped", "archive_corrupt"], detail: str | None = None)`; `ReplayEvent = RootModel[Annotated[ReplayStart | ReplayTm | ReplayAlarm | ReplayLink | ReplayClock | ReplayEnd, Field(discriminator="kind")]]` exported as `schemas/sse/replay.schema.json`.
- `gs.api.bodies`: `ReplayRequest(vehicle: VehicleId, t_from: UtcTime = Field(alias="from", strict=False), t_to: UtcTime = Field(alias="to", strict=False), speed: Literal[1, 10])`; `ReplayCreated(replay_id: UUID)`; `AlarmQuality(raises: int, false_raises: int | None, psi: dict[str, float], chattering: list[str], short_t1: int, short_t0: int, peak_10min: dict[str, int], time_in_flood_s: float)`; `SessionReport.alarm_quality: AlarmQuality | None = None` (reports frozen before Gate 4 still load).
- Route stubs (`ApiError(501, "NOT_IMPLEMENTED", …)`, final signatures and statuses):

| Route | Signature | Declared statuses |
| --- | --- | --- |
| `POST /api/replays` | `replays.create_replay(body: ReplayRequest, request, user: User) -> ReplayCreated`, 201 | 400 (`INVALID_WINDOW`, `WINDOW_TOO_LARGE`) 401 403 404 409 422 503 |
| `DELETE /api/replays/{replay_id}` | `replays.delete_replay(replay_id: UUID, request, user) -> Response`, 204 | 401 403 404 422 |
| `GET /api/replays/{replay_id}/events` | `replays.replay_events(replay_id: UUID, request, user) -> StreamingResponse` (`text/event-stream`) | 401 404 422 |
| `POST /api/sessions/{session_id}/report/refreeze` | `sessions.refreeze_report(session_id: int, request, user) -> SessionReport` | 401 403 404 409 `ReportConflict` 410 422 503 |

- `migrations/05-gate4.sql` appends `GRANT UPDATE (report, csv, created_at) ON session_report TO gs_app;` (§3.2's matrix has no UPDATE; refreeze needs it). `scripts/minio_init.sh` adds user `replay` with policy `nyx-replay` (`s3:GetObject` on `archive/*`, `s3:ListBucket` on `archive`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_openapi.py (add)
def test_gate4_routes_declared(): ...   # the four paths with the statuses above; refreeze 409 body ReportConflict; replay events text/event-stream only
# tests/unit/test_interface_models_strict.py (add)
def test_replay_models_flag_and_discriminate(): ...  # every member has replay true; ReplayEvent rejects a live "hb"; ReplayTm keeps Tm's fields
def test_old_report_without_alarm_quality_loads(): ...  # a Gate 3b SessionReport JSON validates, alarm_quality None
# tests/integration/test_minio.py (add)
def test_replay_user_reads_only(): ...   # get ok, put and delete AccessDenied
# tests/integration/test_initdb.py (add)
def test_session_report_update_granted(): ...   # gs_app may UPDATE report/csv/created_at, not session_id
```

- [ ] **Step 2: Run to verify they fail**.
- [ ] **Step 3: Implement**; `docker compose down -v && make up`; `make gen`; commit the generated files once (frozen for Tracks RP, SR, PF, DOC).
- [ ] **Step 4: Run the task gate** incl. `make gen && git diff --exit-code -- interfaces ui/src/api`, `cd ui && npx tsc --noEmit && npm test`, and the auth route walk (`test_every_route_requires_session`).
- [ ] **Step 5: Commit** `git commit -m "feat: freeze the Gate 4 REST surface, replay SSE models and the replay MinIO user"`

---

## Track RP – replay (after Task 11)

### Task 12: Replay core – order, recorded alarm state, pacing on replay time

**Files:**
- Create: `src/gs/replay/__init__.py`, `src/gs/replay/rows.py`, `src/gs/replay/run.py`, `tests/unit/test_replay_rows.py`, `tests/unit/test_replay_run.py`, `tests/integration/test_replay_rows.py`

**Interfaces:**
- `gs.replay.rows`:
  - `next_state(prev: AlarmState | None, row_kind: TransitionKind, closes: bool) -> AlarmState` — the display state after a recorded row (no state column exists): RAISE → UNACK; ESCALATE → UNACK (from ACKED this is the re-alarm); DEESCALATE keeps; RTN → RTN_UNACK; CLEAR → CLEARED; ACK → CLEARED when `closes` (`alarm_events` row of an RTN_UNACK alarm), else ACKED. Pinned against the engine (test below).
  - `async def recorded_alarm_rows(conn, vehicle, t_from, t_to) -> list[dict]` and `async def recorded_event_rows(conn, vehicle, t_from, t_to) -> list[EventView]` (vehicle rows only, `t_onboard` in the window).
  - `async def initial_alarms(conn, vehicle, t_from) -> list[AlarmView]` — one query (CTE: every `alarm_events` row of the alarms whose latest row positioned before `t_from` does not close them), folded with `next_state`; static fields from `alarms` (`trigger_check`, `opened_at`, hashes); `ack_request` None.
  - `async def los_open_at(conn, vehicle, t_from) -> bool` (latest LOS-pair event before `t_from` is a LOS).
  - `def position(kind: Literal["frame", "alarm", "ack", "event"], row) -> tuple[datetime, int, int, int]` — frame `(t_onboard, seq, 0, 0)`; transition `(trigger_t_onboard, trigger_seq, 1, id)`; ACK `(after_t_onboard, after_seq, 1, id)` (directly after the frame it names); event `(t_onboard, 2**32, 2, id)`.
- `gs.replay.run`: `CLOCK_STEP_S = 0.1` (wall); `async def replay_messages(conn, s3, bucket: str, mdb: MissionDb, replay_id: UUID, vehicle: str, t_from: datetime, t_to: datetime, speed: Literal[1, 10], sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> AsyncIterator[ReplayStart | ReplayTm | ReplayAlarm | ReplayLink | ReplayClock | ReplayEnd]` — `ReplayStart` first; then per hour from `window_hours` the frames merged with the recorded rows by `position`, **nothing recomputed**; each frame a `ReplayTm` with `severity` from the open recorded alarms; between items it sleeps `Δt_onboard / speed` in steps of `CLOCK_STEP_S` and emits a `ReplayClock(t)` per step (replay time advances during a dropout); `ArchiveCorrupt` mid-run → `ReplayEnd("archive_corrupt", "archive object failed hash check")`; else `ReplayEnd("done")`. Never writes a table.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_replay_rows.py
def test_next_state_tracks_engine(): ...   # leak → RAISE, ACK, ESCALATE, DEESCALATE, RTN, CLEAR and an ACK of an RTN_UNACK alarm through engine.step/engine.ack; after every transition next_state == the engine's alarm state (CLEARED when removed)
def test_position_puts_ack_after_named_frame(): ...
# tests/unit/test_replay_run.py — fake window and rows, fake sleep recording durations
async def test_order_and_shapes(): ...      # ReplayStart, then frames and rows in position order, ReplayEnd("done"); every message replay=True and validates against replay.schema.json
async def test_pacing_by_t_onboard_at_10x(): ...   # 10 Hz frames at speed 10 → sleeps sum to window/10
async def test_clock_advances_through_dropout(): ...  # a 10 s frame gap at 1× → 100 ReplayClock messages with increasing t
async def test_corrupt_hour_ends_stream(): ...
# tests/integration/test_replay_rows.py
async def test_initial_state_acked_then_escalate(): ...   # recorded RAISE + ACK before t_from, ESCALATE inside → ReplayStart.alarms state ACKED, then a ReplayAlarm with state UNACK
async def test_los_open_at_window_start(): ...
```

- [ ] **Step 2: Run to verify they fail** → import errors.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: replay recorded frames, transitions and events in order on replay time"`

---

### Task 13: Replay API and the live-stream filter for `NYX-TA9`

**Files:**
- Modify: `src/gs/api/replays.py`, `src/gs/api/app.py` (lifespan: S3 client, `app.state.replays`), `src/gs/api/hub.py` (drop `ui:events` messages whose vehicle is not in the API's `VEHICLES`)
- Create: `tests/integration/test_replay_api.py`

**Interfaces:**
- Consumes the Task 11 stubs, `replay_messages`, `verify_window`, `has_data`, `ArchiveCorrupt`, `deps.mission`.
- `MAX_WINDOW = timedelta(hours=24)`, `REPLAY_IDLE_S = 60`; `@dataclass class Replay(id: UUID, user: str, req: ReplayRequest, created: float, stop: asyncio.Event)`.
- `POST`: `from ≥ to` → 400 `INVALID_WINDOW`; window > 24 h → 400 `WINDOW_TOO_LARGE`; `has_data` false → 404 `NO_DATA` "no data in window"; `verify_window` raises → 409 `ARCHIVE_CORRUPT` "archive object failed hash check"; DB or MinIO unreachable → 503; else 201. Replays are per process, owned by the creating user (another user → 404), removed when their stream ends, on `DELETE`, on client disconnect, or after `REPLAY_IDLE_S` without a `GET`. The first `GET` takes the replay (a second `GET` → 404). Speed is not a security boundary; replay never accepts ACKs or sounds (D-REP-03).
- SSE framing and `retry` as the live `/api/events`; each message `event: <kind>` + `data: <model_dump_json()>`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_create_and_stream_hot_window(): ...   # in-process worker + backfill of a vid() vehicle, authed client: 201, GET streams replay_start … replay_end done; every frame of the window once
async def test_no_data_404(): ...
async def test_corrupt_hour_409(): ...              # Task 5 scenario: POST → 409, message "archive object failed hash check"
async def test_window_rules_400(): ...              # from == to; 24 h + 1 s
async def test_other_user_404_and_second_get_404(): ...
async def test_delete_stops_replay(): ...
async def test_disconnect_stops_replay(): ...       # client closes mid-stream → the generator is closed (no further s3.get), registry empty within 1 s
async def test_idle_replay_expires(): ...           # REPLAY_IDLE_S patched small
async def test_hub_drops_vehicles_outside_api_vehicles(): ...   # a tm for vid() not in VEHICLES published to ui:events → no client receives it; a TA1 tm still arrives
```

- [ ] **Step 2: Run to verify they fail** → 501.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run the task gate** plus `tests/integration/test_auth.py tests/integration/test_sse.py -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: serve replays over SSE with window, data and hash checks, and keep NYX-TA9 off the live console"`

---

### Task 14: Replay equality and determinism (D-REP-05, D-REP-06)

**Files:**
- Create: `tests/support/recompute.py`, `tests/integration/test_replay_equality.py`

**Interfaces:**
- `tests/support/recompute.py`: `recompute_keys(frames: Sequence[pa.Table], acks: Sequence[Mapping[str, Any]], sets: Mapping[str, MissionDb], vehicle: str) -> tuple[list[str], list[str]]` — `fold` from `empty_state` over the recorded frames one at a time; before the first frame after each ACK's `(after_epoch, after_seq)` it applies `engine.ack` with the recorded key, user, note and time; the set switches at each `LIMITS_APPLIED` by its `set_sha256` (`missiondb.load` of the `limits_versions` bytes); returns (transition keys in order, fold-produced event keys).
- Recording fixture (module scope): in-process worker on a vid() vehicle; `backfill` of 70 min with `leak@600` and `dropout@2400:{"duration_s":10}`; `pause_at` the seq where the ROC alarm is YELLOW → the test POSTs the ACK through the API and waits for ACKED, then resumes (the leak then escalates: ack-then-escalate); a second pause where the test edits a copy of `mission/limits.yaml` (cabin temp yellow_high 27 → 26.5) and waits for `LIMITS_APPLIED`; then `run_pass` with retention 0 archives and deletes the first full hour.

- [ ] **Step 1: Write the tests**

```python
@pytest.mark.parametrize("source", ["parquet", "hot"])
async def test_replay_equals_recorded(source, recording):
    # replay(window) == the alarm_events / events rows recorded live for that window, same order (position),
    # incl. the ack-then-escalate rows, the data-STALE window with its LOS-pair rows and the GAP
    ...
async def test_recompute_reproduces_recorded_keys(recording):
    keys, events = recompute_keys(...)
    assert keys == recorded_transition_keys          # every alarm_events key incl. ACK rows, in order
    assert set(events) >= recorded_frame_event_keys  # GAP, SEQ_*, FROZEN, LIMITS_APPLIED and the LOS pair of the 10 s dropout
    # excluded by rule: a LOS pair recorded by the read loop's wall-clock timer during a backfill pause (vehicle-time gap ≤ 2 s); it never resets the engine
async def test_late_frame_in_archived_hour(recording): ...   # one late row into the archived hour → replay shows it once (merged, EX-29)
async def test_window_across_added_parameter(recording): ... # replay rows of the archived hour carry null for a parameter added afterwards (EX-19)
async def test_reducer_matches_alarms_table(recording): ...  # next_state folded over each alarm's rows == alarms.state
```

- [ ] **Step 2: Run them**: they pass on the code of Tasks 6, 12, 13 (this task adds proof, not code); a failure is a bug → failing test kept, fix in the owning module, `docs/bug-log.md` row.
- [ ] **Step 3: Implement** only `tests/support/recompute.py`.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "test: prove replay equals the recorded rows and that step() reproduces the recorded keys"`

---

### Task 15: Console replay page with live alarms (D-UI-16, D-REP-06)

**Files:**
- Create: `ui/src/replay.ts`, `ui/src/replay.test.ts`, `ui/src/ReplayPage.tsx`, `ui/src/replayPage.test.tsx`
- Modify: `ui/src/App.tsx` (a "Replay" toggle in the top bar; the live store, live SSE, beep and tone stay mounted), `ui/src/api.ts` (`createReplay(req) -> Promise<{replay_id}>`, `deleteReplay(id)`), `ui/src/style.css` (purple frame; existing REPLAY token), `ui/src/style.test.ts`

**Interfaces:**
- `replay.ts`: `createReplayStore()` with `ingestReplay(store, kind, data)` for every `ReplayEvent` kind; state: `t` (replay `t_onboard` now = the latest `clock` or `tm` time), per-parameter last value and `t_onboard`, `severity`, alarms by id (read-only), events (newest first), `losOpen`, `ended` reason; `replayParamState(...)` = `paramState` with `age = t − t_onboard` (STALE at 2.0 s of vehicle time, D-REP-06); LOS from the recorded LOS-pair events and `ReplayStart.los_open`.
- `ReplayPage.tsx`: vehicle select (from `/api/vehicles`), `<input type="datetime-local">` from/to labelled "UTC" and read as UTC, speed 1× / 10×, Start / Stop, progress = replay time (`toISOString()`), purple frame and a **REPLAY** banner that can't be dismissed showing the live counter "LIVE: n unacknowledged (r RED)" from the **live** store; replay table and alarm list reuse the live components read-only (no ACK button); errors 400/404/409 shown with the API message.
- Never beeps or plays the tone for replay messages (D-REP-03); live RED still beeps (live store untouched).

- [ ] **Step 1: Write the failing tests**

```ts
// replay.test.ts
test("stale on replay time at 1x and 10x", ...)   // tm at T, clocks to T+2.0 → STALE; T+1.9 → not
test("los from recorded rows and replay_start", ...)
test("replay alarms never reach the beep set", ...)
// replayPage.test.tsx
test("inputs are UTC", ...)                        // 2026-11-10T14:00 → from "2026-11-10T14:00:00.000Z"
test("banner shows the live counter", ...)          // live store with 2 UNACK (1 RED) → "LIVE: 2 unacknowledged (1 RED)"
test("409 shows the hash message", ...)
```

- [ ] **Step 2: Run to verify they fail** — `cd ui && npm test` → modules missing.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `cd ui && npx tsc --noEmit && npm test && npm run build` (coverage ≥ 80 %), then the **track merge gate** incl. system tests.
- [ ] **Step 5: Commit** `git commit -m "feat: add the replay page with replay-time freshness and the live alarm counter"`

---

## Track SR – sessions (after Task 11)

### Task 16: Report refreeze (D-DB-10)

**Files:**
- Modify: `src/gs/api/sessions.py`, `src/gs/api/report.py`, `tests/integration/test_sessions.py`

**Interfaces:**
- `async def refreeze(conn, session_id: int, mdb: MissionDb, now: datetime) -> SessionReport | ReportConflict | Literal["gone"] | None` in `report.py`: None → 404; no stored report → 409 `REPORT_PENDING` (the existing body); stored `complete` → 409 `REPORT_COMPLETE`; any `archive_object` row of the session's vehicle overlapping `[started_at, ended_at)` with `hot_deleted_at` set → 410 `HOT_DATA_GONE`; else `build_report` with the watermark rule of `frozen_report`, `UPDATE session_report SET report, csv, created_at = clock_timestamp()` and audit `REPORT_REFROZEN` (actor = user, details `{session_id, frames, expected, complete}`) in one transaction; 200 with the new report.

- [ ] **Step 1: Write the failing tests**

```python
async def test_refreeze_incomplete_report(): ...   # frozen incomplete (FREEZE_AFTER_S patched), late rows inserted, refreeze → complete or larger n, stored row replaced, audit REPORT_REFROZEN
async def test_refreeze_complete_409(): ...
async def test_refreeze_never_frozen_409_pending(): ...
async def test_refreeze_after_hot_delete_410(): ...  # archive_object row with hot_deleted_at inside the window
async def test_refreeze_needs_console_header_and_session(): ...
```

- [ ] **Step 2: Run to verify they fail** → 501.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run the task gate** → green.
- [ ] **Step 5: Commit** `git commit -m "feat: refreeze an incomplete session report while its hot rows exist"`

---

### Task 17: Alarm-quality section in the session report (D-DB-06, D-ALM-15)

**Files:**
- Modify: `src/gs/api/report.py`, `tests/integration/test_sessions.py`, `tests/unit/test_report_csv.py`

**Interfaces:**
- Consumes `gs.alarms.quality.quality`, `QEvent`, `AlarmQuality`.
- `build_report` sets `alarm_quality` from the session's `alarm_events` (same window and order as `alarms`), `fault_windows=None` (no fault log on a real bench, so `false_raises` null). CSV rows `section = "alarm_quality"`, `key` = metric (`raises`, `false_raises`, `short_t1`, `short_t0`, `time_in_flood_s`, `peak_10min:<vehicle|all>`, `psi:<vehicle>/<param>`), number in `value`; P-pf30 quoting unchanged.

- [ ] **Step 1: Write the failing tests**

```python
async def test_report_alarm_quality(): ...   # session with a leak alarm and a chattering parameter (hand-inserted rows) → raises, psi, short_t1, peak_10min, false_raises None, CSV rows present
async def test_old_stored_report_still_served(): ...  # a stored Gate 3b report JSON without the key → GET report 200, alarm_quality null
```

- [ ] **Step 2–4: Run to fail, implement, run the task gate**, then the **track merge gate**.
- [ ] **Step 5: Commit** `git commit -m "feat: add the alarm-quality section to the session report and its CSV"`

---

## Track PF – performance (after Task 11)

### Task 18: p95 with its confidence interval from pooled samples (D-PERF-10)

**Files:**
- Modify: `perf/stats.py`, `perf/run_n.py` (summary), `tests/unit/test_perf_stats.py`, `tests/unit/test_run_n.py`

**Interfaces:**
- `ci_ranks(n: int, p: float = 0.95) -> tuple[int, int] | None` (Le Boudec ranks of Global Constraints; None when the upper rank > n or the lower < 1); `p95_ci(xs: Sequence[float]) -> tuple[float, float] | None`; `pooled(samples: Mapping[str, Sequence[float]]) -> list[float]` (raw concatenation across vehicles and runs); `Verdict(p95: float, ci: tuple[float, float] | None, limit: float, passed: bool, marginal: bool)` with `verdict_of(xs, limit) -> Verdict` (`passed` = point p95 < limit; `marginal` = `ci[0] ≤ limit ≤ ci[1]`); `ci_text(v) -> str` ("n too small for a two-sided CI" when None).
- `run_n` summary: every p95 printed with its CI and the marginal flag; ALM-05 shows the first 20 (the spec's sample) and all 120 pooled.

- [ ] **Step 1: Write the failing tests**

```python
def test_p95_ci_ranks(): assert ci_ranks(120) == (109, 120) and ci_ranks(60) is None
def test_pooled_not_averaged():
    s = {"NYX-TA1": [1.0] * 100 + [10.0] * 20, "NYX-TA2": [1.0] * 120}
    assert p95(pooled(s)) != (p95(s["NYX-TA1"]) + p95(s["NYX-TA2"])) / 2
def test_marginal_when_ci_straddles(): ...
def test_summary_prints_ci_and_small_n_text(): ...   # run_n summary on hand-built samples
```

- [ ] **Step 2–4: Run to fail, implement (stdlib), run the task gate.**
- [ ] **Step 5: Commit** `git commit -m "perf: report every p95 with its rank-based confidence interval from pooled samples"`

---

### Task 19: Simulator rate and the run P driver (D-PERF-05/06/08)

**Files:**
- Create: `perf/run_p.py`, `tests/unit/test_run_p.py`
- Modify: `src/gs/simulator/frames.py` (`tick_loop(..., period_s: float = PERIOD_S)`), `src/gs/simulator/main.py` (`SimSettings.rate_hz` from optional `SIM_RATE_HZ`, default 10, accepted 1–100), `docker-compose.yml` (`SIM_RATE_HZ: "${SIM_RATE_HZ:-10}"`), `tests/unit/test_sim_settings.py`, `tests/unit/test_sim_tick_independent.py`, `tests/meta/test_compose.py`, `Makefile` (`perf-p:` → `uv run python -m perf.run_p`, smoke `make -n perf-p`), `perf/run_n.py` (the helpers run P calls become public, signatures unchanged)

**Interfaces:**
- `perf.run_p`: `DURATION_S = 300`, `RATE_HZ = 100`, `SAMPLE_EVERY_S = 1.0`, `LAG_LIMIT_S = 1.0`; `lag_row(groups: Sequence[Mapping[str, Any]], time_lag_s: float, t: datetime) -> tuple` (`XINFO GROUPS` lag + pending and `clock_timestamp() − max(t_onboard)` per vehicle); `lag_verdict(rows) -> Verdict` (max time lag < 1 s); `totals(...) -> dict` (worker CPU % from `docker stats --no-stream` at 1 Hz for `ingest-1`/`ingest-2`, Redis `used_memory` before/after and per vehicle-hour, ingest latency p50/p95 as the DB insert time, frames stored); `main()` restarts the simulator with `SIM_RATE_HZ=100`, samples for 5 min, records machine spec, image digests, load average before/after, the same-machine load-driver line (D-PERF-08), writes `docs/perf/run-p-<UTC date>/` (`lag.csv`, `ingest.csv`, `alarm.csv`, `query.csv`, `totals.json`, `summary.md`; ING-10 pass/fail, the others informative with CIs), then restores `SIM_RATE_HZ=10`.

- [ ] **Step 1: Write the failing tests**

```python
def test_rate_from_env(): ...            # SIM_RATE_HZ=100 → period 0.01; 0 / 101 / "x" → ConfigError
async def test_tick_grid_at_100hz(): ...  # fake clock: 100 frames per second on the absolute grid
def test_lag_row_and_verdict(): ...       # max time lag 0.99 pass, 1.0 fail
def test_totals_from_hand_built_samples(): ...
```

- [ ] **Step 2–4: Run to fail, implement, run the task gate** (+ `make -n perf-p`).
- [ ] **Step 5: Commit** `git commit -m "perf: add the simulator rate setting and the run P driver with the ING-10 lag sampler"`

---

### Task 20: Scaling analysis generator (D-PERF-09)

**Files:**
- Create: `perf/scaling.py`, `tests/unit/test_scaling.py`

**Interfaces:**
- `TARGET_FPS = 1000` (10 vehicles × 100 Hz); `capacity_fps(fps: float, cpu_fraction: float) -> float`; `catch_up_s(backlog_frames: float, capacity: float, load: float) -> float` (`inf` when capacity ≤ load: the metastable condition, Huang et al. 2022 p. 7); `extrapolate(totals: Mapping[str, float]) -> Scaling` (worker CPU at 1000 frames/s, Redis MB per vehicle-hour × 10, insert time); `render(s: Scaling) -> str` — `docs/perf/scaling.md` naming the first bottleneck and its fix from the design (engine per vehicle single-threaded → split by parameter group; UI coalesces to 10 Hz renders; 1 min rollups at ~22 GB/day) and the headroom sentence ("a restarted worker is fast enough to catch up with the missed and ongoing load" only when `catch_up_s` is finite); `main(run_dir: Path)` reads `totals.json`.

- [ ] **Step 1: Write the failing tests** — `test_capacity_and_catch_up` (200 fps at 20 % CPU → 1000 fps; backlog 6000 at load 200 → 7.5 s; capacity 200 at load 200 → inf), `test_render_names_bottleneck_and_metastable_line`.
- [ ] **Step 2–4: Run to fail, implement, run the task gate.**
- [ ] **Step 5: Commit** `git commit -m "perf: generate the scaling analysis from run P totals"`

---

### Task 21: ATP-01 soak tooling (ING-01, D-ALM-15)

**Files:**
- Create: `perf/soak_count.py`, `tests/unit/test_soak_count.py`
- Modify: `Makefile` (`soak:` → `uv run python -m perf.soak_count --hours 2`, smoke `make -n soak`)

**Interfaces:**
- `lost_by_epoch(rows: Sequence[tuple[datetime, int, int, int]]) -> int` (per `(epoch, first_seq, last_seq, count)`: Σ(last − first + 1 − count)); `main(hours: float)` — logs in as `conductor` (password from `.env`, never printed), starts sessions on NYX-TA1 and NYX-TA2, waits `hours`, stops both, waits for the frozen reports and downloads both CSVs; computes lost frames by `(epoch, seq)`, GAP events, simulator `overflow` from its `/healthz`, `quality()` over the window's `alarm_events` with `fault_windows=[]` (nothing injected, so every RAISE is false), writes `docs/perf/soak-<UTC date>/summary.md` (pass = 0 lost, 0 false raises, no chattering).

- [ ] **Step 1: Write the failing tests** — `test_lost_by_epoch` (gapless → 0; one epoch missing 3 → 3; two epochs), `test_summary_pass_rule` (hand-built quality and counts).
- [ ] **Step 2–4: Run to fail, implement, run the task gate**, then the **track merge gate**.
- [ ] **Step 5: Commit** `git commit -m "perf: add the ATP-01 soak count with alarm-quality metrics"`

---

## Track DOC – documents and checks (after Task 11)

### Task 22: Automated checks for inspection items and the trace

**Files:**
- Create: `scripts/check_commits.py`, `tests/unit/test_check_commits.py`, `tests/meta/test_stack.py`, `tests/meta/test_requirements_trace.py`
- Modify: `.github/workflows/ci.yml` (lint job: `uv run python scripts/check_commits.py origin/main`; `! grep -rn "toLocale" ui/src --include=*.ts --include=*.tsx`)

**Interfaces:**
- `check_commits.bad_subjects(subjects: Sequence[str]) -> list[str]` — allowed: `^(feat|fix|refactor|docs|test|chore|perf|ci|merge)(\([^)]+\))?!?: \S`, and `^Merge ` (git and GitHub defaults); `main(base: str) -> int` over `git log --format=%s <base>..HEAD` (ENG-07).
- `tests/meta/test_stack.py::test_python_package_and_react_typescript_ui` (ENG-10: `pyproject.toml` `requires-python`, `ui/package.json` has `react` and `typescript`).
- `tests/meta/test_requirements_trace.py`: `evidence(md: str) -> dict[str, list[Ref]]` parses every table row of `docs/requirements.md` with prio M or S (the 47); a `Ref` is a test file (`unit/…`, `integration/…`, `system/…`, `meta/…` under `tests/`, `e2e/…`, `ui/src/…`, `perf/…`, `scripts/…`) with optional `::name` (bare `::name` continues the previous file; `[params]` stripped); `missing(refs) -> list[str]` (file absent, or no `def name` / `async def name` / `test("name"` in it); `test_every_requirement_has_evidence` (each of the 47 has a ref, or method I with an inspection note); `test_every_named_test_exists` — **expected to fail until Task 25** (marked `xfail(strict=True)` here; Task 25 removes the mark).

- [ ] **Step 1: Write the failing tests** — `test_bad_subjects` (`"feat: x"`, `"merge: Track C into Gate 3b"`, `"Merge pull request #2"` ok; `"update stuff"`, `"feat:x"` bad), `test_stack`, `test_trace_parser_on_a_fixture_table`, `test_every_requirement_has_evidence`.
- [ ] **Step 2–4: Run to fail, implement, run the task gate.**
- [ ] **Step 5: Commit** `git commit -m "test: automate the commit-message, stack and requirement-trace inspections"`

---

### Task 23: README, CHANGELOG, guides and ATP procedures (spec §6)

**Files:**
- Create: `README.md`, `CHANGELOG.md`, `docs/atp/ATP-01.md`, `docs/atp/ATP-07.md`, `docs/atp/ATP-08.md`, `docs/img/console.png` (screenshot of the running console), `tests/meta/test_readme_sections.py`
- Modify: `docs/operator-guide.md` (section "Run a replay": pick vehicle, UTC window, speed, the purple banner and live counter, what 409 means), `docs/dev-guide.md` (archiver settings `HOT_RETENTION_H`, `ARCHIVE_LOOP_S`, backfill into NYX-TA9, `make perf-p`, `make soak`), `tests/meta/test_guides.py`

**Interfaces:**
- README sections (exact headings, checked): `## What it is`, `## Run` (one code block of three commands: `git clone …`, `cd nyx`, `make up`), `## What to try first`, `## Architecture`, `## Decisions and why`, `## Limitations` (from design §4, incl. the single MinIO copy: detection, no repair), `## Next steps`, plus the screenshot `docs/img/console.png`; the demo link is added at Gate 5.
- CHANGELOG: `## [Unreleased]` with "What's in" (per gate) and "Known limitations"; Gate 5 turns it into `1.0.0`.
- `docs/atp/ATP-07.md`: manual steps someone else can repeat — backfill NYX-TA9, wait for the archiver, `mc cp` an object and its sidecar and run `sha256sum -c`, kill the archiver mid-upload, replay a 20 min window at 10×; `ATP-01.md` (`make soak`), `ATP-08.md` (`make perf`, `make perf-p`, scaling).

- [ ] **Step 1: Write the failing tests** — `test_readme_sections` (headings, three commands, screenshot file exists), `test_changelog_has_unreleased_and_limitations`, `test_guides` adds the replay section and the dev-guide items, `test_atp_procedures_exist`.
- [ ] **Step 2–4: Run to fail, write the documents, run the task gate**, then the **track merge gate**.
- [ ] **Step 5: Commit** `git commit -m "docs: add the README, changelog, replay section of the operator guide and ATP procedures"`

---

## Track Z – integration and runs (after RP, SR, PF, DOC are merged)

### Task 24: Turn the `except`-coverage gate on (D-ENG-21)

**Files:**
- Modify: `.github/workflows/ci.yml` (`test` job: `uv run pytest --cov=gs --cov-fail-under=80 --cov-report=json:coverage.json`, then `uv run pytest --no-cov -m after_coverage tests/meta/test_except_coverage.py`), `tests/meta/except_allowlist.txt`, tests that execute uncovered handlers
- [ ] **Step 1:** run both commands locally; list the unexecuted handlers under `src/gs/{ingest,archiver,api}`.
- [ ] **Step 2:** for each, write the test that executes it (preferred), or allowlist it with the reason (a handler reached only by a system test names that test). A handler that turns out wrong is a bug: failing test, fix, `docs/bug-log.md` row.
- [ ] **Step 3:** both commands green; `.gitignore` has `coverage.json`.
- [ ] **Step 4: Commit** `git commit -m "ci: require every except handler in ingest, archiver and api to be executed or allowlisted"`

---

### Task 25: Replay end to end, ATP-07, and closing the trace

**Files:**
- Create: `e2e/replay.spec.ts`
- Modify: `tests/meta/test_requirements_trace.py` (remove the `xfail`), `docs/requirements.md` (only where a built test carries another name: point the row at the real test, never drop one), tests the trace check reports missing

**Interfaces:**
- `e2e/replay.spec.ts` (stack with NYX-TA9 backfilled through `tests/support/archive.py::backfill_ta9` before the run): banner purple and not dismissible; speed 10×; a window with the 10 s dropout shows STALE and LOS on replay time; the replay alarm list matches the recorded transitions; a live RED injected on NYX-TA1 during the replay beeps once and raises the banner counter; the 409 message for a corrupted hour.

- [ ] **Step 1:** write `e2e/replay.spec.ts`; run `sh /Users/malek/try/nyx/.superpowers/syslock.sh sh -c 'make up && cd e2e && npx playwright test replay.spec.ts'` → green.
- [ ] **Step 2:** run `uv run pytest tests/meta/test_requirements_trace.py -q`; for every missing name write the test (or the code and its test) it names; repeat until green.
- [ ] **Step 3:** full gate on `gate4-test-readiness`: coverage suite, `after_coverage`, `syslock.sh uv run pytest -q tests/system`, `syslock.sh` e2e, ui tests, `make gen` diff.
- [ ] **Step 4: Commit** `git commit -m "test: replay end to end and close every requirement's named evidence"`

---

### Task 26: Measurement runs – ATP-08 and the ATP-01 2 h soak

**Files:**
- Create (generated, committed): `docs/perf/run-n-<date>/**`, `docs/perf/run-p-<date>/**`, `docs/perf/scaling.md`, `docs/perf/soak-<date>/**`
- [ ] **Step 1:** fresh stack (`docker compose down -v && make up`), machine otherwise idle; `sh /Users/malek/try/nyx/.superpowers/syslock.sh make perf` (run N with CIs).
- [ ] **Step 2:** `sh /Users/malek/try/nyx/.superpowers/syslock.sh make perf-p`, then `uv run python -m perf.scaling docs/perf/run-p-<date>`.
- [ ] **Step 3:** `sh /Users/malek/try/nyx/.superpowers/syslock.sh make soak` (2 h).
- [ ] **Step 4:** check every summary: ING-09 < 200 ms, ALM-05 < 300 ms on all 120 (first 20 shown), UI-01 < 1 s, DAT-03 < 300 ms, ING-10 max time lag < 1 s, each p95 with its CI and marginal flag; soak 0 lost, 0 false raises. A failing value is reported to the coordinator as is (no tuning, no rerun until it passes).
- [ ] **Step 5: Commit** `git commit -m "perf: record run N, run P, the scaling analysis and the ATP-01 soak"`

---

### Task 27: Record the rulings

**Files:**
- Modify: `docs/design.md` (D-ARC-04/06 `version` column and the version mechanics, D-ARC-05 "alert" = ERROR log + `/healthz` `delete_refused`, D-ARC-11 unresolved rule and key suffix, D-ENG-16 `ARCHIVE_LOOP_S` and the backfill library/CLI split, D-REP-04 replay message kinds, D-ALM-16 acceptance per R20, §3.1 replay/refreeze statuses, §3.2 `05-gate4.sql`, §3.7 note: the archiver and API MinIO users), `docs/assumptions.md` (A-R16-2 closed by D-ARC-11, A-R20-3 if Task 9 lists a dominating value, A-R20-2 still open), `docs/requirements.md` (DAT-04..06, ALM-02/03/05, ING-09/10, ENG-03/05/07/08/10, UI-08 evidence lines), `docs/bug-log.md`
- [ ] **Step 1:** write each ruling of the section below into the decision it changes, one sentence plus the ruling ID.
- [ ] **Step 2:** `uv run pytest tests/meta -q` green (trace and guides).
- [ ] **Step 3: Commit** `git commit -m "docs: record the Gate 4 rulings in the design, assumptions and trace"`

---

## Rulings made by this plan (for the coordinator)

- **T4-a** DDL is a new `migrations/05-gate4.sql` (ALTERs + one grant), not an edit of Track 0's `02-tables.sql`; the result equals §3.2 plus `archive_object.version`.
- **T4-b** D-ARC-06 v1+: one `archive_object` row per `(vehicle, hour)` names the latest verified version; late rows before the hot delete → version N+1 from all hot rows; after it → previous object ∪ late rows, the row update and the late-row delete in one transaction with the count/min/max check against the late rows.
- **T4-c** "Alerts" on a refused delete = an ERROR log line with the vehicle plus `/healthz` `degraded` (`delete_refused`) until that hour deletes.
- **T4-d** An `ARCHIVE_CORRUPT` is unresolved while its `expected` equals the row's current `sha256`; versions N ≥ 1 get a `/v<N>` key suffix.
- **T4-e** A-R16-2's default (show a gap) yields to D-ARC-11: the whole replay is refused with 409.
- **T4-f** Replay windows: `from < to` and ≤ 24 h, else 400 (`INVALID_WINDOW`, `WINDOW_TOO_LARGE`); one consumer per replay; replays per process, owner-only, removed on end, delete, disconnect or 60 s idle; not audited (D-CMD-08's list has no replay action).
- **T4-g** Replay SSE: the live shapes subclassed with `replay: true`, plus `replay_start` (alarms and LOS at `from`), `clock` (replay time every 100 ms wall) and `replay_end`; alarm state comes from a reducer over the recorded rows (no state column), pinned against the engine.
- **T4-h** `NYX-TA9`: `mission/` has no vehicle list, so TA9 lives in the ingest `VEHICLES` only; the CLI accepts only TA9, the library any vehicle (integration tests use vid()); the API hub drops vehicles outside its `VEHICLES`; replay accepts any vehicle id (404 without data).
- **T4-i** D-ALM-16: the grid runs as specified, the acceptance follows R20 F2 (noise margins, Monte Carlo only for ROC and fault profiles); ROC grid 0.05–0.25 kPa/s; the study bypasses the load-time floor on purpose.
- **T4-j** D-ALM-15 details: duration = RAISE → RTN/CLEAR, interval = RTN/CLEAR → next RAISE; flood time combined; `step` alarms dropped via the step window; ATP-01 passes `fault_windows=[]`, the report `None`.
- **T4-k** `except` gate: marker `after_coverage`, run after the coverage run in CI; allowlist keys `<path>::<qualname>::<exc>  # reason`.
- **T4-l** The API reads MinIO as a get-only `replay` user (R2-19 applied to the API).
- **T4-m** Refreeze: never-frozen → 409 `REPORT_PENDING`; `HOT_DATA_GONE` when any archived hour of the session window is hot-deleted; needs `GRANT UPDATE (report, csv, created_at)`.
- **T4-n** Determinism test compares every `alarm_events` key and the frame-produced event keys; a LOS pair recorded by the wall-clock timer during a backfill pause is excluded (it never resets the engine).
- **T4-o** Replay banner counter = live UNACK alarms with the RED count.
- **T4-p** `init_env` appends missing keys to an existing `.env` (never rewrites a line).
- **T4-q** Archiver `/healthz`: DB unreachable → down; Redis or MinIO unreachable → degraded; a pass that saw an S3 error deletes nothing.
- **T4-r** `ARCHIVE_LOOP_S` (default 300) and `SIM_RATE_HZ` (default 10, 1–100) join the D-ENG-16 test controls; no injectable clock.
- **T4-s** Backfill paces by buffer back-pressure, not wall rate, and starts after the last stored TA9 hour (`next_ta9_window`).

## Self-review notes

- Gate 4 row coverage: archiver D-ARC-01 (T3), 02/03 (T3), 04 (T3, T4), 05 (T1, T4), 06 v1 + v1+ (T1, T4), 07 (T5), 08 (T3), 09 user/policy (T2), 10 (T5), 11 (T5, T6); test controls D-ENG-16: `HOT_RETENTION_H` (T4, T5), `archive:*` hooks (T3, T7), backfill into NYX-TA9 with pause-for-ACK (T7, T14); replay D-REP-01 (T6, T12), 02 (T12), 03 (T12, T15), 04 (T11, T13), 05 (T14), 06 (T12, T15); live alarms on the replay page D-UI-16 (T15, T25); report refreeze D-DB-10 (T11, T16); run P + scaling D-PERF-05/06/08/09 (T19, T20, T26); remaining §6 docs (T23); ATP-01 2 h soak (T21, T26); D-ALM-15 soak + report (T8, T17, T21); D-ALM-16 + R20 (T9); D-PERF-10 (T18); D-ENG-21 (T10, T24); range checks with `hour_bounds()` and the scrub (T1, T4, T5); exit check "every requirement has a test or an inspection note" (T22, T25); ATP-07 (T7, T14, T25), ATP-08 (T26).
- Depends on Gate 3b names (re-check at Task 11 and adjust Interfaces if they landed differently): `report.build_report` / `frozen_report` / `report_csv` / `CSV_COLUMNS`, `perf.stats` (`nearest_rank`, `p95`, `after_warmup`, `write_csv`), `perf.run_n` helpers, the hub module name `gs.api.hub`, the ACK request route, `tests/meta/test_guides.py`, the live SSE model export path into `schema.d.ts`, and `tick_loop`'s keyword parameters (Task 7 is written against `9a0dcef`).
