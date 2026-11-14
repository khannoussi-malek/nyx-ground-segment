# Gate 3a – Ingest Depth and Alarms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the design's "3 – Implementation day 2" row: ingest depth (D-ING-04..26: blocking advisory-lock ownership, hand-back, `idle` steal behind a pre-read fence, Postgres error classes with the delivery-counter reset, reclaim + heartbeat inside the read loop, watchdog, sequence tracker, LOS/AOS and FROZEN events), the alarm engine with its `engine_state` snapshot (D-ALM-01..12), the deadband floor, mission-db hot reload with the un-migrated fallback, simulator faults (D-SIM-07/08/11), test hooks and env thresholds (D-ENG-16) incl. `STOP_AT=after_fence_before_read`, and ruff `BLE001`/`S110`/`FIX` (D-ENG-21), so the backend halves of ATP-04 and ATP-05 pass.
**Architecture:** Pure cores (sequence tracker, link/FROZEN detectors, check registry, alarm engine, mission-db poll rule) are unit-tested in isolation; the owner worker folds them over each batch and writes frames, `seq_state`, `events`, alarm rows and the `engine_state` snapshot in one transaction on its advisory-lock connection, then publishes `tm`, then XACKs. Ownership, failover and liveness live in the read loop. The work runs as parallel tracks after a shared-contracts track; a final integration track wires the engine and the reload into the worker and runs the cross-track system tests.
**Tech Stack:** Python 3.14, psycopg 3.3.9, redis-py 8.1.0, pydantic 2.14.3, Redis 8.10.3, TimescaleDB 2.32.0-pg17, pytest 9.2.0, docker compose. No new dependency (stdlib `statistics`, `faulthandler`, `zlib`, `uuid`).
**Spec:** `Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md` (binding) · `docs/design.md` (§2.1–2.3, 2.8, §3, §3.2, §3.3, §3.6, §3.7, §5.3 day-2 row) · `docs/requirements.md` (test names) · `docs/assumptions.md`.

## Global Constraints

- **Branches.** Track 0 commits on `gate3a-ingest-alarms`. Each later track works on `gate3a/<track>` (`gate3a/alarms`, `gate3a/ingest`, `gate3a/sim`, `gate3a/reload`, `gate3a/integration`) in its own git worktree, branched from `gate3a-ingest-alarms` **after Track 0 is merged**, and merges back with `git merge --no-ff` in the order of "Parallel tracks". Never commit to `main`. Push with `git push -u origin <branch>`.
- **One owner per file.** After Track 0, every file has exactly one owning track (tables below). A change to a Track 0 contract file needs a coordinator ruling and lands as a Track 0 patch on `gate3a-ingest-alarms`, which tracks then merge in.
- **One compose project per worktree.** Each worktree sets `COMPOSE_PROJECT_NAME=nyx-<track>` in its shell and shifts every host port in its own `.env` (R22 keys `DB_HOST_PORT REDIS_HOST_PORT MINIO_HOST_PORT SIM_HOST_PORT API_HOST_PORT`) by +100 × track index (alarms 1, ingest 2, sim 3, reload 4, integration 5). Never stop, reset or `down` another project's containers (the owner's `nyx` stack is shared).
- **Every change ships with its test in the same commit** (D-ENG-14/15); a bug fix starts with a failing test; a real bug a test catches is appended to `docs/bug-log.md` (D-ENG-19). Untested behaviour is an Important review finding, not Minor (R27). No `...` test bodies (R13): a test line written below as `def test_x(): ...  # comment` is a specification by comment, and the implementer writes real assertions for it; helper names in the test sketches that no Interfaces block defines (`claim`, `produce_values`, `hang`, …) are local to that test module or to the track's own support file. No assertion that only restates a DB constraint, and tests create their own entries (R14).
- **Coverage gate:** `uv run pytest --cov=gs --cov-fail-under=80` over `tests/unit tests/integration tests/meta` (R20). Also green: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src tests scripts`, `make gen && git diff --exit-code -- interfaces ui/src/api`.
- **Ponytail.** Every implementer loads the `ponytail:ponytail` skill before writing code and follows it (minimal correct code, reuse, stdlib first, `ponytail:` comments on deliberate simplifications); it never removes a required test, validation, data-loss handling or security measure (R25).
- **Commits:** conventional subject `<type>: <description>` (`feat fix refactor docs test chore perf ci`), blank line, optional body. **No `Co-Authored-By` trailer** (R21; the repo's commit-msg hook strips it anyway).
- **No time limit, no cuts.** Every decision the day-2 row names is built (design §5 scope rule). A finding that a design value fails (e.g. nominal noise raises an alarm) is reported to the coordinator, never "fixed" by changing a design value.
- Gate 2 rulings that bind: test helpers live in `tests/support/` (R2/R23; DB helpers in `pg.py`); stream entry ids are decoded to `str` once where read (R3); each new compose service ships `docker compose config -q` + a system test that brings it up with `--wait` and checks health (R16); host ports env-overridable and bound to `127.0.0.1` (R22); app image runs as non-root (R24); `serve_health` status callback returns `(status, checks)`, status ∈ `ok · degraded · down` (R26); every Redis client has redis-py's retry **off** (`Retry(NoBackoff(), 0)`), and env config is validated at startup with `gs.common.config.require*()` and fails fast (R28).
- Unchanged from Gate 2: pinned versions and images, `iso_z()` for every time leaving a process (D-ENG-18), config from env only, no host literal in `src/`, compose rules (D-ENG-03), one `data` field per stream entry, `StrictModel` interface models, integration tests connect as the service's ACL user and use `admin` only for setup, test vehicles from `tests.support.env.vid()`.
- **No DDL in this plan.** Every table it writes exists in `migrations/02-tables.sql` (§3.2). If a track finds it needs DDL, it stops and asks; a ruled-in change goes into a new file `migrations/05-gate3a-<track>.sql` and the workflow is `make reset` (D-ENG-12).
- **Design values (verbatim; tasks repeat the ones they use).** `BLOCK 1000`; Redis backoff 0.5 s doubling to 5 s; COUNT 100 for new entries; poison: `times_delivered` **> 3** → DLQ + `POISON`; data error = SQLSTATE class `22` or `23` or a Python processing exception, 3 in-process retries; every other class operational (exit, after `XCLAIM tm:<v> ingest <me> 0 <ids…> RETRYCOUNT 0 JUSTID`); lock `pg_advisory_lock(hashtext('tm:'‖vehicle))`; non-home start delay 2 s; hand-back check every 5 s; steal tick 5 s, `STEAL_IDLE_MS` default 30 000 (CI 3 000), no steal within that time of the worker's own Redis reconnect; reclaim every 5 s with `RECLAIM_IDLE_MS` default 30 000 (CI 3 000); `hb:veh:<v>` = `1` `EX 3` every pass; watchdog `faulthandler.dump_traceback_later(30, exit=True)` re-armed by a 5 s task; data-STALE / LOS at a `t_onboard` gap > 2 s or 2 s without a frame; FROZEN when all analog values are identical for ≥ 2 s of `t_onboard`; `RESET_SEQ_MAX = 50`, nominal period 0.1 s, GAP tolerance `max(2, d // 10)`; on-limit = more severe band; deadband `d` = `hysteresis_pct` % of `red_high − red_low` or `hysteresis_abs`; floor `d ≥ 1.5 × max(1 % of span, 1 % of |limit|)`; persistence N = 3; ROC = least-squares slope over `window_s` = 1 s of `t_onboard`, ≥ 80 % of expected samples; mission poll 1 s, applied after two equal consecutive polls, rejected set re-validated every 10 s; leak −0.4 kPa/s, overheat +0.5 °C/s; σ cabin pressure 0.02 ≤ 0.027.

## Review Focus

1. **Shipped limits that sit exactly on the deadband floor.** `cabin_temp_c` (3 % of 15 = 0.45 = 1.5 × 0.30) and `bus_voltage_v` (6 % of 8 = 0.48 = 1.5 × 0.32) are equal to the floor in exact arithmetic and differ in the last bit in floats; the shipped `mission/` must still load (Task 3 `test_shipped_set_meets_floor_exactly`).
2. **A poison frame among healthy neighbours.** Every re-read (`XREADGROUP 0`, `XAUTOCLAIM`) raises `times_delivered` of every entry it returns, so a crash-every-time frame in a 100-entry batch would push 99 healthy frames past 3 deliveries (design gap, see Task 9). Only the bad frame may reach `tm:dlq` (Task 9 `test_poison_neighbours_not_dlqd`).
3. **A vehicle whose `hashtext('tm:'‖v)` is negative.** `pg_locks` shows a bigint advisory key split into `classid`/`objid`; hand-back and steal must still find the holder and the waiter (Task 10 `test_lock_match_negative_hashtext`).
4. **SIGTERM while a standby task is blocked in `pg_advisory_lock`.** The worker must still exit 0 inside the 5 s grace period (Task 10 `test_sigterm_while_waiting_on_lock`).
5. **Ten minutes of nominal noise.** At §3.3 σ no parameter may raise an alarm, incl. the cabin-pressure ROC check (Task 4 `test_nominal_noise_no_alarm`); a failure is a design finding, reported, not tuned away.

## Parallel tracks

**Track 0 – shared contracts** runs first and alone (Tasks 1–2) and is merged into `gate3a-ingest-alarms` before any other track branches. Then Tracks A, B, C and D run in parallel worktrees. Merge order back into `gate3a-ingest-alarms`: **A → C → D → B**, each merge followed by the full gate (tests, ruff, mypy, `make gen` clean) on the merged branch. Track Z branches after B is merged and runs alone.

| Track | Tasks (in order) | Consumes | Produces for later tracks |
| --- | --- | --- | --- |
| 0 – shared contracts | 1, 2 | Gate 2 code | lint rules, `VEHICLE_ID_PATTERN`, `gs.ingest.events` (event kinds, keys, `insert_events`, `insert_set`), `gs.alarms.contract` (`Level`, `Transition`, `OpenAlarm`), `MissionDb` file texts, compose `ingest-2` + env passthrough, shared test helpers |
| A – alarm engine | 3, 4, 5 | Track 0 contract types | `gs.alarms.checks` registry, `gs.alarms.engine` (`compile_set`, `initial_state`, `step`, `ack`, `switch_set`), `gs.alarms.state` (`dump`, `load`, `rebuild`, `shape_version`), floor rule in `missiondb.load` |
| B – ingest depth | 6, 7, 8, 9, 10, 11, 12, 13 | Track 0 events, compose env, helpers | tracker, link/FROZEN, `VehicleState`/`fold`, store writers, error classes, ownership, liveness, hooks |
| C – simulator faults | 14 | Track 0 | `POST/DELETE /sim/fault`, faults, `tests.support.sim.post_fault` |
| D – mission-db reload | 15 | Track 0 events/`insert_set` | `gs.ingest.mission` (`validate_set`, `start_set`, `MissionWatcher`) |
| Z – integration | 16, 17, 18 | all | engine + reload wired into the worker; cross-track system tests |

**File ownership after Track 0** (a path not listed here and not created by a task is unowned and must not be edited without a ruling):

| Track | Owns |
| --- | --- |
| 0 (frozen contracts) | `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `docker-compose.yml`, `Makefile`, `.env.example`, `migrations/**`, `src/gs/common/config.py`, `src/gs/common/time.py`, `src/gs/common/interfaces/**`, `src/gs/ingest/events.py`, `src/gs/alarms/__init__.py`, `src/gs/alarms/contract.py`, `tests/support/{__init__,env,pg,compose,redisx,mission,http,sse,s3,gen}.py`, `tests/meta/test_compose.py`, `tests/meta/test_lint_rules.py`, `tests/unit/test_config.py`, `tests/unit/test_events.py`, `tests/integration/test_events_db.py`, `tests/integration/test_ingest_service.py`, `tests/system/test_shutdown.py`, `tests/system/conftest.py` |
| A – alarms | `src/gs/alarms/checks/**`, `src/gs/alarms/engine.py`, `src/gs/alarms/state.py`, `src/gs/common/missiondb.py`, `interfaces/**`, `ui/src/api/schema.d.ts`, `tests/support/alarms.py`, `tests/unit/test_checks_limit.py`, `tests/unit/test_checks_roc.py`, `tests/unit/test_check_registry.py`, `tests/unit/test_limits_config.py`, `tests/unit/test_engine.py`, `tests/unit/test_alarm_lifecycle.py`, `tests/unit/test_engine_state.py`, `tests/unit/test_missiondb_schema.py`, `tests/meta/test_add_parameter.py` |
| B – ingest | `src/gs/ingest/{__init__,main,worker,store,validate,seq,link,fold,errors,owner,liveness}.py`, `src/gs/common/hooks.py`, `tests/support/ingest.py`, `tests/unit/test_seq_tracker.py`, `tests/unit/test_link.py`, `tests/unit/test_frozen_detector.py`, `tests/unit/test_fold.py`, `tests/unit/test_pg_error_class.py`, `tests/unit/test_steal_decision.py`, `tests/unit/test_ingest_status.py`, `tests/unit/test_ingest_settings.py`, `tests/unit/test_hooks.py`, `tests/unit/test_classify.py`, `tests/integration/test_ingest_store.py`, `tests/integration/test_ingest_main.py`, `tests/integration/test_dlq.py`, `tests/integration/test_frame_txn.py`, `tests/integration/test_ownership.py`, `tests/integration/test_liveness.py`, `tests/integration/test_hooks_ingest.py`, `tests/system/test_ownership.py`, `tests/system/test_dlq_crash.py`, `tests/system/test_db_errors.py` |
| C – sim | `src/gs/simulator/**`, `src/gs/common/health.py`, `tests/support/sim.py`, `tests/unit/test_sim_*.py`, `tests/unit/test_serve_health.py`, `tests/integration/test_simulator.py`, `tests/integration/test_sim_fault_api.py`, `tests/integration/test_stream_trim.py`, `tests/system/test_sim_outage.py` |
| D – reload | `src/gs/ingest/mission.py`, `tests/support/missionfiles.py`, `tests/unit/test_mission_watch.py`, `tests/integration/test_mission_start.py` |
| Z – integration | anything, after B is merged; new: `tests/integration/test_alarm_txn.py`, `tests/integration/test_limits_reload.py`, `tests/system/test_link_events.py`, `tests/system/test_faults.py`, `tests/system/test_seq_reset.py`, `tests/system/test_redis_restart.py`, `tests/system/test_alarm_keys.py`, docs |

**Cross-track contract (decided now; Track 0 encodes what is code).**

| Contract | Value |
| --- | --- |
| Redis keys | `tm:<v>` (group `ingest`), `tm:dlq`, `ui:events` (day 2 publishes `tm` only; `alarm`/`link`/`resync` messages are the day-3 row's models), `hb:veh:<v>` = `1` `EX 3` |
| Event kinds and keys (`events.key`, `iso_z` epoch) | `GAP/<v>/<epoch>/<from_seq>` · `SEQ_JUMP/<v>/<epoch>/<from_seq>` · `SEQ_RESET/<v>/<new_epoch>` · `SEQ_ANOMALY/<v>/<new_epoch>` · `LOS/<v>/<epoch>/<last_seq>` · `AOS/<v>/<epoch>/<last_seq>` (same suffix as its LOS) · `FROZEN/<v>/<epoch>/<first_seq>` · `POISON/<stream>/<entry_id>` · `LIMITS_APPLIED/<v>/<epoch>/<first_seq>` · `LIMITS_REJECTED/<set_sha256>` (vehicle NULL) · `ENGINE_STATE_RESET/<v>/<epoch>/<last_seq>` |
| Alarm transition key / id | `<v>/<param>/<epoch>/<trigger_seq>/<kind>`; `alarm_id = uuid5(NAMESPACE_URL, "nyx-alarm/" + <key of the RAISE that opened it>)` |
| `engine_state.state` jsonb | `{"link": …, "frozen": …, "engine": …}`; `schema_version` = `gs.alarms.state.shape_version(...)` (Track A) over every persisted dataclass; until Task 16 the `engine` part is absent |
| Ingest env (compose, Track 0) | `INGEST_HOMES=ingest-1=NYX-TA1,ingest-2=NYX-TA2` · `STEAL_IDLE_MS=${STEAL_IDLE_MS:-30000}` · `RECLAIM_IDLE_MS=${RECLAIM_IDLE_MS:-30000}` · `GS_TEST_HOOKS=${GS_TEST_HOOKS:-0}` · `CRASH_AT=${CRASH_AT:-}` · `STOP_AT=${STOP_AT:-}` · `HANG_AT=${HANG_AT:-}` |
| Engine API (Track A → Z) | signatures in Tasks 4–5 Interfaces |
| Reload API (Track D → Z) | signatures in Task 15 Interfaces |
| Ingest API (Track B → Z) | `VehicleState`, `Outcome`, `fold`, store writers, `Ctx` in Tasks 8–11 Interfaces |

**Track Z cross-track tests (only runnable after all merges):** `integration/test_alarm_txn.py` (engine rows in the frame transaction, redelivery raises nothing twice, AOS and open alarms survive a takeover), `integration/test_limits_reload.py` (ALM-08 end to end incl. `::test_commands_only_edit_is_new_version`, `::test_set_applied_after_two_equal_polls`, `::test_unmigrated_param_at_start_falls_back`), `system/test_alarm_keys.py::test_kill_owner_keeps_alarm_keys[…]` (ING-07 alarm half, needs sim `step`), `system/test_link_events.py` (ATP-04: dropout → GAP 100 ± 1, LOS, AOS), `system/test_faults.py` (ATP-05: duplicate, malformed, seq_jump, freeze, live leak ROC → RED), `system/test_seq_reset.py` (ING-05), `system/test_redis_restart.py` (ENG-02).

**Not in this plan (day-3 row or later):** commanding and the tracker (D-CMD-*, D-SIM-06/10, so the `pause N` / `hold_exec N` faults, whose effect is on the command path), the ACK path (D-ALM-14: `alarm_ack_request` applied first in the frame transaction), `tc`/`tcack`/`alarm`/`cmd`/`link`/`ground`/`resync` interface models and their `ui:events` messages, D-ING-27, console and banners, login hardening, sessions/report, the API's own mission-db poll and `LIMITS_RELOADED` audit, D-ALM-15/16, the except-coverage meta test (Gate 4), backfill / `NYX-TA9` / `HOT_RETENTION_H` / archive hooks (Gate 4), README sections (Gate 4 docs).

---

## Track 0 – shared contracts

### Task 1: Error-handler lint, one vehicle pattern, digest negative control

**Files:**
- Modify: `pyproject.toml` (`[tool.ruff.lint]`), `src/gs/api/app.py:121`, `src/gs/common/interfaces/base.py`, `src/gs/common/config.py`, `tests/unit/test_config.py`, `tests/meta/test_add_parameter.py`
- Create: `tests/meta/test_lint_rules.py`

**Interfaces:**
- Consumes: `gs.common.config.vehicle_list`, `gs.common.interfaces.base.VehicleId`, `tests/meta/test_add_parameter.py::tree_digest`.
- Produces: `gs.common.interfaces.base.VEHICLE_ID_PATTERN = r"^[A-Z0-9-]{1,32}$"` (`VehicleId` uses it; `config.VEHICLE_ID = re.compile(VEHICLE_ID_PATTERN)`, the duplicate literal is gone); `tree_digest(root: Path, *dirs: str) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/meta/test_lint_rules.py  (D-ENG-21 part 1: lint on from day 2)
BAD = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n# TODO later\n"
def test_ruff_flags_blind_except_pass_and_todo(tmp_path):
    p = tmp_path / "src" / "bad.py"; p.parent.mkdir(); p.write_text(BAD)
    out = subprocess.run(["uv", "run", "ruff", "check", "--config", str(REPO / "pyproject.toml"),
                          "--output-format", "json", str(p)], capture_output=True, text=True)
    codes = {d["code"] for d in json.loads(out.stdout)}
    assert {"BLE001", "S110"} <= codes and any(c.startswith("FIX") for c in codes)
def test_tests_are_exempt_from_fix(tmp_path):     # FIX applies to src/ only
    p = tmp_path / "tests" / "t.py"; p.parent.mkdir(); p.write_text("# TODO x\n")
    out = subprocess.run([... same command ..., str(p)], capture_output=True, text=True)
    assert json.loads(out.stdout) == []

# tests/unit/test_config.py (add)
@pytest.mark.parametrize("name", ["NYX-TA1", "A", "x", "", "NYX_TA1", "A" * 33, "A" * 32])
def test_vehicle_list_agrees_with_model(name):
    model_ok = _accepts(TypeAdapter(VehicleId), name)
    try: vehicle_list(name or ","); cfg_ok = True
    except ConfigError: cfg_ok = False
    assert cfg_ok == model_ok

# tests/meta/test_add_parameter.py (add; Gate 2 Task 13 minor)
def test_tree_digest_detects_edits(tmp_path):
    shutil.copytree(REPO / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    before = tree_digest(tmp_path, "src")
    f = tmp_path / "src/gs/common/time.py"; f.write_text(f.read_text() + "\n")
    assert tree_digest(tmp_path, "src") != before
    f.write_text(f.read_text()[:-1]); assert tree_digest(tmp_path, "src") == before
    (tmp_path / "src/new.py").write_text(""); assert tree_digest(tmp_path, "src") != before
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/meta/test_lint_rules.py tests/unit/test_config.py tests/meta/test_add_parameter.py -q`
Expected: lint test FAIL (codes missing), digest test FAIL (`tree_digest()` takes no root), vehicle test FAIL to import `VEHICLE_ID_PATTERN` if the test imports it.

- [ ] **Step 3: Implement**
`select = ["E", "F", "I", "UP", "B", "BLE001", "S110", "FIX"]` with `[tool.ruff.lint.per-file-ignores] "tests/**" = ["FIX"]`. The one existing broad catch, `src/gs/api/app.py:121`, gets `# noqa: BLE001  any failure means that dependency is down`. `tree_digest(root, *dirs)` hashes relative paths from `root`; `test_add_parameter` calls `tree_digest(REPO, "src", "interfaces")`.

- [ ] **Step 4: Run the gate**
Run: `uv run pytest --cov=gs --cov-fail-under=80 && uv run ruff check && uv run ruff format --check && uv run mypy --strict src tests scripts && make gen && git diff --exit-code -- interfaces ui/src/api`
Expected: all green, no generated diff (the pattern string is unchanged).

- [ ] **Step 5: Commit**
`git commit -m "chore: lint blind excepts and TODOs, share the vehicle id pattern, mutation-test the tree digest"`

---

### Task 2: Shared contracts – events, alarm types, mission texts, ingest-2, helpers

**Files:**
- Create: `src/gs/ingest/events.py`, `src/gs/alarms/__init__.py`, `src/gs/alarms/contract.py`, `tests/unit/test_events.py`, `tests/integration/test_events_db.py`
- Modify: `src/gs/common/missiondb.py` (`MissionDb` keeps the file texts), `docker-compose.yml`, `tests/meta/test_compose.py`, `tests/integration/test_ingest_service.py`, `tests/system/test_shutdown.py`, `tests/support/pg.py`, `tests/support/compose.py`, `tests/system/conftest.py`

**Interfaces:**
- Consumes: `gs.common.time.iso_z`, `gs.common.missiondb.load`, `tests.support.pg.app_dsn`.
- Produces `gs.ingest.events`:
  - `@dataclass(frozen=True) class GroundEvent: key: str; vehicle: str | None; kind: EventKind; t_onboard: datetime | None; details: Mapping[str, Any]` with `EventKind = Literal["GAP","SEQ_JUMP","SEQ_RESET","SEQ_ANOMALY","LOS","AOS","FROZEN","POISON","LIMITS_APPLIED","LIMITS_REJECTED","ENGINE_STATE_RESET"]`.
  - Key builders (formats in the contract table): `seq_key(kind, vehicle, epoch, from_seq=None) -> str`, `link_key(kind: Literal["LOS","AOS"], vehicle, epoch, last_seq) -> str`, `frozen_key(vehicle, epoch, first_seq)`, `poison_key(stream, entry_id)`, `applied_key(vehicle, epoch, first_seq)`, `rejected_key(set_sha256)`, `reset_key(vehicle, epoch, last_seq)`, `transition_key(vehicle, param, epoch, trigger_seq, kind) -> str`, `alarm_id_for(raise_key: str) -> UUID`.
  - `async def insert_events(conn: psycopg.AsyncConnection, events: Sequence[GroundEvent]) -> int` — `INSERT INTO events (key, vehicle, kind, t_onboard, details) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (key) DO NOTHING`, inside the caller's transaction; returns rows inserted.
  - `async def insert_set(conn, mdb: MissionDb) -> None` — `INSERT INTO limits_versions (set_sha256, limits_sha256, commands_sha256, parameters_sha256, limits_yaml, commands_yaml, parameters_yaml) VALUES (…) ON CONFLICT DO NOTHING`.
- Produces `gs.alarms.contract`: `class Level(IntEnum): NOMINAL = 0; YELLOW = 1; RED = 2`; `AlarmState = Literal["UNACK","ACKED","RTN_UNACK","CLEARED"]`; `TransitionKind = Literal["RAISE","ESCALATE","DEESCALATE","RTN","ACK","CLEAR"]`; `@dataclass(frozen=True) class Transition: key: str; alarm_id: UUID; vehicle: str; param: str; kind: TransitionKind; check_kind: str | None; level: str | None; severity: Level; alarm_state: AlarmState; acked_sev: Level | None; trigger_check: str; trigger_seq: int | None; trigger_t: datetime | None; alert: bool; by_user: str; note: str | None; opened_at: datetime; closed_at: datetime | None`; `@dataclass(frozen=True) class OpenAlarm: alarm_id: UUID; param: str; state: AlarmState; severity: Level; acked_sev: Level | None; trigger_check: str; opened_at: datetime`.
- `MissionDb` gains `limits_yaml: str`, `commands_yaml: str`, `parameters_yaml: str` (the bytes decoded UTF-8; hashes unchanged).
- Compose: service `ingest-2` = `ingest-1` with `CONSUMER_NAME: ingest-2`; both get the ingest env of the contract table. **Transient:** until Track B merges, two lock-less workers share both vehicles at frame level (no loss, storage is idempotent; order across workers is not kept); Track B Task 10 ends it.
- `tests/support/pg.py` adds: `events_for(vehicle: str, kind: str | None = None, since: datetime | None = None) -> list[dict[str, Any]]`, `seqs_since(vehicle, since) -> list[int]` (by `t_onboard`), `out_of_order(vehicle, since) -> int` (inversions of `seq` ordered by `t_received`), `lock_holder(vehicle, dsn=app_dsn("gs")) -> str | None` (`application_name` of the granted advisory lock on `hashtext('tm:'‖v)`), `alarm_events_for(vehicle, since=None) -> list[dict[str, Any]]`, `gapless(seqs: Sequence[int]) -> bool` (each step +1 mod 2³², or a drop to ≤ 50 = a reset). `tests/support/redisx.py` adds `dlq_since(stream: str, since: datetime) -> list[str]` and `pending_of(vehicle, consumer) -> int`. `tests/support/env.py` adds `wait_until(cond, timeout: float) -> None` (sync twin of `until`) and `now() -> datetime` (UTC).
- `tests/support/compose.py` adds `recreate(services: Sequence[str], env: Mapping[str, str] | None = None, wait: bool = True) -> None` (`docker compose up -d --no-deps [--wait] <svcs>` with `env` over `os.environ`) and `restart_count(svc) -> int` (`docker inspect -f {{.RestartCount}}`).
- `tests/system/conftest.py` adds fixture `fast_ingest` (module scope): `recreate(["ingest-1","ingest-2"], {"GS_TEST_HOOKS": "1", "STEAL_IDLE_MS": "3000", "RECLAIM_IDLE_MS": "3000"})`, teardown `recreate(["ingest-1","ingest-2"])`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_events.py
E = datetime(2026, 11, 14, 10, tzinfo=UTC)
def test_keys_are_the_contract():
    assert seq_key("GAP", "NYX-TA1", E, 50) == "GAP/NYX-TA1/2026-11-14T10:00:00.000000Z/50"
    assert seq_key("SEQ_RESET", "NYX-TA1", E) == "SEQ_RESET/NYX-TA1/2026-11-14T10:00:00.000000Z"
    assert link_key("AOS", "NYX-TA1", E, 7)[4:] == link_key("LOS", "NYX-TA1", E, 7)[4:]
    assert poison_key("tm:NYX-TA1", "17-0") == "POISON/tm:NYX-TA1/17-0"
    assert rejected_key("ab" * 32) == "LIMITS_REJECTED/" + "ab" * 32
    assert transition_key("NYX-TA1", "cabin_temp_c", E, 9, "RAISE").endswith("/9/RAISE")
def test_alarm_id_is_deterministic():
    k = transition_key("NYX-TA1", "p", E, 3, "RAISE")
    assert alarm_id_for(k) == alarm_id_for(k) != alarm_id_for(k + "x")
def test_level_order(): assert Level.NOMINAL < Level.YELLOW < Level.RED
def test_mission_texts_hash_to_file_hashes():
    db = load_dir(MISSION_DIR)
    assert hashlib.sha256(db.limits_yaml.encode()).hexdigest() == db.limits_sha256

# tests/integration/test_events_db.py
async def test_insert_events_idempotent():
    v = vid(); ev = GroundEvent(seq_key("GAP", v, E, 1), v, "GAP", E, {"lost": 3})
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as c:
        async with c.transaction(): assert await insert_events(c, [ev, ev]) == 1
        async with c.transaction(): assert await insert_events(c, [ev]) == 0
    assert [e["details"]["lost"] for e in events_for(v, "GAP")] == [3]
async def test_insert_set_once():
    db = load_dir(MISSION_DIR)
    async with await psycopg.AsyncConnection.connect(app_dsn("gs")) as c:
        await insert_set(c, db); await insert_set(c, db)
        row = await (await c.execute("select limits_yaml from limits_versions where set_sha256=%s",
                                     (db.set_sha256,))).fetchone()
    assert row == (db.limits_yaml,)

# tests/meta/test_compose.py: APP gains "ingest-2"; ENV/USES gain "ingest-2" (same as ingest-1);
# ENV["ingest-1"] and ENV["ingest-2"] gain {"INGEST_HOMES","STEAL_IDLE_MS","RECLAIM_IDLE_MS",
# "GS_TEST_HOOKS","CRASH_AT","STOP_AT","HANG_AT"}
def test_ingest_defaults():   # design defaults when the shell sets nothing
    for svc in ("ingest-1", "ingest-2"):
        env = services[svc]["environment"]
        assert (env["STEAL_IDLE_MS"], env["RECLAIM_IDLE_MS"], env["GS_TEST_HOOKS"]) == ("30000", "30000", "0")
        assert env["INGEST_HOMES"] == "ingest-1=NYX-TA1,ingest-2=NYX-TA2" and env["CONSUMER_NAME"] == svc

# tests/integration/test_ingest_service.py (R16 smoke): fixture brings up "ingest-1", "ingest-2";
# new test asserts inspect_health("ingest-2") == "healthy".
# tests/system/test_shutdown.py: parametrize adds "ingest-2".
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/unit/test_events.py tests/integration/test_events_db.py tests/meta/test_compose.py -q`
Expected: import errors (`gs.ingest.events`, `gs.alarms.contract`), compose test FAIL (no `ingest-2`).

- [ ] **Step 3: Implement the contract modules and compose changes as specified in Interfaces**
`transition_key`/`seq_key` format the epoch with `iso_z`. `alarm_id_for` = `uuid.uuid5(uuid.NAMESPACE_URL, "nyx-alarm/" + key)`. `details` is written with `psycopg.types.json.Jsonb`. In `missiondb.load` keep `raw.decode()` per file next to the hash (the loader already rejects non-UTF-8).

- [ ] **Step 4: Run the gate (Task 1 Step 4 command) plus** `docker compose config -q && uv run pytest tests/integration/test_ingest_service.py -q && make up && uv run pytest tests/system/test_shutdown.py -q`
Expected: all pass; `ingest-2` healthy; both workers stop inside 5 s.

- [ ] **Step 5: Commit, then merge Track 0**
`git commit -m "feat: add gate 3a shared contracts (event keys, alarm types, mission texts, ingest-2)"` — then the coordinator creates the track branches from this commit.

---

## Track A – alarm engine

### Task 3: Check registry, limit and ROC checks, deadband floor

**Files:**
- Create: `src/gs/alarms/checks/__init__.py`, `src/gs/alarms/checks/limit.py`, `src/gs/alarms/checks/roc.py`, `tests/unit/test_checks_limit.py`, `tests/unit/test_checks_roc.py`, `tests/unit/test_check_registry.py`, `tests/unit/test_limits_config.py`
- Modify: `src/gs/common/missiondb.py` (check configs come from the registry; floor rule in `load`), `tests/unit/test_missiondb_schema.py` (imports), `interfaces/schemas/mission/limits.schema.json` and `interfaces/openapi.json` / `ui/src/api/schema.d.ts` only if `make gen` changes them

**Interfaces:**
- Consumes: `gs.alarms.contract.Level`; `gs.common.missiondb.LimitCheck`, `RocCheck` (moved, re-exported).
- Produces in `gs.alarms.checks`:
  - `@dataclass(frozen=True) class Sample: seq: int; t: float; value: float; period_s: float` (`t` = `t_onboard` epoch seconds).
  - `@dataclass(frozen=True) class Reading: level: Level; side: Literal["LOW","HIGH","RISING","FALLING"] | None`.
  - `class CheckConfig(BaseModel)` (strict, frozen, `extra="forbid"`, `allow_inf_nan=False`; fields `type: str`, `persistence: int | None = Field(None, ge=1)`).
  - `class Check(Protocol)`: `kind: ClassVar[str]`, `Config: ClassVar[type[CheckConfig]]`, `State: ClassVar[type]` (frozen dataclass of JSON-native fields), `__init__(self, cfg: CheckConfig)`, `initial(self) -> object`, `step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]` (`None` = no opinion).
  - `CHECKS: dict[str, type[Check]]`, `register(cls: type[C]) -> type[C]`, `load_all() -> None` (imports every module of the package via `pkgutil.iter_modules(__path__)`; called at package import).
  - `limit.py`: `class LimitCheck(CheckConfig)` (the Gate 2 model and validator, moved verbatim, `type: Literal["limit"]`), `deadband(cfg: LimitCheck) -> float`, `floor(cfg: LimitCheck) -> float`, `@register class Limit` (kind `"limit"`, stateless `State`).
  - `roc.py`: `class RocCheck(CheckConfig)` (moved verbatim), `@register class Roc` (kind `"roc"`, `State(t: tuple[float, ...], v: tuple[float, ...])`).
  - `gs.common.missiondb`: `LimitEntry.checks` items are validated by `CHECKS[item["type"]].Config` (unknown type → error naming it); the exported JSON schema is a discriminated union built from `CHECKS` sorted by kind; `load()` raises `MissionDbError("limits.yaml", "<param>: deadband <d> below floor <f>")` when any limit check fails the floor. `LimitCheck`, `RocCheck` stay importable from `gs.common.missiondb`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_checks_limit.py
def run(cfg, values, start=Level.NOMINAL):   # N = 1: the raw reading is the new current level
    chk, cur, st = Limit(cfg), start, None; st = chk.initial(); out = []
    for i, v in enumerate(values):
        st, r = chk.step(st, Sample(i, i * 0.1, v, 0.1), cur); cur = r.level; out.append(r)
    return out
CP2 = LimitCheck(type="limit", red_low=95.0, yellow_low=98.0, yellow_high=104.0, red_high=106.0,
                 hysteresis_pct=2.0)       # R8's table assumes 2 % (d = 0.22); built directly, not via load
BOUNDARY = [  # R8 §1, 16 rows: (value, start level, expected)
    (94.99, N, RED), (95.00, N, RED), (95.01, N, YELLOW), (97.99, N, YELLOW), (98.00, N, YELLOW),
    (98.01, N, NOMINAL), (98.21, YELLOW, YELLOW), (98.23, YELLOW, NOMINAL), (103.99, N, NOMINAL),
    (104.00, N, YELLOW), (104.01, N, YELLOW), (105.99, N, YELLOW), (106.00, N, RED),
    (106.01, N, RED), (95.21, RED, RED), (95.23, RED, YELLOW)]
@pytest.mark.parametrize("v,start,want", BOUNDARY)
def test_boundaries(v, start, want): assert run(CP2, [v], start)[0].level == want
def test_shipped_deadband_rows():          # 15 % of 11 = 1.65 (§3.4: DEESCALATE above 96.65, RTN above 99.65)
    cfg = shipped("cabin_pressure_kpa", "limit")
    assert run(cfg, [96.64], Level.RED)[0].level == Level.RED
    assert run(cfg, [96.66], Level.RED)[0].level == Level.YELLOW
    assert run(cfg, [99.64], Level.YELLOW)[0].level == Level.YELLOW
    assert run(cfg, [99.66], Level.YELLOW)[0].level == Level.NOMINAL
def test_side(): assert [r.side for r in run(CP2, [94.0, 107.0, 100.0])] == ["LOW", "HIGH", None]
def test_one_sided_abs(): ...  # bus_current_a: 8.0 → YELLOW HIGH; 7.81 from YELLOW stays YELLOW; 7.79 → NOMINAL

# tests/unit/test_checks_roc.py
def test_leak_alarms_before_yellow():     # ALM-04: 0.4 kPa/s from 101.3, σ 0.02, seed 1
    first_roc = first_seq(roc_levels(leak_series()), Level.YELLOW)
    first_limit = first_seq(limit_levels(leak_series()), Level.YELLOW)
    assert first_roc is not None and first_limit is not None
    assert (first_limit - first_roc) * 0.1 >= 5.0          # §3.4: ≈ 1.3 s vs 8.25 s
def test_falling_only_ignores_rise(): assert all(r.level == Level.NOMINAL for r in roc_levels(ramp(+0.5)))
def test_too_few_samples_no_opinion():   # < 80 % of window_s / period_s in the window
    assert roc_readings([(0.0, 101.3), (0.5, 100.0)], period_s=0.1)[-1] is None
def test_rising_and_both_directions(): ...  # rising cfg alarms on +0.5/s; both alarms on ±0.5/s; side RISING/FALLING

# tests/unit/test_check_registry.py
def test_shipped_kinds(): assert set(CHECKS) == {"limit", "roc"}
def test_new_check_file_is_registered(tmp_path, monkeypatch):   # ALM-07
    (tmp_path / "flat.py").write_text(FLAT_CHECK_SOURCE)          # one class with @register, kind "flat"
    monkeypatch.setattr(checks_pkg, "__path__", [*checks_pkg.__path__, str(tmp_path)])
    load_all()
    assert "flat" in CHECKS
    entry = LimitEntry.model_validate({"checks": [{"type": "flat", "level": 2}]})
    assert type(entry.checks[0]) is CHECKS["flat"].Config
def test_unknown_type_rejected():
    with pytest.raises(ValidationError, match="unknown check type 'nope'"):
        LimitEntry.model_validate({"checks": [{"type": "nope"}]})

# tests/unit/test_limits_config.py
def test_deadband_below_floor_rejected():   # cabin pressure at 10 %: 1.1 < 1.5 x 1.06
    with pytest.raises(MissionDbError, match="cabin_pressure_kpa: deadband"):
        load_with(limits_edit("hysteresis_pct: 15", "hysteresis_pct: 10"))
def test_shipped_set_meets_floor_exactly():   # Review Focus 1
    db = load_dir(MISSION_DIR)
    for p in ("cabin_temp_c", "bus_voltage_v"):
        cfg = db.limits[p].checks[0]; assert math.isclose(deadband(cfg), floor(cfg), rel_tol=1e-9)
def test_floor_values():
    assert floor(shipped("cabin_pressure_kpa", "limit")) == pytest.approx(1.59)
    assert floor(shipped("bus_current_a", "limit")) == pytest.approx(0.15)   # one-sided: 1 % of |limit| only
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/unit/test_checks_limit.py tests/unit/test_checks_roc.py tests/unit/test_check_registry.py tests/unit/test_limits_config.py -q`
Expected: `ModuleNotFoundError: gs.alarms.checks`.

- [ ] **Step 3: Implement the registry, `Limit`, `Roc`, `deadband`, `floor` and the missiondb changes**
`Limit.step` (stateless): base level = RED if `v ≤ red_low` or `v ≥ red_high`, YELLOW if `v ≤ yellow_low` or `v ≥ yellow_high`, else NOMINAL (absent bounds skipped); hysteresis only delays a move to a less severe band: the result is the highest level `L ≤ current` such that `v ≤ low_L + d` or `v ≥ high_L − d` for a bound of band `L`, or the base level if that is higher. Side = the side of the bound that decided it. `deadband` = `hysteresis_pct / 100 × (red_high − red_low)` or `hysteresis_abs`. `floor` = `1.5 × max(1 % of span (two-sided only), 1 % of |each present limit|)`; the load check passes when `d ≥ floor × (1 − 1e-9)` (Review Focus 1). `Roc.step`: append `(s.t, s.value)`, keep samples with `t ≥ s.t − window_s`; fewer than `0.8 × window_s / s.period_s` → `(state, None)`; else `slope = statistics.linear_regression(ts, vs).slope`, level = `severity` when `slope ≤ −limit_per_s` (falling), `≥ limit_per_s` (rising) or `|slope| ≥ limit_per_s` (both), else NOMINAL; side from the slope's sign. ROC has no deadband (the mission model has none; persistence governs its return, see Report). Regenerate with `make gen` and commit the result if it changes.

- [ ] **Step 4: Run tests and the gate**
Run: `uv run pytest tests/unit -q && make gen && git status --porcelain interfaces ui/src/api` then the Task 1 gate.
Expected: all pass; any generated change is staged with this commit.

- [ ] **Step 5: Commit**
`git commit -m "feat: add the alarm check registry with limit and rate checks and the deadband floor"`

---

### Task 4: Alarm engine – persistence, one alarm per parameter, life cycle

**Files:**
- Create: `src/gs/alarms/engine.py`, `tests/support/alarms.py`, `tests/unit/test_engine.py`, `tests/unit/test_alarm_lifecycle.py`
- Modify: `tests/meta/test_add_parameter.py` (the new parameter is limit-checked)

**Interfaces:**
- Consumes: `gs.alarms.checks.{CHECKS, Sample, Reading}`, `gs.alarms.contract.{Level, Transition, OpenAlarm}`, `gs.ingest.events.{transition_key, alarm_id_for}`, `gs.common.missiondb.MissionDb`.
- Produces in `gs.alarms.engine`:
  - `@dataclass(frozen=True) class Slot: kind: str; level: Level; side: str | None; ring: tuple[Level, ...]; state: object`
  - `@dataclass(frozen=True) class Alarm: alarm_id: UUID; state: AlarmState; severity: Level; side: str | None; acked_sev: Level | None; trigger_check: str; opened_at: datetime`
  - `@dataclass(frozen=True) class EngineState: slots: Mapping[tuple[str, str], Slot]; alarms: Mapping[str, Alarm]` (open alarms by param; never mutated, every step builds new mappings)
  - `@dataclass(frozen=True) class CompiledSet: set_sha256: str; limits_sha256: str; checks: Mapping[str, tuple[tuple[Check, int], ...]]` (param → (check, persistence) in file order)
  - `compile_set(mdb: MissionDb) -> CompiledSet`; `initial_state(cset: CompiledSet) -> EngineState`
  - `step(state: EngineState, cset: CompiledSet, vehicle: str, epoch: datetime, seq: int, t: datetime, period_s: float, values: Mapping[str, float | None], reset: bool) -> tuple[EngineState, tuple[Transition, ...]]`
  - `ack(state: EngineState, vehicle: str, param: str, user: str, note: str, key: str, t: datetime) -> tuple[EngineState, Transition | None]` (pure; the apply path is day 3)
- `tests/support/alarms.py`: `run_engine(cset, frames: Iterable[tuple[int, float, dict[str, float | None]]], resets: set[int] = frozenset()) -> list[Transition]`, `nominal_frames(seconds, seed)`, `leak_frames(...)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_engine.py   (cset = compile_set(load_dir(MISSION_DIR)); N = 3)
def test_spike_no_alarm():                       # ALM-03
    tr = run_engine(CSET, temp_series([22, 31, 22, 22, 22, 22]))
    assert tr == []
def test_three_reds_raise_once_on_third():       # ALM-05 latency from the N-th frame
    tr = run_engine(CSET, temp_series([22, 31, 31, 31, 31]))
    assert [(t.kind, t.severity, t.trigger_seq, t.alert) for t in tr] == [("RAISE", Level.RED, 3, True)]
def test_gap_resets_counters_not_alarm():         # D-ALM-04
    tr = run_engine(CSET, temp_series([31, 31, 31, 22, 22, 22, 22]), resets={4})
    assert [t.kind for t in tr] == ["RAISE"]       # ring cleared at the reset: RTN needs 3 fresh frames → none yet
def test_null_sample_holds_level():               # EX-03
    tr = run_engine(CSET, temp_series([31, 31, 31, None, None, None, None]))
    assert [t.kind for t in tr] == ["RAISE"]
def test_one_alarm_per_param_escalates():          # D-ALM-06, §3.4 leak
    kinds = [(t.kind, t.check_kind, t.severity) for t in run_engine(CSET, leak_frames(seconds=17, seed=1))]
    assert kinds[0] == ("RAISE", "roc", Level.YELLOW) and ("ESCALATE", "limit", Level.RED) in kinds
    assert sum(k[0] == "RAISE" for k in kinds) == 1
def test_noise_one_alarm(param, check_value, reading):   # ALM-02, parametrized over every limit
    # of every param x reading in ("pct_of_span", "pct_of_limit"): value = limit + noise uniform ±1 %
    # (of span or of |limit|), N = 1 cfg, 2000 frames → exactly one RAISE, no RTN
def test_nominal_noise_no_alarm():                 # Review Focus 5: 10 min at §3.3 sigma, every param
    assert run_engine(CSET, nominal_frames(seconds=600, seed=7)) == []
def test_keys_and_ids():
    (t,) = run_engine(CSET, temp_series([31, 31, 31]))
    assert t.key == transition_key(V, "cabin_temp_c", EPOCH, 3, "RAISE") and t.alarm_id == alarm_id_for(t.key)
    assert t.level == "RED_HIGH" and t.trigger_check == "limit"

# tests/unit/test_alarm_lifecycle.py   (R8 §5 state table)
def test_rtn_before_ack_is_rtn_unack(): ...        # RAISE → RTN: alarm_state RTN_UNACK, kind "RTN"
def test_ack_then_rtn_clears(): ...                # ACK (user, note) → ACKED; RTN → kind "CLEAR", CLEARED, by system
def test_ack_on_rtn_unack_clears_by_user(): ...    # kind "ACK", alarm_state CLEARED, by_user "fc1"
def test_acked_yellow_escalating_alerts_again():   # D-ALM-08 / Q2
    ... # RAISE YELLOW, ACK, ESCALATE RED → alarm_state UNACK, alert True
def test_red_yellow_red_after_ack_no_alert(): ...  # acked_sev RED kept: ESCALATE stays ACKED, alert False
def test_rtn_unack_reraise_same_alarm(): ...       # same alarm_id, kind RAISE, alarm_state UNACK
def test_ack_requires_note():
    with pytest.raises(ValueError): ack(state_with_open_alarm(), V, "cabin_temp_c", "fc1", "  ", "k", T)
def test_second_ack_is_noop(): ...                 # ACKED + ack → (same state, None)

# tests/meta/test_add_parameter.py: after the frame is stored, the new parameter is limit-checked:
#   tr = run_engine(compile_set(db), [(i, 75.0 hum.) for i in 1..3]) → one RAISE RED for "cabin_humidity_pct"
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/unit/test_engine.py tests/unit/test_alarm_lifecycle.py tests/meta/test_add_parameter.py -q`
Expected: `ModuleNotFoundError: gs.alarms.engine`.

- [ ] **Step 3: Implement `compile_set`, `initial_state`, `step`, `ack` in `src/gs/alarms/engine.py`**
Per frame: for each param with checks: a NULL value or `reset=True` (GAP, SEQ_RESET, data-STALE; the caller decides) replaces every slot's `ring` with `()` and `state` with `check.initial()`, keeping `level`; a NULL is not classified. Otherwise each check gives a `Reading` from the slot's debounced `level`; `None` pushes nothing. The ring keeps the last N readings; when it is full: `min(ring) > level` → level = min (escalate), `max(ring) < level` → level = max (de-escalate). Param severity = max slot level; `trigger_check` = first slot at that level in file order. Transitions from param severity vs the open alarm (R8 §5 table): none → RAISE (UNACK; alert if RED); up → ESCALATE (UNACK → alert; ACKED with `acked_sev < RED` → UNACK + alert; ACKED with `acked_sev == RED` → stays ACKED, no alert); down → DEESCALATE (state kept); to NOMINAL → `RTN` (UNACK → RTN_UNACK) or `CLEAR` (ACKED → CLEARED, by system; `closed_at = t`); RTN_UNACK + rise → RAISE on the same alarm. `level` text = `f"{severity.name}_{side}"`. `opened_at` = the RAISE frame's `t`. `ack` writes kind `ACK`, UNACK → ACKED with `acked_sev = severity`, RTN_UNACK → CLEARED; empty note → `ValueError`. Times are converted to epoch seconds for `Sample.t`.

- [ ] **Step 4: Run tests and the gate**
Run: `uv run pytest tests/unit tests/meta -q` then the Task 1 gate.
Expected: all pass. If `test_nominal_noise_no_alarm` or `test_noise_one_alarm` fails on the shipped values, stop and report (Global: design values are not tuned here).

- [ ] **Step 5: Commit**
`git commit -m "feat: add the alarm engine with persistence, one alarm per parameter and the ISA life cycle"`

---

### Task 5: Engine-state snapshot, approximate rebuild and set switch

**Files:**
- Create: `src/gs/alarms/state.py`, `tests/unit/test_engine_state.py`
- Modify: `src/gs/alarms/engine.py` (add `switch_set`)

**Interfaces:**
- Consumes: Task 4 types; `gs.alarms.checks.CHECKS`; `gs.alarms.contract.OpenAlarm`.
- Produces:
  - `gs.alarms.state.shape_version(*extra: type) -> int` = `zlib.crc32` of the sorted `(qualname, field name, field type repr)` of `Slot`, `Alarm`, every registered `Check.State` and each `extra` dataclass, masked to 31 bits (no hand-bumped constant: a shape change changes it).
  - `dump(state: EngineState) -> dict[str, Any]` (JSON-native: levels as ints, UUIDs and times as `iso_z` strings, check states as `{"kind": k, **asdict(state)}`), `load(obj: Mapping[str, Any], cset: CompiledSet) -> EngineState` (`ValueError` on an unknown kind or shape).
  - `rebuild(open_alarms: Sequence[OpenAlarm], cset: CompiledSet) -> EngineState` — A-E1-5 approximation: alarms from the rows; every slot from `initial()` with level NOMINAL **except** the alarm's `trigger_check` slot, seeded at the alarm's severity (ruling: without the seed the first frame would RTN a still-red value and re-alert).
  - `gs.alarms.engine.switch_set(state: EngineState, old: CompiledSet, new: CompiledSet, vehicle: str, epoch: datetime, seq: int, t: datetime) -> tuple[EngineState, tuple[Transition, ...]]` — per slot `(param, kind)` (D-EXT-06 rule, R8 §7–8): in both sets → keep level, side and check state (ROC window), reset the ring; only in old → forced NOMINAL (resulting transitions carry `note = "LIMITS_CHANGED"`, `trigger_seq = seq`); only in new → `initial()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_engine_state.py
@pytest.mark.parametrize("seed", range(50))
def test_snapshot_roundtrip_equals_uninterrupted(seed):      # D-ALM-10 property, stdlib random
    frames = mixed_frames(seed)          # leak ramps, spikes, NULLs, resets, ACKs at random seqs
    straight = run_engine(CSET, frames)
    rng, cut = random.Random(seed), []
    state, got = initial_state(CSET), []
    for batch in random_batches(frames, rng):
        state = load(json.loads(json.dumps(dump(state))), CSET)     # every batch boundary
        state, tr = fold_frames(state, CSET, batch); got += tr
    assert [t.key for t in got] == [t.key for t in straight]
    assert dump(state) == dump(run_engine_state(CSET, frames))
def test_unknown_kind_rejected():
    with pytest.raises(ValueError): load({"slots": [{"kind": "nope"}], "alarms": []}, CSET)
def test_shape_version_tracks_state_shape():
    @dataclass(frozen=True) class A: x: int
    @dataclass(frozen=True) class B: x: int; y: int
    assert shape_version(A) != shape_version(B) and shape_version(A) == shape_version(A)
def test_rebuild_seeds_trigger_check():
    st = rebuild([OpenAlarm(ID, "cabin_temp_c", "UNACK", Level.RED, None, "limit", T)], CSET)
    _, tr = step(st, CSET, V, E, 10, T, 0.1, {**NOMINALS, "cabin_temp_c": 31.0}, False)
    assert tr == ()                     # still red: no RTN, no second alert
def test_switch_keeps_shared_slot_and_alarm(): ...   # open alarm kept, ring reset, ROC window kept
def test_switch_removed_check_returns_to_normal(): ... # roc removed from cabin pressure → RTN with note LIMITS_CHANGED
def test_switch_added_check_starts_initial(): ...
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/unit/test_engine_state.py -q`
Expected: `ModuleNotFoundError: gs.alarms.state`.

- [ ] **Step 3: Implement `shape_version`, `dump`, `load`, `rebuild` in `state.py` and `switch_set` in `engine.py`**
`dump` sorts slots by `(param, kind)` and alarms by param so equal states dump equal.

- [ ] **Step 4: Run tests and the gate**
Run: `uv run pytest tests/unit -q` then the Task 1 gate. Expected: all pass.

- [ ] **Step 5: Commit**
`git commit -m "feat: snapshot the alarm engine state and switch mission-db sets per check slot"`

---

## Track B – ingest depth

### Task 6: Sequence tracker

**Files:**
- Create: `src/gs/ingest/seq.py`, `tests/unit/test_seq_tracker.py`

**Interfaces:**
- Consumes: `gs.ingest.events.{GroundEvent, seq_key}`.
- Produces in `gs.ingest.seq`: `MOD = 2**32`, `HALF = 2**31`, `RESET_SEQ_MAX = 50`, `NOMINAL_PERIOD_S = 0.1`; `@dataclass(frozen=True) class SeqState: epoch: datetime; last_seq: int; last_t: datetime; period_s: float`; `Klass = Literal["FIRST","IN_ORDER","GAP","SEQ_JUMP","DUP_OR_LATE","SEQ_RESET","SEQ_ANOMALY"]`; `step(s: SeqState | None, vehicle: str, seq: int, t: datetime) -> tuple[SeqState, Klass, tuple[GroundEvent, ...]]` (on DUP_OR_LATE the state is returned unchanged); `is_new(k: Klass) -> bool` (`k != "DUP_OR_LATE"`).

- [ ] **Step 1: Write the failing tests** — R5's table T1–T9, 10 Hz unless stated:

```python
def test_t1_nominal_no_events(): assert events(feed(range(100))) == []
def test_t2_dropout_one_gap():                      # ING-04
    ev = events(feed(range(50)) + feed(range(150, 200), start_t=5.0 + 10.0))
    (g,) = ev; assert (g.kind, g.details["from_seq"], g.details["lost"]) == ("GAP", 50, 100)
    assert abs(g.details["lost_by_time"] - 100) <= 1 and g.key == seq_key("GAP", V, EPOCH, 50)
def test_t3_duplicate(): assert classes(feed([0, 1, 2, 3, 4, 4, 5]))[5] == "DUP_OR_LATE" and events(...) == []
def test_t4_redelivery_after_gap_no_second_gap(): ...   # state after T2, refeed 145..150 → all DUP_OR_LATE
def test_t5_seq_jump(): ...                              # 9 → 1010, 0.1 s → SEQ_JUMP lost 1000, lost_by_time 0
def test_t6_restart_is_reset_not_dup(): ...              # 31 frames, seq 0 at +1.5 s → SEQ_RESET, epoch = t of new 0
def test_t7_restart_first_seen_3(): ...                  # 500 frames, then seq 3 → SEQ_RESET + GAP(new epoch, from 0, lost 3)
def test_t8_wrap(): ...                                  # 2^32-2, 2^32-1, 0, 1 → none; 2^32-2, 2^32-1, 2 → GAP from 0 lost 2
def test_t9_deterministic(): assert run(FRAMES) == run(FRAMES)
def test_backward_jump_above_50_is_anomaly(): ...        # seq 500 → 100 with time forward → SEQ_ANOMALY, new epoch
def test_period_tracks_100hz(): ...                      # EWMA 0.9/0.1 on in-order dt → ≈ 0.01 after 200 frames
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_seq_tracker.py -q` → import error.
- [ ] **Step 3: Implement `step` exactly as R5's "Per-vehicle tracker"** (`docs/research/R5-idempotency-gaps-resets.md` §Decision candidate): `d = (seq − last_seq − 1) % MOD`; behind (`d ≥ HALF`) and `dt ≤ 0` → DUP_OR_LATE; behind and `dt > 0` → SEQ_RESET when `seq ≤ 50` (plus `GAP` from 0 with `lost = seq` when `seq > 0`) else SEQ_ANOMALY, new epoch = `t`; period EWMA `0.9 p + 0.1 dt` only for `d == 0, dt > 0`; `by_time = max(round(dt / period) − 1, 0)`; GAP when `abs(d − by_time) ≤ max(2, d // 10)`, else SEQ_JUMP; GAP/SEQ_JUMP use the old epoch and `from_seq = (last_seq + 1) % MOD`. Details: `{epoch, from_seq, lost, lost_by_time}`; SEQ_RESET/ANOMALY: `{epoch, prev_epoch, from_seq}`; `t_onboard` = the revealing frame's `t`.
- [ ] **Step 4: Run** `uv run pytest tests/unit/test_seq_tracker.py -q` → PASS; then the Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: add the pure per-vehicle sequence tracker"`

---

### Task 7: LOS/AOS and FROZEN detectors

**Files:**
- Create: `src/gs/ingest/link.py`, `tests/unit/test_link.py`, `tests/unit/test_frozen_detector.py`

**Interfaces:**
- Consumes: `gs.ingest.seq.SeqState`, `gs.ingest.events.{GroundEvent, link_key, frozen_key}`.
- Produces in `gs.ingest.link`: `STALE_S = 2.0`, `FROZEN_S = 2.0`;
  - `@dataclass(frozen=True) class LinkState: los_open: bool = False; epoch: datetime | None = None; last_seq: int | None = None`
  - `on_frame(s: LinkState, vehicle: str, prev: SeqState | None, t: datetime, lost: int | None) -> tuple[LinkState, tuple[GroundEvent, ...], bool]` — `stale = prev is not None and (t − prev.last_t).total_seconds() > STALE_S`; stale and not open → LOS (`link_key("LOS", v, prev.epoch, prev.last_seq)`, `t_onboard = prev.last_t`); open or stale → AOS (same suffix as its LOS, `t_onboard = t`, `details = {epoch, last_seq, lost_frames: lost}`); returns the data-STALE flag for the engine reset.
  - `on_silence(s: LinkState, vehicle: str, prev: SeqState | None) -> tuple[LinkState, tuple[GroundEvent, ...]]` — LOS when not open and `prev` exists (the read loop calls it after 2 s without a new frame).
  - `@dataclass(frozen=True) class FrozenState: values: tuple[float | None, ...] | None = None; first_seq: int = 0; t_from: datetime | None = None; reported: bool = False`
  - `frozen_step(s: FrozenState, vehicle: str, epoch: datetime, seq: int, t: datetime, values: tuple[float | None, ...]) -> tuple[FrozenState, GroundEvent | None]` — a change of any value restarts the run; equal values for `t − t_from ≥ FROZEN_S` emit one `FROZEN` (`frozen_key(v, epoch, first_seq)`, details `{first_seq, t_from}`) per run. Values = every mission parameter in order (analog only; `mode` excluded, D-ING-21); compared with `==` (`-0.0 == 0.0` counts as frozen).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_link.py
def test_gap_over_2s_gives_los_and_aos_once():
    s, ev, stale = on_frame(LinkState(), V, prev_at(seq=49, t=4.9), t_at(15.0), lost=100)
    assert [e.kind for e in ev] == ["LOS", "AOS"] and stale and not s.los_open
    assert ev[1].details["lost_frames"] == 100 and ev[0].key[4:] == ev[1].key[4:]
def test_exactly_2s_is_not_stale(): assert on_frame(LinkState(), V, prev_at(t=0.0), t_at(2.0), 0)[2] is False
def test_silence_then_frame():                         # LOS from the read-loop timer, AOS on the next frame
    s, (los,) = on_silence(LinkState(), V, prev_at(seq=9, t=1.0))
    s2, (aos,), stale = on_frame(s, V, prev_at(seq=9, t=1.0), t_at(1.1), lost=0)
    assert los.kind == "LOS" and aos.kind == "AOS" and not stale and los.key[4:] == aos.key[4:]
def test_silence_twice_one_los(): ...                    # second on_silence while open → no event
def test_reset_lost_unknown(): ...                       # lost=None → AOS details lost_frames None

# tests/unit/test_frozen_detector.py
def test_all_identical_2s_frozen_once(): ...             # 25 equal frames at 10 Hz → exactly one FROZEN at the frame where t − t_from ≥ 2.0
def test_one_value_changing_not_frozen(): ...            # only rate_x varies → none
def test_100hz_needs_2s_not_20_frames(): ...             # 100 Hz: 25 equal frames (0.24 s) → none (RV-24)
def test_new_run_after_change_reports_again(): ...
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_link.py tests/unit/test_frozen_detector.py -q` → import error.
- [ ] **Step 3: Implement as specified in Interfaces.**
- [ ] **Step 4: Run** → PASS; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: add LOS/AOS and FROZEN detectors over vehicle time"`

---

### Task 8: Frame transaction I – sequence, link and FROZEN events

**Files:**
- Create: `src/gs/ingest/fold.py`, `tests/unit/test_fold.py`, `tests/integration/test_frame_txn.py`
- Modify: `src/gs/ingest/worker.py` (`process_batch`, `run_vehicle`), `src/gs/ingest/store.py`, `src/gs/ingest/main.py`, `tests/support/ingest.py`, `tests/integration/test_ingest_store.py`, `tests/integration/test_dlq.py` (harness signature)

**Interfaces:**
- Consumes: Tasks 6–7; `gs.ingest.events.{insert_events, insert_set, reset_key}`; `gs.alarms.state.shape_version` is **not** available in this track — the version function here is `fold.state_version() -> int` (crc32 of the `LinkState`/`FrozenState`/`SeqState` field signatures); Task 16 replaces it with `shape_version(LinkState, FrozenState)`.
- Produces in `gs.ingest.fold`:
  - `@dataclass(frozen=True) class VehicleState: seq: SeqState | None; link: LinkState; frozen: FrozenState; set_sha256: str`
  - `@dataclass(frozen=True) class Outcome: new_frames: tuple[FrameHeader, ...]; events: tuple[GroundEvent, ...]; dup_or_late: int`
  - `fold(vs: VehicleState, vehicle: str, frames: Sequence[FrameHeader], params: Sequence[str]) -> tuple[VehicleState, Outcome]` — per frame in stream order: `seq.step`; `lost` = the GAP's `lost` (IN_ORDER → 0, SEQ_RESET/ANOMALY → None); `link.on_frame`; FROZEN on new frames only; DUP_OR_LATE frames counted, no events.
  - `dump_vehicle(vs) -> dict[str, Any]` (`{"link": …, "frozen": …}`), `load_vehicle(seq: SeqState | None, obj: Mapping | None, set_sha256: str) -> VehicleState`.
- Produces in `gs.ingest.store`: `load_seq_state(conn, vehicle) -> SeqState | None`; `load_engine_row(conn, vehicle) -> tuple[int, str, dict] | None` (`schema_version, set_sha256, state`); `async write_outcome(conn, vehicle, vs: VehicleState, outcome: Outcome) -> None` = in order: `upsert seq_state` (`INSERT … ON CONFLICT (vehicle) DO UPDATE SET epoch=EXCLUDED.epoch, last_seq=EXCLUDED.last_seq, last_t=EXCLUDED.last_t, period_s=EXCLUDED.period_s`), `insert_events`, `upsert engine_state(vehicle, last_seq, epoch, set_sha256, schema_version, state)` with `updated_at = clock_timestamp()`. A vehicle with no frame yet writes no `seq_state`/`engine_state`.
- `process_batch(ctx: Ctx, conn, stream, entries, vs: VehicleState) -> tuple[VehicleState, int]`; inside one transaction (D-ING-22): frames (`insert_frames`), then `write_outcome`; then COMMIT → `XADD ui:events` (inserted `tm`) → MULTI DLQ + XACK. The returned state replaces the loop's state only after the MULTI succeeds.
- `run_vehicle` loads state at start (`load_seq_state`, `load_engine_row`; a row whose version ≠ `state_version()` is ignored and an `ENGINE_STATE_RESET` event is written with the first batch); calls `on_silence` when no new frame arrived for `STALE_S` of `time.monotonic()` while the loop is reading, writing LOS + `engine_state` in a small transaction. `main.run` inserts the start set with `insert_set` (from `load_dir`, as in Gate 2) before the vehicle tasks start. `Ctx` gains `set_sha256: str` and `dup_or_late: Counter[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fold.py
def test_dup_frames_not_new(): ...                         # [0,1,1,2] → new_frames seqs [0,1,2], dup_or_late 1
def test_gap_reveals_los_aos_with_lost(): ...              # [0..9] + [110..] after 10 s → GAP, LOS, AOS (lost 100)
def test_dump_load_roundtrip(): assert load_vehicle(S, dump_vehicle(VS), SHA) == VS

# tests/integration/test_frame_txn.py
async def test_gap_event_once_across_redelivery():        # ING-04 + T4 on real Postgres
    v = vid(); produce(v, 50); produce_after(v, start_seq=150, n=50, gap_s=10)
    async with worker([v]): await until(lambda: stored(v) == 100, 10)
    redeliver_last(v, 6)                                    # XADD 6 byte-identical copies of stored frames
    async with worker([v]): await until(lambda: drained(v), 10)
    (gap,) = events_for(v, "GAP"); assert gap["details"]["lost"] == 100
    assert [e["kind"] for e in events_for(v) if e["kind"] in ("LOS", "AOS")] == ["LOS", "AOS"]
async def test_seq_state_in_same_txn():                    # D-ING-15
    ... # after a batch: seq_state.last_seq == max stored seq, epoch = first frame t
async def test_restart_continues_tracker_no_false_gap(): ... # stop worker, start again, more frames → no GAP/RESET
async def test_silence_writes_los_then_aos(): ...           # produce 10, wait 2.5 s, produce 10 → LOS written before the 2nd burst, AOS lost_frames 0
async def test_aos_after_restart_during_los(): ...          # LOS open, restart worker, next frame → AOS (state from engine_state)
async def test_frozen_event_on_identical_values(): ...      # 25 frames with identical parameter values → one FROZEN
async def test_engine_state_version_mismatch_reset(): ...   # row with schema_version -1 → ENGINE_STATE_RESET event, processing continues
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_fold.py tests/integration/test_frame_txn.py -q`.
- [ ] **Step 3: Implement `fold.py`, the store functions and the worker changes as specified.** Existing Gate 2 tests (`test_ingest_store.py`, `test_dlq.py`) keep passing through the updated harness (`tests/support/ingest.py::process_batch_once` builds an empty `VehicleState`).
- [ ] **Step 4: Run** `uv run pytest tests/unit tests/integration -q` → PASS; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: write sequence, LOS/AOS and FROZEN events in the frame transaction"`

---

### Task 9: Error classes, poison rule, data-error isolation, controlled exit

**Files:**
- Create: `src/gs/ingest/errors.py`, `tests/unit/test_pg_error_class.py`, `tests/system/test_db_errors.py`
- Modify: `src/gs/ingest/worker.py`, `src/gs/ingest/main.py`, `tests/integration/test_dlq.py`, `tests/integration/test_ingest_main.py`, `tests/system/test_ownership.py` (create with `test_db_error_exits_without_ack`), `docs/requirements.md` (ING-08 row wording: "`ALTER SYSTEM SET default_transaction_read_only = on` + `pg_reload_conf()` for 10 s", per A-R15-3)

**Interfaces:**
- Consumes: Task 8 `process_batch`; `gs.ingest.events.{poison_key}`; `DlqRecord` reason `poison`.
- Produces in `gs.ingest.errors`: `pg_error_class(sqlstate: str | None) -> Literal["data", "operational"]` (`"22…"`/`"23…"` → data, everything else incl. `None` → operational, A-R2R-1); `POISON_DELIVERIES = 3`; `RETRIES = 3`; `class OperationalDbError(Exception)`.
- `process_batch` now: (1) `times_delivered > 3` (from `XPENDING`, before processing) → DLQ record `reason="poison"`, `detail="delivered <n> times"` + `POISON` event (`details {stream, entry_id, deliveries}`) — the entry is not processed; (2) a `psycopg.Error` with a data class, or any other exception from fold/store (`# noqa: BLE001  D-ING-04: a processing exception is the frame's fault`), rolls back and re-runs the batch **one frame per transaction** from the pre-batch state, each frame up to 3 times; a frame failing 3× → DLQ `poison`, `detail = "<SQLSTATE or exception type>: <message>"`; the rest commit, no exit; (3) an operational error anywhere → rollback, no XACK, `XCLAIM tm:<v> ingest <me> 0 <ids…> RETRYCOUNT 0 JUSTID` over every id of the batch (skipped with a WARNING if Redis fails too), then `raise OperationalDbError`; `main()` turns it into `sys.exit(1)`.
- **Redelivery is read one entry at a time** (ruling, Review Focus 2): the own-PEL path (`XREADGROUP … 0`) uses `COUNT 1` until empty; Tasks 10–11 use `COUNT 1` for `XAUTOCLAIM` too. Only `>` reads use COUNT 100.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_pg_error_class.py
@pytest.mark.parametrize("code,want", [("22003","data"), ("23505","data"), ("08006","operational"),
    ("57P01","operational"), ("25006","operational"), ("53100","operational"), ("57014","operational"),
    ("40P01","operational"), (None,"operational"), ("XX000","operational")])
def test_class(code, want): assert pg_error_class(code) == want

# tests/integration/test_dlq.py (add)
async def test_poison_after_3():                     # ING-08: DLQ on the 4th delivery
    v = vid(); eid = xadd_raw(v, frame(v, 0))
    for c in ("g1", "g2", "g3"): claim(v, eid, c)      # 3 deliveries, never acked
    async with worker([v]): await until(lambda: drained(v), 5)
    rec = DlqRecord.model_validate_json(dlq_entry_for(eid))
    assert (rec.reason, rec.deliveries) == ("poison", 4) and stored(v) == 0
    assert [e["key"] for e in events_for(v, "POISON")] == [poison_key(f"tm:{v}", eid)]
async def test_poison_neighbours_not_dlqd():          # Review Focus 2
    v = vid(); produce(v, 20); bad = eids(v)[7]
    for c in ("g1", "g2"): read_all_without_ack(v, c)  # whole batch delivered twice
    claim(v, bad, "g3")                                # the bad one a third time
    async with worker([v]): await until(lambda: drained(v), 5)
    assert stored(v) == 19 and [json.loads(d)["entry_id"] for d in dlq_entries(v)] == [bad]
async def test_data_error_isolates_frame(owner_trigger):   # SQLSTATE 22023 for (v, seq 5) via a test trigger
    v = owner_trigger.vehicle; produce(v, 10)
    async with worker([v]) as w:
        await until(lambda: drained(v), 5); assert not w.done()        # no exit
    rec = DlqRecord.model_validate_json(dlq_entries(v)[0])
    assert stored(v) == 9 and rec.reason == "poison" and rec.detail.startswith("22023")
async def test_operational_exit_resets_delivery_count(readonly_db):   # R15 E1, in-process
    v = vid(); produce(v, 5)
    with pytest.raises(OperationalDbError):
        async with worker([v], dsn=readonly_db) as w: await w.tasks[0]
    assert [p["times_delivered"] for p in pending_detail(v)] == [0] * 5 and pending(v) == 5

# tests/integration/test_ingest_main.py: an operational error exits 1 (existing test kept)

# tests/system/test_ownership.py
def test_db_error_exits_without_ack():                # connection-class error → exit, restart, 0 lost
    t0 = now(); pid = owner_backend_pid("NYX-TA1")
    terminate_backend(pid)                              # as postgres
    wait_until(lambda: restart_count_any_ingest_increased(), 20)
    time.sleep(3); assert gapless(seqs_since("NYX-TA1", t0)) and dlq_since("tm:NYX-TA1", t0) == []

# tests/system/test_db_errors.py
def test_operational_error_loop_keeps_frames():       # ING-07/08, R15 E1, A-R15-3
    t0 = now(); before = {s: restart_count(s) for s in ("ingest-1", "ingest-2")}
    set_read_only(True)                                 # ALTER SYSTEM SET default_transaction_read_only = on; SELECT pg_reload_conf()
    try: time.sleep(10)
    finally: set_read_only(False)                       # ALTER SYSTEM RESET …; pg_reload_conf()
    wait_drained(("NYX-TA1", "NYX-TA2"), 30)
    assert sum(restart_count(s) - before[s] for s in before) >= 3
    for v in ("NYX-TA1", "NYX-TA2"): assert gapless(seqs_since(v, t0)) and dlq_since(f"tm:{v}", t0) == []
```
(`owner_trigger` and `readonly_db` are fixtures in this test module: the trigger is created as `gs_owner` on `telemetry` raising `SQLSTATE '22023'` only for the test vehicle and dropped in teardown; `readonly_db` is a `throwaway_db()` with `ALTER DATABASE … SET default_transaction_read_only = on`. `gapless` checks consecutive seqs within each run of `seqs_since`.)

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_pg_error_class.py tests/integration/test_dlq.py -q`.
- [ ] **Step 3: Implement `errors.py` and the `process_batch` / own-PEL changes as specified.**
- [ ] **Step 4: Run** unit + integration → PASS; `make up && uv run pytest tests/system/test_db_errors.py tests/system/test_ownership.py -q` → PASS; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: split Postgres errors by class, isolate bad frames and reset delivery counts on a controlled exit"`

---

### Task 10: Ownership – blocking lock, takeover, hand-back, pre-read fence

**Files:**
- Create: `src/gs/ingest/owner.py`, `tests/integration/test_ownership.py`, `tests/unit/test_ingest_settings.py` (extend if it exists)
- Modify: `src/gs/ingest/worker.py`, `src/gs/ingest/main.py` (`IngestSettings.homes`), `tests/support/ingest.py`

**Interfaces:**
- Consumes: Task 9 `OperationalDbError`, `process_batch`; `tests.support.pg.lock_holder`.
- Produces in `gs.ingest.owner`:
  - `LOCK_SQL = "SELECT pg_advisory_lock(hashtext(%s))"`, `UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtext(%s))"`, argument `"tm:" + vehicle`.
  - `MATCH = "l.locktype = 'advisory' AND l.objsubid = 1 AND ((l.classid::bigint << 32) | l.objid::bigint) = hashtext(%s)::bigint"` (bigint key split across `classid`/`objid`; Review Focus 3).
  - `async acquire(conn, vehicle, stop: asyncio.Event) -> bool` (False when `stop` was set; cancels the blocked call with `conn.cancel_safe()`, Review Focus 4); `async release(conn, vehicle) -> None`; `async waiting(conn, vehicle, consumer) -> bool` (a not-granted lock matching `MATCH` held by a backend whose `application_name = consumer`); `async holder(conn, vehicle) -> tuple[int, str] | None` (granted: pid, application_name); `async fence(conn) -> None` (`SELECT 1`; any `psycopg.Error` → `OperationalDbError`).
  - `parse_homes(raw: str, vehicles: tuple[str, ...]) -> Mapping[str, str]` (vehicle → home consumer, from `ingest-1=NYX-TA1,ingest-2=NYX-TA2`; malformed, duplicate vehicle or unknown vehicle → `ConfigError`).
- `IngestSettings` gains `homes: Mapping[str, str]` (env `INGEST_HOMES`). `Ctx` gains `homes` and `timing: Timing` (`@dataclass(frozen=True) class Timing: start_delay_s: float = 2.0; handback_s: float = 5.0; reclaim_s: float = 5.0; tick_s: float = 5.0; los_s: float = 2.0; hb_ttl_s: int = 3; watchdog_s: float = 30.0`; tests pass shorter values).
- `run_vehicle`: connect with `application_name = consumer`; non-home vehicles wait `start_delay_s` once at startup; `acquire`; then the takeover protocol (D-ING-11): load state (Task 8), `XAUTOCLAIM tm:<v> ingest <me> 0 0-0 COUNT 1` until the cursor is `0-0`, processing each entry (Task 9 rules); then read new entries with `fence` → hook point `after_fence_before_read` (Task 12) → `XREADGROUP … > COUNT 100 BLOCK 1000`. Every `handback_s`, a task holding a non-home vehicle checks `waiting(conn, v, homes[v])`; true → `release` (after its current batch is acked) and `acquire` again. The fence, frame transactions and LOS writes use the lock connection only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_ownership.py
async def test_two_workers_share_and_keep_order():          # ING-06
    v1, v2 = vid(), vid(); homes = {v1: "w1", v2: "w2"}
    async with worker([v1, v2], "w1", homes, FAST), worker([v1, v2], "w2", homes, FAST):
        await until(lambda: lock_holder(v1) == "w1" and lock_holder(v2) == "w2", 5)
        await produce_paced([v1, v2], n=200, hz=50)
        await until(lambda: stored(v1) == stored(v2) == 200, 15)
    for v in (v1, v2): assert out_of_order(v, T0) == 0 and gapless(seqs_since(v, T0))
async def test_order_check_catches_alternating_consumers():   # negative control: no lock, forced alternation
    v = vid(); produce(v, 20); a = read_n(v, "ca", 10); b = read_n(v, "cb", 10)
    await process_batch_once(v, b, "cb"); await process_batch_once(v, a, "ca")
    assert out_of_order(v, T0) > 0
async def test_hand_back_when_home_worker_waits():
    v1, v2 = vid(), vid(); homes = {v1: "w1", v2: "w2"}
    async with worker([v1, v2], "w1", homes, FAST):
        await until(lambda: lock_holder(v2) == "w1", 5)
        async with worker([v1, v2], "w2", homes, FAST):
            await until(lambda: lock_holder(v2) == "w2", 5)
            produce(v2, 50); await until(lambda: stored(v2) == 50, 5)
    assert gapless(seqs_since(v2, T0)) and out_of_order(v2, T0) == 0
async def test_takeover_drains_previous_owner_pel(): ...    # ghost reads 10 without ack; w1 stores them first, in order
async def test_lock_match_negative_hashtext():              # Review Focus 3
    for v in (vid_with_hashtext_sign(-1), vid_with_hashtext_sign(+1)):
        async with hold_lock(v, app="home-x") as _, waiting_lock(v, app="home-y"):
            async with conn() as c:
                assert await holder(c, v) == (ANY_PID, "home-x") and await waiting(c, v, "home-y")
async def test_fenced_owner_exits_before_read():             # D-ING-12, R2-06
    v = vid()
    async with worker([v], "w1", {v: "w1"}, FAST) as w:
        await until(lambda: lock_holder(v) == "w1", 5); terminate_lock_backend(v)
        produce(v, 5)
        with pytest.raises(OperationalDbError): await w.tasks[0]
    assert pending_of(v, "w1") == 0                           # nothing new entered its PEL
def test_sigterm_while_waiting_on_lock():                   # Review Focus 4 (subprocess, like test_ingest_main)
    v = vid()
    with hold_lock_sync(v, app="other"):
        p = start_ingest(VEHICLES=v, INGEST_HOMES=f"ingest-main-test={v}")
        time.sleep(1.5); p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=5) == 0

# tests/unit/test_ingest_settings.py
def test_parse_homes(): assert parse_homes("ingest-1=NYX-TA1,ingest-2=NYX-TA2", ("NYX-TA1","NYX-TA2")) == {...}
@pytest.mark.parametrize("raw", ["ingest-1", "a=NYX-TA1,b=NYX-TA1", "a=NYX-ZZ"])
def test_bad_homes_fail_fast(raw): ...
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/integration/test_ownership.py tests/unit/test_ingest_settings.py -q`.
- [ ] **Step 3: Implement `owner.py`, `Timing`, `parse_homes` and the `run_vehicle` flow as specified.** `tests/support/ingest.py::worker(vehicles, consumer="ingest-test", homes=None, timing=Timing(), dsn=None)`; `homes=None` means "every vehicle is home" so Gate 2 tests keep their behaviour.
- [ ] **Step 4: Run** unit + integration → PASS; `make up && uv run pytest tests/system -q` → PASS (the two compose workers now hold one vehicle each); Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: own each vehicle through a blocking advisory lock with takeover, hand-back and a pre-read fence"`

---

### Task 11: Read-loop liveness – reclaim, heartbeat, steal, watchdog, health

**Files:**
- Create: `src/gs/ingest/liveness.py`, `tests/unit/test_steal_decision.py`, `tests/integration/test_liveness.py`
- Modify: `src/gs/ingest/worker.py`, `src/gs/ingest/main.py`, `tests/unit/test_ingest_status.py`, `tests/integration/test_ingest_main.py` (`test_ingest_health_reports_used_memory`)

**Interfaces:**
- Consumes: Task 10 `owner.holder`, `Timing`; `IngestSettings`.
- Produces in `gs.ingest.liveness`:
  - `should_steal(idle_ms: int, steal_idle_ms: int, since_reconnect_ms: int | None) -> bool` = `idle_ms > steal_idle_ms and (since_reconnect_ms is None or since_reconnect_ms >= steal_idle_ms)` (R2-23; the hold-off uses the same threshold, so CI's 3 000 shortens both).
  - `async steal_tick(ctx: Ctx, conn: psycopg.AsyncConnection) -> list[str]` — for each vehicle this worker does not own: `holder()` → its consumer's `idle` from `XINFO CONSUMERS tm:<v> ingest` → `should_steal` → `SELECT pg_terminate_backend(%s)`; returns the stolen vehicles.
  - `watchdog_ok(seen: Mapping[str, float], owned: Collection[str], now: float, limit_s: float) -> bool`.
  - `ingest_status(seen, owned, now, used_memory: int | None) -> tuple[State, dict[str, object]]` (moved from `main.py`): `down` if an owned loop has not passed for `STALE_S = 10.0`; `degraded` if `used_memory is None` (Redis INFO failed); checks `{owned: sorted, loop_age_s: {v: age} for owned, redis_used_memory}`.
- `IngestSettings` gains `steal_idle_ms`, `reclaim_idle_ms` (`require_int("STEAL_IDLE_MS")`, `require_int("RECLAIM_IDLE_MS")`). `Ctx` gains `owned: set[str]` (mutated only by its own vehicle task), `reconnected_at: list[float | None]`.
- In the vehicle read loop (D-ING-26), every pass: `SET hb:veh:<v> 1 EX 3`; every `reclaim_s`: `XAUTOCLAIM tm:<v> ingest <me> <RECLAIM_IDLE_MS> 0-0 COUNT 1` until `0-0`, processing each (delivery-count rule first); the hand-back check (Task 10). One worker-level task every `tick_s`: `steal_tick`, `INFO memory` → `used_memory`, and `faulthandler.dump_traceback_later(timing.watchdog_s, exit=True)` re-armed only while `watchdog_ok`; `faulthandler.cancel_dump_traceback_later()` on clean shutdown.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_steal_decision.py
@pytest.mark.parametrize("idle,since,want", [(3001, None, True), (3000, None, False),
    (10_000, 500, False), (10_000, 3000, True)])
def test_should_steal(idle, since, want): assert should_steal(idle, 3000, since) is want

# tests/unit/test_ingest_status.py (rewritten for owned loops)
def test_standby_task_is_not_down(): assert ingest_status({}, [], 100.0, 1)[0] == "ok"
def test_owned_stale_loop_down(): ...
def test_redis_unknown_degraded(): ...
def test_watchdog_ok_only_when_every_owned_loop_passed(): ...

# tests/integration/test_liveness.py
async def test_reclaim_idle_30s():                           # ING-08 (threshold 1 s here, 3 s in CI, 30 s default)
    v = vid(); produce(v, 5); read_without_ack(v, "ghost")
    async with worker([v], "w1", {v: "w1"}, Timing(reclaim_s=0.5), reclaim_idle_ms=1000):
        await until(lambda: stored(v) == 5 and pending_of(v, "ghost") == 0, 6)
async def test_heartbeat_from_loop():
    v = vid()
    async with worker([v], "w1", {v: "w1"}, FAST):
        await until(lambda: hb_ttl(v) in (1, 2, 3), 3)
    await until(lambda: hb_ttl(v) == -2, 4)                   # key gone ≤ 3 s after the loop stops
async def test_steal_terminates_idle_owner():
    v = vid(); produce(v, 5)
    async with ghost_owner(v, "ghost-owner"):                 # holds the lock; its consumer read once
        async with worker([v], "w1", {v: "other"}, Timing(tick_s=0.5, start_delay_s=0), steal_idle_ms=1000):
            await until(lambda: lock_holder(v) == "w1" and stored(v) == 5, 8)
async def test_no_steal_of_a_reading_owner(): ...           # owner reading every second: holder unchanged after 3 s at threshold 1 s

# tests/integration/test_ingest_main.py (add)
def test_ingest_health_reports_used_memory():
    body = health_json(start_ingest(...)); assert body["checks"]["redis_used_memory"] > 0
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement as specified.** Integration-test-only knobs (`reclaim_idle_ms`, `steal_idle_ms`) are `worker()` parameters of the harness; production values come from `IngestSettings`.
- [ ] **Step 4: Run** unit + integration → PASS; `make up && uv run pytest tests/system -q`; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: reclaim, heartbeat and steal from inside the read loop, with a watchdog and owned-loop health"`

---

### Task 12: Test hooks and kill/crash system tests

**Files:**
- Create: `src/gs/common/hooks.py`, `tests/unit/test_hooks.py`, `tests/integration/test_hooks_ingest.py`, `tests/system/test_dlq_crash.py`
- Modify: `src/gs/ingest/worker.py`, `src/gs/ingest/main.py`, `tests/system/test_ownership.py`

**Interfaces:**
- Consumes: `tests.support.compose.{recreate, restart_count}`, fixture `fast_ingest` (Track 0).
- Produces in `gs.common.hooks`:
  - `POINTS = ("before_commit", "after_commit_before_publish", "after_publish_before_ack", "after_xack", "after_fence_before_read")`
  - `@dataclass(frozen=True) class Hooks: crash_at: str | None = None; stop_at: str | None = None; hang_at: str | None = None; marker_dir: Path = Path(tempfile.gettempdir()); exit: Callable[[int], NoReturn] = os._exit; kill: Callable[[int, int], None] = os.kill`
  - `Hooks.from_env() -> Hooks` — all off unless `GS_TEST_HOOKS == "1"` (then a set but unused hook var logs one WARNING when hooks are off); values validated (`CRASH_AT`/`STOP_AT` ∈ `POINTS` or `frame_seq:<n>` or `frame_seq:<vehicle>:<n>`; `HANG_AT` ∈ `{"in_loop"}`), malformed → `ConfigError`.
  - `fire(self, point: str) -> None` — a named point fires **once per container**: it creates `marker_dir/gs-hook-<point>` first and does nothing if it exists (ruling: otherwise `restart: unless-stopped` re-runs the same hook forever and the batch reaches the poison count); CRASH logs `test hook CRASH_AT=<point>` and calls `exit(99)`; STOP calls `kill(getpid(), SIGSTOP)`.
  - `frame(self, vehicle: str, seq: int) -> None` — `frame_seq` hooks fire on **every** delivery of the matching frame (no marker), D-ENG-16.
  - `async hang(self, point: str) -> None` — `HANG_AT=in_loop`: `await asyncio.Event().wait()`.
- Call sites in the worker: `before_commit` (inside the transaction, before COMMIT), `after_commit_before_publish`, `after_publish_before_ack`, `after_xack`, `after_fence_before_read` (between `fence()` and `XREADGROUP`), `frame` per frame before the transaction, `hang("in_loop")` at the top of each pass.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_hooks.py
def test_off_without_flag(monkeypatch): monkeypatch.setenv("CRASH_AT", "before_commit"); assert Hooks.from_env().crash_at is None
@pytest.mark.parametrize("v", ["nope", "frame_seq:x", "frame_seq:NYX-TA1:"])
def test_bad_point_fails_fast(monkeypatch, v): ...       # GS_TEST_HOOKS=1, CRASH_AT=v → ConfigError
def test_named_point_fires_once(tmp_path):
    calls = []; h = Hooks(crash_at="after_xack", marker_dir=tmp_path, exit=calls.append)
    h.fire("after_xack"); h.fire("after_xack"); h.fire("before_commit"); assert calls == [99]
def test_frame_seq_every_delivery_and_vehicle_filter(tmp_path): ...   # 'frame_seq:NYX-TA1:5' fires for (TA1,5) twice, never for (TA2,5)
def test_stop_sends_sigstop(tmp_path): ...                # kill called with (getpid(), SIGSTOP)

# tests/integration/test_hooks_ingest.py
def test_crash_before_commit_loses_nothing(tmp_path):    # subprocess gs-ingest; marker dir = tmp_path
    v = vid(); produce(v, 20)
    p = start_ingest(VEHICLES=v, GS_TEST_HOOKS="1", CRASH_AT="before_commit", TMPDIR=str(tmp_path))
    assert p.wait(10) == 99 and stored(v) == 0 and pending(v) > 0
    p = start_ingest(VEHICLES=v, GS_TEST_HOOKS="1", CRASH_AT="before_commit", TMPDIR=str(tmp_path))
    wait_until(lambda: stored(v) == 20 and drained(v), 10); stop(p)

# tests/system/test_ownership.py (add; uses fast_ingest)
@pytest.mark.parametrize("case", ["before_commit", "after_commit_before_publish", "after_publish_before_ack",
                                  "docker_kill", "crash_at_self_restart"])
def test_kill_owner_mid_stream(fast_ingest, case):        # ING-07: 0 lost by seq count
    t0 = now(); rc = restart_count("ingest-1")
    if case == "docker_kill": compose("kill", "ingest-1")
    else: recreate(["ingest-1"], {**FAST_ENV, "CRASH_AT": "after_xack" if case == "crash_at_self_restart" else case}, wait=False)
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 15)        # peer takes over at once
    if case == "crash_at_self_restart":
        wait_until(lambda: restart_count("ingest-1") > rc and lock_holder("NYX-TA1") == "ingest-1", 20)  # hand-back
    time.sleep(3); recreate(["ingest-1"], FAST_ENV)                       # restore, no hook
    assert gapless(seqs_since("NYX-TA1", t0)) and dlq_since("tm:NYX-TA1", t0) == []

# tests/system/test_dlq_crash.py
def test_crash_every_time_frame_to_dlq(fast_ingest):      # ING-08: DLQ on delivery 4, other vehicle keeps flowing
    n = latest_seq("NYX-TA1") + 50
    recreate(["ingest-1", "ingest-2"], {**FAST_ENV, "CRASH_AT": f"frame_seq:NYX-TA1:{n}"}, wait=False)
    a2 = stored("NYX-TA2")
    wait_until(lambda: any(json.loads(d)["raw"].find(f'"seq": {n},') > 0 for d in dlq_entries("NYX-TA1")), 90)
    rec = poison_record("NYX-TA1", n); assert (rec.reason, rec.deliveries) == ("poison", 4)
    assert stored("NYX-TA2") > a2 + 100
    recreate(["ingest-1", "ingest-2"], FAST_ENV); wait_drained(("NYX-TA1",), 30)
    assert seqs_missing("NYX-TA1", around=n) == [n]       # only the poison frame is not stored
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `hooks.py` and the call sites.** `main.run` builds `Hooks.from_env()` into `Ctx.hooks`.
- [ ] **Step 4: Run** unit + integration → PASS; `make up && uv run pytest tests/system -q` → PASS; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "test: add crash, stop and hang hooks and the kill-a-worker system tests"`

---

### Task 13: Hung-owner system tests (steal, fence, fence window)

**Files:**
- Modify: `tests/system/test_ownership.py`; source only if a test exposes a defect (then a failing test first and a `docs/bug-log.md` row).

**Interfaces:**
- Consumes: Tasks 10–12; `docker compose pause/unpause`, `docker compose kill -s SIGCONT ingest-1` (tini forwards SIGCONT to its child; the test asserts the woken worker then exits, so a non-forwarding tini fails the test visibly).

- [ ] **Step 1: Write the tests**

```python
@pytest.mark.parametrize("case", ["sigstop_in_read", "sigstop_after_xack", "await_forever_in_loop",
                                  "sigcont_after_steal", "stop_after_fence"])
def test_hung_owner_is_stolen(fast_ingest, case):          # ING-06/08, D-ING-12/26, R14 E-5
    t0 = now(); rc = restart_count("ingest-1")
    hang(case)            # pause | STOP_AT=after_xack | HANG_AT=in_loop | pause | STOP_AT=after_fence_before_read
    if case == "await_forever_in_loop":
        wait_until(lambda: hb_missing("NYX-TA1"), 6)        # heartbeat stops with the loop (GROUND PROCESSING DOWN signal)
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-2", 3 + 5 + 3)   # STEAL_IDLE_MS + tick + slack
    if case == "await_forever_in_loop":
        wait_until(lambda: restart_count("ingest-1") > rc, 45)            # watchdog exit at 30 s
        assert "most recent call first" in compose("logs", "ingest-1").stdout
    else:
        wake(case)        # unpause | kill -s SIGCONT
        wait_until(lambda: restart_count("ingest-1") > rc, 15)            # fenced owner exits, restarts
    wait_until(lambda: lock_holder("NYX-TA1") == "ingest-1", 20)          # hand-back
    recreate(["ingest-1"], FAST_ENV); time.sleep(3)
    seqs = seqs_since("NYX-TA1", t0); assert gapless(seqs) and len(seqs) == len(set(seqs))
    assert events_for("NYX-TA1", "GAP", since=t0) == []                    # no false GAP
    ooo = out_of_order("NYX-TA1", t0); print(f"{case}: out-of-order {ooo}")
    if case == "sigcont_after_steal": assert ooo == 0                     # stop before the fence: 0 out of order
```

- [ ] **Step 2: Run** `make up && uv run pytest tests/system/test_ownership.py -q -k hung` — expected PASS if Tasks 10–12 are right; a failure is a defect in them (fix with its test, bug-log row).
- [ ] **Step 3: Run the full system suite and the Task 1 gate.**
- [ ] **Step 4: Commit** `git commit -m "test: prove a hung owner is stolen and a woken owner is fenced"`

---

## Track C – simulator faults

### Task 14: Simulator faults and fault control

**Files:**
- Create: `src/gs/simulator/faults.py`, `tests/unit/test_sim_faults.py`, `tests/integration/test_sim_fault_api.py`, `tests/integration/test_stream_trim.py`
- Modify: `src/gs/simulator/frames.py`, `src/gs/simulator/main.py` (`xadd_tm(..., maxlen=MAXLEN)`), `src/gs/common/health.py`, `tests/unit/test_serve_health.py`, `tests/support/sim.py`, `tests/system/test_sim_outage.py`

**Interfaces:**
- Consumes: `gs.simulator.frames.{make_frame, tick_loop, NOMINALS}`, `gs.common.health.serve_health`.
- Produces:
  - `serve_health(host, port, status, routes: Mapping[tuple[str, str], Callable[[bytes], Awaitable[tuple[int, dict[str, object] | None]]]] | None = None)` — adds `(method, path)` routes with a JSON body (headers read up to the blank line, `Content-Length` ≤ 65 536, else 413); existing behaviour unchanged.
  - `gs.simulator.faults`: `class FaultRequest(BaseModel)` (strict, `extra="forbid"`): `vehicle: VehicleId`, `fault: Literal["leak","overheat","dropout","freeze","seq_jump","duplicate","malformed","step"]`, `params: dict[str, object] = {}`, validated per fault: `dropout {duration_s > 0}` (required); `leak`, `overheat`, `freeze {duration_s?}` (absent = until DELETE); `seq_jump {by: int ≥ 1 = 1000}`; `duplicate {count: int ≥ 1 = 1}`; `malformed {kind: "json" | "schema" = "json"}`; `step {param ∈ mission params, value: float, duration_s > 0}`. `class FaultBoard` (the one mutable holder shared by HTTP handler and tick loop; replaces whole immutable tuples per vehicle): `add(req, now_mono)`, `clear(vehicle, fault)`, `active(vehicle, now_mono) -> tuple[Active, ...]`, `take_once(vehicle, name) -> Active | None` (one-shot faults). Pure `apply(values: Mapping[str, float], active, now_mono, frozen_values: Mapping[str, float] | None) -> dict[str, float]` — leak: `cabin_pressure_kpa += −0.4 × elapsed_s`; overheat: `cabin_temp_c += 0.5 × elapsed_s`; step: value forced exactly (no noise); freeze: the values of the frame before the freeze started.
  - Tick semantics (D-SIM-07, A-R5-1/2): `dropout` — frames generated, seq advances, nothing appended; `seq_jump` — next frame's seq = `last + 1 + by`; `duplicate` — the tick's frame then `count` byte-identical copies; `malformed` — the tick's frame then one extra invalid entry (`json`: `'{"vehicle": "<v>", "seq": '`; `schema`: valid JSON with `"seq": "5"`), the seq counter is not consumed. `freeze` keeps seq and time advancing.
  - HTTP on the health port (D-SIM-09): `POST /sim/fault` → 204, invalid body or vehicle not in `SIM_VEHICLES` → 422 `{"error": "INVALID_FAULT", "message": …}`; `DELETE /sim/fault` `{vehicle, fault}` → 204 (idempotent; removes the fault and its offset).
  - `tests/support/sim.py`: `post_fault(vehicle: str, fault: str, port: int | None = None, **params: object) -> int`, `delete_fault(vehicle, fault, port=None) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_sim_faults.py
def test_leak_rate(): assert apply({"cabin_pressure_kpa": 101.3}, [leak(at=0)], 10.0, None)["cabin_pressure_kpa"] == pytest.approx(97.3)
def test_overheat_rate(): ...                          # +0.5 °C/s
def test_step_forces_exact_value_then_ends(): ...
def test_freeze_repeats_previous_values(): ...
@pytest.mark.parametrize("body", [{"vehicle": "NYX-TA1", "fault": "dropout"}, {"vehicle": "NYX-TA1", "fault": "nope"},
    {"vehicle": "NYX-TA1", "fault": "step", "params": {"param": "x", "value": 1, "duration_s": 1}}])
def test_invalid_requests(body): ...                     # ValidationError
def test_pause_and_hold_exec_not_yet_accepted(): ...     # day 3 (D-SIM-06): 'pause', 'hold_exec' → ValidationError

# tests/integration/test_sim_fault_api.py  (in-process sim on a free port, as test_simulator.py does)
async def test_dropout_10s_drops_100_frames_seq_continues():
    v = vid(); seqs = await run_sim_with(v, seconds=13, at=1.0, fault=("dropout", {"duration_s": 10}))
    jump = [b - a for a, b in zip(seqs, seqs[1:]) if b - a != 1]; assert len(jump) == 1 and abs(jump[0] - 101) <= 1
async def test_duplicate_is_byte_identical(): ...
async def test_malformed_extra_entry_seq_unchanged(): ...   # raw entry not JSON; seqs contiguous
async def test_seq_jump_by_1000(): ...
async def test_post_422_and_delete_204(): ...

# tests/integration/test_stream_trim.py
async def test_trim_keeps_unacked():                       # D-ING-06, §3.6
    v = vid(); r = aclient("sim", decode=False); s = f"tm:{v}"
    ensure_group(s); await xadd_tm(r, s, frame(v, 0), maxlen=10)
    read_without_ack(v, "c")                                # entry 0 pending
    for i in range(1, 200): await xadd_tm(r, s, frame(v, i), maxlen=10)
    assert first_id(s) == pending_ids(v)[0]                 # the un-acked entry survived ACKED trimming
    ack_all(v); await xadd_tm(r, s, frame(v, 200), maxlen=10); assert stream_len(s) <= 120

# tests/system/test_sim_outage.py (tighten, Gate 2 Task 18 minors)
#  - expected = 2 vehicles x 10 Hz x measured outage seconds; assert grown >= expected - 20
#  - per vehicle: seqs non-empty and seqs[0] == tail_seq + 1 (no gap at the outage start), contiguous
#  - docstring states exactly what is proven
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `faults.py`, the tick changes, the routes and `post_fault`.**
- [ ] **Step 4: Run** unit + integration → PASS; `make up && uv run pytest tests/system/test_sim_outage.py -q`; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: add simulator faults and the /sim/fault control endpoint"`

---

## Track D – mission-db reload

### Task 15: Mission-db poll rule, set validation and start fallback

**Files:**
- Create: `src/gs/ingest/mission.py`, `tests/support/missionfiles.py`, `tests/unit/test_mission_watch.py`, `tests/integration/test_mission_start.py`

**Interfaces:**
- Consumes: `gs.common.missiondb.{load, MissionDb, MissionDbError}`; `gs.ingest.events.{insert_events, insert_set, rejected_key, GroundEvent}`; `tests.support.pg.{throwaway_db, app_dsn, owner_dsn}`; `gs.common.migrate.add_columns`.
- Produces in `gs.ingest.mission`: `POLL_S = 1.0`, `REVALIDATE_S = 10.0`;
  - `@dataclass(frozen=True) class Poll: last: tuple[str, str, str] | None = None` and `observe(p: Poll, hashes: tuple[str, str, str], active: tuple[str, str, str]) -> tuple[Poll, bool]` — ready only when `hashes == p.last` (two consecutive equal polls, EX-09) and `hashes != active`.
  - `validate_set(raw: tuple[bytes, bytes, bytes], columns: Collection[str], active: MissionDb | None) -> MissionDb` — `load(*raw)` (incl. the floor rule once Track A is merged); a parameter with no `telemetry` column → `MissionDbError("parameters.yaml", "parameter <p> has no telemetry column: run make migrate")`; with `active` given, a command turned from `hazardous: true` to `false` → `MissionDbError("commands.yaml", "<c>: hazardous true→false needs a restart")` (D-EXT-02, EX-25; at start `active=None`, so a restart applies it).
  - `async telemetry_columns(conn) -> set[str]` (`information_schema.columns` with `table_name = 'telemetry' AND table_schema = current_schema()`).
  - `async start_set(conn, mission_dir: Path) -> tuple[MissionDb, tuple[GroundEvent, ...]]` — files valid and migrated → that set (`insert_set`); else the newest `limits_versions` row (`ORDER BY first_seen_at DESC`) that still validates against the columns → that set plus `LIMITS_REJECTED` (`details {file, error, set_sha256, limits_sha256, commands_sha256, parameters_sha256}` of the rejected files); none → re-raise `MissionDbError` (the worker refuses to start).
  - `class MissionWatcher`: `__init__(self, mission_dir: Path, dsn: str, consumer: str, active: MissionDb)`, property `current -> MissionDb`, `async run(self, stop: asyncio.Event) -> None` — every `POLL_S` read the three files and hash the bytes read; `observe`; ready → `validate_set` → `insert_set` → `current` = new set; invalid → one `LIMITS_REJECTED` event (key dedups across workers) and the hashes remembered as rejected; a rejected set is re-validated every `REVALIDATE_S` (a later `make migrate` takes effect without touching the files, R2-18). Its own autocommit connection, `application_name = f"{consumer}-mission"` (global rows, not vehicle data).
- `tests/support/missionfiles.py`: `mission_copy(tmp_path) -> Path`, `edit(path: Path, old: str, new: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mission_watch.py
def test_applied_after_two_equal_polls():
    p, ready = observe(Poll(), B, A); assert not ready
    p, ready = observe(p, B, A); assert ready
def test_intermediate_set_never_ready(): ...          # B then C then C → ready only for C
def test_unchanged_never_ready(): ...                 # A, A while active A → False
def test_hazardous_downgrade_rejected_on_reload(): ...
def test_hazardous_downgrade_allowed_at_start(): ...   # active None → accepted
def test_unmigrated_param_rejected(): ...              # columns lack the new param → "run make migrate"

# tests/integration/test_mission_start.py   (throwaway DBs: never the shared gs schema, R2-24)
async def test_valid_files_used_and_stored(tmp_path): ...
async def test_unmigrated_param_at_start_falls_back(tmp_path):
    m = mission_copy(tmp_path); add_param(m, "cabin_humidity_pct")
    with throwaway_db() as db:                          # schema + gs-migrate columns of the shipped set
        await store_shipped_set(db)
        mdb, ev = await start_set_on(db, m)
        assert mdb.set_sha256 == SHIPPED.set_sha256 and [e.kind for e in ev] == ["LIMITS_REJECTED"]
        assert "run make migrate" in ev[0].details["error"]
async def test_invalid_at_start_without_history_refuses(tmp_path): ...  # band order error, empty limits_versions → MissionDbError
async def test_watcher_applies_valid_edit_within_5s(tmp_path): ...     # edit → current changes ≤ 5 s, limits_versions row exists
async def test_watcher_rejects_then_revalidates_after_migrate(tmp_path):
    ... # unmigrated param → LIMITS_REJECTED, current unchanged; add_columns as owner → current switches within 10 s + 2 polls
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `mission.py` as specified.**
- [ ] **Step 4: Run** unit + integration → PASS; Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: poll the mission db, validate sets and fall back to the last valid set at start"`

---

## Track Z – integration (after A, C, D, B are merged)

### Task 16: Alarm engine in the frame transaction

**Files:**
- Create: `tests/integration/test_alarm_txn.py`
- Modify: `src/gs/ingest/fold.py`, `src/gs/ingest/store.py`, `src/gs/ingest/worker.py`, `src/gs/ingest/main.py`, `tests/unit/test_fold.py`

**Interfaces:**
- Consumes: Track A `compile_set`, `initial_state`, `step`, `dump`, `load`, `rebuild`, `shape_version`; Track B `VehicleState`, `fold`, `write_outcome`; Track 0 `Transition`, `OpenAlarm`.
- Produces: `VehicleState` gains `engine: EngineState`; `Outcome` gains `transitions: tuple[Transition, ...]`; `fold(vs, vehicle, frames, params, cset: CompiledSet)` runs `step` on **new frames only** (D-ING-17) with `reset = (GAP or SEQ_RESET in that frame's events) or data-STALE`, `period_s = seq state period`; `fold.state_version()` becomes `shape_version(LinkState, FrozenState)`; `dump_vehicle` adds `"engine": dump(vs.engine)`. `write_outcome` writes, after the events: per transition `INSERT INTO alarms (alarm_id, vehicle, param, state, severity, acked_sev, trigger_check, limits_sha256, set_sha256, opened_at, closed_at) … ON CONFLICT (alarm_id) DO UPDATE SET state, severity, acked_sev, trigger_check, limits_sha256, set_sha256, closed_at` (severity column keeps the last non-NOMINAL severity) and `INSERT INTO alarm_events (key, alarm_id, vehicle, param, kind, check_kind, level, severity, trigger_seq, trigger_t_onboard, by_user, note, alert, limits_sha256, set_sha256) … ON CONFLICT (key) DO NOTHING`; then the `engine_state` upsert. A version mismatch at load → `rebuild(open rows from alarms WHERE vehicle = %s AND state <> 'CLEARED')` + `ENGINE_STATE_RESET`. `main.run` compiles the start set once.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_alarm_txn.py
async def test_red_temp_raises_with_file_hash():          # ALM-01/06/08 row content
    v = vid(); produce_values(v, cabin_temp_c=[22.0] * 5 + [31.0] * 5)
    async with worker([v]): await until(lambda: len(alarm_events_for(v)) == 1, 5)
    (row,) = alarm_events_for(v)
    assert (row["kind"], row["severity"], row["alert"], row["trigger_seq"]) == ("RAISE", "RED", True, 7)
    assert row["limits_sha256"] == hashlib.sha256((MISSION_DIR / "limits.yaml").read_bytes()).hexdigest()
async def test_redelivery_raises_nothing_twice(): ...       # crash after commit (process_batch then no XACK) → reprocess: 1 RAISE
async def test_alarm_state_survives_restart(): ...          # RAISE, restart worker, 3 nominal → RTN (no second RAISE)
async def test_takeover_continues_engine_exactly(): ...     # w1 → w2 mid-ramp: transitions equal an uninterrupted in-memory run_engine
async def test_version_mismatch_rebuilds_from_alarms(): ... # corrupt schema_version → ENGINE_STATE_RESET, still-red value: no RTN, no new alert
async def test_late_frame_not_alarmed(): ...                # LATE frame stored, no transition
```

- [ ] **Step 2–4: Run to fail, implement, run** unit + integration + `make up && uv run pytest tests/system -q` + Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: run the alarm engine in the frame transaction and persist its snapshot"`

---

### Task 17: Mission-db application in the worker

**Files:**
- Create: `tests/integration/test_limits_reload.py`
- Modify: `src/gs/ingest/main.py`, `src/gs/ingest/worker.py`, `src/gs/ingest/fold.py`

**Interfaces:**
- Consumes: Track D `start_set`, `MissionWatcher`; Track A `switch_set`, `compile_set`; `gs.ingest.events.applied_key`.
- Produces: `main.run` calls `start_set` (writing its `LIMITS_REJECTED` events) instead of `load_dir` and runs `MissionWatcher.run` in the TaskGroup; `Ctx.mission: Callable[[], MissionDb]` (= `watcher.current`). At each batch boundary the owner compares `vs.set_sha256` with the current set: different (or no `engine_state` row yet) → `switch_set` (old compiled set from `limits_versions` by `vs.set_sha256`, cached), then `LIMITS_APPLIED` (`applied_key(v, epoch, first_seq)`, details `{first_seq, set_sha256, limits_sha256, commands_sha256, parameters_sha256}`) in the same frame transaction, before the frames' transitions. The frame model (`build_frame_model`) is rebuilt when the parameter list changes.

- [ ] **Step 1: Write the failing tests** — `tests/integration/test_limits_reload.py` with the requirements' names:

```python
async def test_edit_live_within_5s_and_alarm_carries_hash(tmp_path):     # ALM-08
    m = mission_copy(tmp_path); v = vid()
    async with worker([v], mission_dir=m):
        stream_values(v, cabin_temp_c=22.0)
        t = time.monotonic(); edit(m / "limits.yaml", "yellow_high: 27.0", "yellow_high: 21.5")
        await until(lambda: events_for(v, "LIMITS_APPLIED"), 5); assert time.monotonic() - t < 5
        await until(lambda: alarm_events_for(v), 3)
    row = alarm_events_for(v)[0]
    assert row["limits_sha256"] == sha256_file(m / "limits.yaml") and row["set_sha256"] == set_formula(m)
async def test_commands_only_edit_is_new_version(tmp_path): ...  # LIMITS_APPLIED with new commands_sha256; next alarm keeps limits_sha256, new set_sha256
async def test_set_applied_after_two_equal_polls(tmp_path): ...  # A' then A'' within 0.3 s → LIMITS_APPLIED only for A''
async def test_invalid_reload_keeps_old_set(tmp_path): ...       # LIMITS_REJECTED names the file; alarms continue with old hash
async def test_unmigrated_param_at_start_falls_back(tmp_path): ...  # throwaway db; worker runs on the stored set; after add_columns → applied ≤ 10 s + 2 polls
async def test_removed_roc_check_returns_to_normal(tmp_path): ...  # open ROC alarm, delete the roc line → RTN with note LIMITS_CHANGED
```

- [ ] **Step 2–4: Run to fail, implement, run** the full suite + system + Task 1 gate.
- [ ] **Step 5: Commit** `git commit -m "feat: apply reloaded mission-db sets at the owner's batch boundary"`

---

### Task 18: Cross-track system tests (ATP-04/05 backend, ING-05, ENG-02, ING-07 alarm keys) and doc rulings

**Files:**
- Create: `tests/system/test_link_events.py`, `tests/system/test_faults.py`, `tests/system/test_seq_reset.py`, `tests/system/test_redis_restart.py`, `tests/system/test_alarm_keys.py`
- Modify: `docs/design.md` (record the rulings of this plan in D-ING-04, D-ING-10, D-ING-21, D-DB-08, D-ENG-16, D-ALM-11 and §3), `docs/requirements.md` (Planned evidence: add the new test names), `docs/assumptions.md` (rulings as `lead` assumptions)

**Interfaces:**
- Consumes: everything; `tests.support.sim.post_fault`, `tests.support.compose.recreate`, fixture `fast_ingest`.

- [ ] **Step 1: Write the tests**

```python
# tests/system/test_link_events.py
def test_dropout_gap_los_aos():                      # ATP-04 backend, ING-04
    t0 = now(); assert post_fault("NYX-TA1", "dropout", duration_s=10) == 204
    wait_until(lambda: events_for("NYX-TA1", "AOS", since=t0), 20)
    (gap,) = events_for("NYX-TA1", "GAP", since=t0); assert abs(gap["details"]["lost"] - 100) <= 1
    (los,) = events_for("NYX-TA1", "LOS", since=t0); (aos,) = events_for("NYX-TA1", "AOS", since=t0)
    assert aos["details"]["lost_frames"] == gap["details"]["lost"] and los["key"][4:] == aos["key"][4:]
    assert events_for("NYX-TA2", "GAP", since=t0) == []

# tests/system/test_faults.py                        # ATP-05 backend
def test_duplicate_stored_once(): ...                 # 3 copies → seq stored once, no GAP, DLQ empty
def test_malformed_to_dlq_and_ingest_continues(): ... # DLQ json_invalid, stored count keeps growing
def test_seq_jump_gives_seq_jump_not_gap(): ...       # SEQ_JUMP lost 1000, no GAP
def test_freeze_gives_frozen_event(): ...            # freeze duration_s 3 → one FROZEN
def test_leak_roc_then_red_one_alarm():               # ALM-04/06 live (§3.4)
    t0 = now(); post_fault("NYX-TA1", "leak")
    try: wait_until(lambda: any(r["severity"] == "RED" for r in alarm_events_for("NYX-TA1", t0)), 25)
    finally: delete_fault("NYX-TA1", "leak")
    rows = alarm_events_for("NYX-TA1", t0)
    assert (rows[0]["kind"], rows[0]["check_kind"]) == ("RAISE", "roc")
    assert sum(r["kind"] == "RAISE" for r in rows) == 1 and any(r["kind"] == "ESCALATE" and r["alert"] for r in rows)

# tests/system/test_seq_reset.py                     # ING-05
def test_sim_restart_seq_reset():
    t0 = now(); compose("restart", "simulator")
    wait_until(lambda: len(events_for("NYX-TA1", "SEQ_RESET", since=t0)) == 1, 20)
    print(f"resume latency {first_stored_latency('NYX-TA1', t0):.3f} s")  # recorded; judged in ATP-05 (A-R2R-8)
    assert gapless(seqs_since("NYX-TA1", t0)) and dlq_since("tm:NYX-TA1", t0) == []

# tests/system/test_redis_restart.py                 # ENG-02 backend (BROKER banner is day 3)
def test_redis_stop_10s_recovers():
    t0 = now(); compose("stop", "redis"); time.sleep(10); compose("start", "redis")
    for s in ("simulator", "ingest-1", "ingest-2", "api"): wait_healthy(s, 60)
    wait_drained(("NYX-TA1", "NYX-TA2"), 30)
    for v in ("NYX-TA1", "NYX-TA2"):
        assert gapless(seqs_since(v, t0)) and events_for(v, "GAP", since=t0) == []

# tests/system/test_alarm_keys.py                    # ING-07 alarm half: keys equal an uninterrupted run
@pytest.mark.parametrize("case", ["before_commit", "after_commit_before_publish", "after_publish_before_ack",
                                  "docker_kill", "crash_at_self_restart"])
def test_kill_owner_keeps_alarm_keys(fast_ingest, case):
    t0 = now(); steps = start_steps("NYX-TA1", "cabin_temp_c", value=31.0, every_s=5, duration_s=1.0, count=4)
    kill_or_crash("ingest-1", case); finish(steps); recreate(["ingest-1"], FAST_ENV)
    for w in stepped_windows("NYX-TA1", t0, value=31.0):             # stored seqs where the value was 31.0
        assert raise_at("NYX-TA1", w.seqs[2]) == 1 and rtn_after("NYX-TA1", w) == 1   # 3rd red frame; 3rd nominal frame
```

- [ ] **Step 2: Run** `make up && uv run pytest tests/system -q` — expected PASS; a failure is a defect in the owning code (fix with a failing test first, bug-log row).
- [ ] **Step 3: Record the rulings** of this plan (the Report list) in `docs/design.md` (one sentence per affected decision row, citing this plan), `docs/assumptions.md` and the requirements Planned-evidence cells.
- [ ] **Step 4: Run** the full gate (Task 1 Step 4) and `uv run python scripts/check_acl_log.py` (no ACL denial from steal, reclaim, heartbeat or INFO).
- [ ] **Step 5: Commit** `git commit -m "test: add the ATP-04/05 backend system tests and record gate 3a rulings"`

---

## Self-review notes

- Day-2 row coverage: D-ING-04 (T9), 05 (T9), 06 (T14 trim test), 07 health `used_memory` (T11), 08 (Gate 2 + T9 own-PEL), 09–12 (T10, T11, T13), 13 (Gate 2), 14–16 (T6), 17 (T8, T16), 18–20 (Gate 2), 21 (T7, T8), 22 (T8, T16; ACK requests are day 3), 23 (Gate 2), 24 (T9), 25–26 (T11, T13); D-ALM-01..03 (T3, T4), 04 (T4, T16), 05 (T3), 06–09 (T4), 10 (T3, T5), 11 (T5, T8, T16), 12 (T15, T17), 13 (out of scope by design), 14 (day 3), 15/16 (Gate 4); deadband floor (T3); un-migrated fallback (T15, T17); D-SIM-07 (T14 except `pause`/`hold_exec`), 08/11 (Gate 2 tests kept); D-ENG-16 hooks and thresholds (T2 env, T11, T12, T13); D-ING-03/04/24 counter reset (T9); D-ING-12 fence window (T13 `stop_after_fence`); D-ENG-21 lint (T1).
- Gate 2 deferrals: >3-delivery poison rule (T9); Task 13 digest negative control (T1); loose sim-outage bounds (T14); duplicated `VEHICLE_ID` regex (T1); Task 13's "limit-checked" assertion for the new parameter (T4).
