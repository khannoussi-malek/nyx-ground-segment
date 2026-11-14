# Gate 3b – Commanding, Console, Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the design's "3 – Implementation day 3" row: commanding with the tracker's pure `decide()`, the single-use confirm token and the late-evidence `outcome` (D-CMD-01..11, D-SIM-06/10), the `tc`, `tcack`, `alarm`, `cmd`, `link`, `ground` and `resync` interface models with contract tests (D-EXT-16), the ACK path with its fixed position and PENDING state (D-ALM-14), the operator console incl. the BROKER banner, the event strip, the M4 chart, the hazardous confirm dialog with the typed vehicle ID and the loss-of-monitoring tone (D-UI-01..21), login hardening (D-ENG-08), sessions and the frozen report (D-DB-04/06/07/10), the stream rejection rule (D-ING-27), JSON logs with vehicle and seq (D-ENG-07, GS-ENG-05), the operator and developer guides, the Schemathesis spike and its CI check (D-EXT-18), and perf run N (D-PERF-04/05), so ATP-01 (short), ATP-02, ATP-03 and ATP-06 pass.
**Architecture:** Pure cores (`outcome`, `decide`, argument checks, cabin effects, ACK application, banner precedence, alarm/command/event text, beep and tone key sets, chart series) are unit-tested alone. The owner worker gains the ACK requests at the start of its frame transaction and the command tracker in its own transaction after the frame COMMIT; it publishes `tm · alarm · link · cmd · resync` to `ui:events`. The API gets one `ui:events` reader (the hub) that fans out to bounded per-client queues, tracks the broker and the per-vehicle heartbeats, and rejects schema-invalid entries; REST routes send commands, take ACKs, run sessions and serve M4 chart buckets. The console wires every SSE kind into one store. Work runs as parallel tracks after a shared-contracts track that freezes every interface model, every REST signature and the generated files.
**Tech Stack:** Python 3.14, FastAPI 0.162.6, pydantic 2.14.3, psycopg 3.3.9, redis-py 8.1.0, Redis 8.10.3, TimescaleDB 2.32.0-pg17, pytest 9.2.0, React 19.3.1, TypeScript 6.0.3, Vite 8.3.8, Vitest 5.1.0, Playwright 1.65.0. New dependencies: `uplot` 1.6.32 (UI, D-UI-11) and `schemathesis` (dev, version fixed by the spike in Task 24, at least two weeks old).
**Spec:** `Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md` (binding) · `docs/design.md` (§2.4, §2.7, D-ING-22/25/27, D-ALM-09/14, D-DB-04/06/07/10, D-ENG-07/08, D-EXT-16..19, D-PERF-02..05, §3, §3.1, §3.2, §3.4, §3.6, §3.7, §5.3 day-3 row) · `docs/requirements.md` (test names) · `docs/assumptions.md`.

## Global Constraints

- **Base.** Gate 3a must be fully merged first (its Track Z Tasks 16–18 and the systests/docs branches, G28). Branch `gate3b-console-commanding` from that merge; Track 0 commits on it. Each later track works on `gate3b/<track>` (`gate3b/commanding`, `gate3b/stream`, `gate3b/console`, `gate3b/data`, `gate3b/integration`) in its own worktree, branched **after Track 0 is merged**, merged back with `git merge --no-ff`. Never commit to `main`. Push with `git push -u origin <branch>`.
- **Worktrees (G3).** Each worktree's `.env`: `COMPOSE_PROJECT_NAME=nyx-<track>`, `GS_IMAGE_TAG=<track>`, every host port (`DB_HOST_PORT REDIS_HOST_PORT MINIO_HOST_PORT SIM_HOST_PORT API_HOST_PORT`) +100 × index (commanding 1, stream 2, console 3, data 4, integration 5). Never stop, reset or `down` another project. Track 0 changes DDL and the ACL file: every worktree runs `make reset && make up` once after branching.
- **One owner per file** (table in "Parallel tracks"). A change to a Track 0 file needs a coordinator ruling and lands as a Track 0 patch. The generated files (`interfaces/**`, `ui/src/api/schema.d.ts`) are frozen after Track 0: a track whose code would change them stops and asks.
- **Every change ships with its test in the same commit** (D-ENG-14/15); a bug fix starts with a failing test; a real bug a test catches gets a `docs/bug-log.md` row (D-ENG-19, written by Track Z). Untested behaviour is an Important finding (R27). Test lines written below as `def test_x(): ...  # comment` are specifications by comment; the implementer writes real assertions (R13). No assertion that only restates a DB constraint; tests create their own entries (R14).
- **Coverage gates:** `uv run pytest --cov=gs --cov-fail-under=80` over `tests/unit tests/integration tests/meta` (R20); `cd ui && npm test` (Vitest, 80 % lines/branches/functions/statements over `src/**/*.{ts,tsx}`, `.tsx` included, G22). Also green: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src tests scripts perf`, `cd ui && npx tsc --noEmit`, `cd e2e && npx tsc --noEmit`, `make gen && git diff --exit-code -- interfaces ui/src/api`.
- **Ponytail.** Every implementer loads `ponytail:ponytail` before writing code and follows it; it never removes a required test, validation, data-loss handling, security measure or accessibility basic (R25).
- **Commits:** `<type>: <description>` (`feat fix refactor docs test chore perf ci`), blank line, optional body. **No `Co-Authored-By` trailer** (R21; the commit-msg hook strips it).
- **No time limit, no cuts.** Every decision the day-3 row names is built (design §5 scope rule). A design value that fails is reported to the coordinator, never tuned. **Spec wins over design, design over this plan.**
- **Gate 2 rulings that bind:** helpers in `tests/support/` (DB helpers in `pg.py`, R2/R23); stream entry ids decoded to `str` once where read (R3); every SSE model's `kind` Literal has a default (R4); `age_ms` = `api_now − t_onboard` (R5); `/healthz` 200 `ok` / 200 `degraded` / 503 `down` with `(status, checks)` callbacks (R9/R26); OpenAPI via `build_openapi()`, no `app.openapi = lambda` (R12); OpenAPI must match real responses, every error status declares its body model (R29); a new compose service or Makefile target ships a smoke check (R16); generated files committed once per task (R18); host ports env-overridable, bound to `127.0.0.1` (R22); non-root image (R24); every Redis client has retry off (`Retry(NoBackoff(), 0)`) and env config fails fast through `gs.common.config.require*()` (R28); UI unit tests run in CI (R30).
- **Gate 3a rulings that bind:** G3 (per-worktree image tag); G7 (a docker-backed test that fails only while several tracks build is re-run alone; the merge gate runs alone); G9 (tracks merge as they finish, in the order below); G13 (AOS `lost_frames` from a GAP only, else `null` = unknown); G15/G16 (engine reset-frame and empty-checks semantics unchanged); G17 (`alarms.severity` keeps the last non-NOMINAL severity on RTN/CLEAR); G18 (engine state loaded with the set named by `engine_state.set_sha256`, a `load` error rebuilds, never a crash loop); G20 (`GET /api/vehicles` and `GET /api/mission-db` exist; the API's own mission-db refresh is this plan's Task 12); G21 (`LimitEntry` checks serialize as their own kind); G22/G25 (brand theme: `#F3F1EF` work area, `#0E0E0E` top bar, Inter; brand yellow `#F4D828` only on pill-shaped action buttons; state marks square, labelled, with glyphs ◆ RED ▲ YELLOW; row tints only behind live violations; WCAG AA checked by `ui/src/style.test.ts`); G26 (a gauge zone fills only for an engine-reported live LIMIT alarm with a side and `acq` OK; ROC alarms, stale values and NOMINAL never fill); the Gate 3a event keys and `alarm_id = uuid5(NAMESPACE_URL, "nyx-alarm/" + <RAISE key>)`.
- Unchanged: pinned versions and images, `iso_z()` for every time leaving a process (D-ENG-18), config from env only, no host literal in `src/`, compose rules (D-ENG-03), one `data` field per stream entry (D-SIM-04), `StrictModel` interface models with `extra="forbid"` except `FrameHeader` (D-EXT-18), integration tests connect as the service's ACL user and use `admin` only for setup, test vehicles from `tests.support.env.vid()`.
- **DDL lives in Task 2 only** (`migrations/02-tables.sql` edited in place so it equals design §3.2; the workflow is `make reset`, D-ENG-12). A track that needs more DDL stops and asks.
- **Design values (verbatim; tasks repeat the ones they use).** States QUEUED → SENT → RECEIVED → VERIFIED or FAILED; `failure_reason` ∈ SEND_ERROR · REJECTED · NOT_RECEIVED · NOT_VERIFIED · ACK_INVALID; `outcome` ∈ NOT_SENT · NOT_EXECUTED · UNKNOWN · RECEIVED_LATE · EXECUTED_LATE, computed, never stored; both windows from SENT: not received in **5 s**, not verified in **15 s** (`coalesce(sent_at, queued_at)` for the 5 s rule); per-command overrides only shorter; timeouts only when the owner has caught up; FAILED is terminal, late evidence goes to `late_verified_at` / `late_ack_at` + `late_ack_accepted`; confirm token `SET confirm:{token} {user}|{args_hash} NX PX 60000`, consumed with `GETDEL`, reason **≥ 10 chars**, `expires_in_s: 60`; `args_hash` = sha256 of canonical `{vehicle, name, args}`; interlock `SET cmdlock:{vehicle}:{key} {cmd_id}|{user} NX PX 2000`, key = `name` (hazardous) or `name:args_hash`, user excluded, released by compare-and-delete on SEND_ERROR; simulator ≤ 1 command per tick, `tcack {cmd_id, accepted, code, reason, t_onboard}`, `last_cmd_id` in the next frame; `isolate_valve {valve}` stops an active `leak` and repressurises **+0.5 kPa/s** back to **101.3**; `vent_cabin {duration_s}` drops **2 kPa/s** for `duration_s` (1–10 s); `pause N`, `hold_exec N`; ACK applied by the owner ≤ 1 s, at the start of its next frame transaction, each in a SAVEPOINT, `after_*` = the vehicle's last frame before the batch, statuses `pending · applied · superseded · rejected`; SSE `retry: 1000`, `hb` every **1 s**, NO LIVE CONNECTION from the first EventSource `error` or **3 s** without a message, STALE at **2.0 s** (100 ms tick), LOS = no frame for 2 s while a healthy owner exists and the broker is up, precedence NO LIVE CONNECTION > GROUND PROCESSING DOWN > LOS > FROZEN > STALE; `ui:events` `MAXLEN ~ 3000`; one beep per `alert:true` transition key, one less urgent tone per loss-of-monitoring onset, both key sets mirrored to `sessionStorage`; login: 5 failed per user per minute → 429 `LOGIN_RATE_LIMITED`, session 12 h `SameSite=strict`; report frozen when `seq_state.last_t ≥ ended_at`, else after **30 s** marked "incomplete: n of m frames"; chart windows 2 min · 1 h · 24 h, `width` default **1500**, accepted **100–4000**, bucket = max(window / width, **100 ms**), empty buckets absent; run N = 2 × 10 Hz for **10 min**, `cabin_temp_c` stepped to red for **1 s every 5 s** (120 alarms), first **10 s** excluded, nearest-rank p95, raw CSV in `docs/perf/`; palette red `#B3141B` (white 6.92:1), yellow with `#7A5C00` border (5.25:1), REPLAY purple reserved.

## Review Focus

1. **Evidence that arrives while the owner is behind.** A worker that takes over a vehicle 8 s after an `isolate_valve` was sent finds its `tcack` (t = +0.3 s) and its echo in the same pass, after both deadlines. The operator expects RECEIVED and VERIFIED, not FAILED: windows are judged by evidence timestamps, never by when the pass runs (Task 5 `test_backlog_evidence_wins_over_timeout`, Task 7 `test_no_false_failed_after_takeover`).
2. **An ACK during loss of signal.** The cabin alarm is RED, the bench link drops, the controller acknowledges. No frame arrives for the owner to "start a batch with", yet the ACK must apply within 1 s, positioned after the last frame (Task 9 `test_ack_applies_during_los`).
3. **A session in which the vehicle never sent a frame** (link down the whole test, or a parameter missing from every frame). The conductor expects a report, not a 500: incomplete "0 of m frames" after 30 s, empty statistics, CSV with empty fields (Task 22 `test_silent_session_report`).
4. **A chart window across a 10 s dropout.** The line must break across the gap, not draw a straight segment through data that does not exist (Task 23 `test_empty_bucket_absent`, Task 18 `chartSeries.test.ts` "gap gives null").
5. **A slow console tab.** A browser that stops reading (laptop lid closed, tab throttled) must not grow API memory or block other consoles; it is disconnected and, on its return, gets a snapshot that still holds every open alarm (Task 11 `test_slow_client_overflow_closes_then_snapshot`).

## Parallel tracks

**Track 0 – shared contracts** (Tasks 1–3) runs first and alone and is merged into `gate3b-console-commanding` before any track branches. Then Tracks A, B, C and D run in parallel worktrees. Merge order back (G9: as they finish, but in this order when several are ready): **D → A → C → B**, each merge followed by the full gate on the merged branch (B last: it owns `worker.py`, `app.py` and the Track 0 call sites). Track Z branches after all four are merged and runs alone.

| Track | Tasks (in order) | Consumes | Produces for later tracks |
| --- | --- | --- | --- |
| 0 – shared contracts | 1, 2, 3 | Gate 3a code | every interface model, `outcome()`, event kinds/keys, `publish_ui`, `schema_sha12`, `schema_reject`; DDL, ACL, compose, JSON logs, helpers; REST bodies and route stubs, `audit()`, view loaders, tracker call site, frozen generated files |
| A – commanding | 4, 5, 6, 7 | Track 0 | simulator command path and `pause`/`hold_exec`, `check_args`, `decide`, `POST/GET /api/commands`, `track_pass` |
| B – alarms and live stream | 8, 9, 10, 11, 12 | Track 0 | transition `value`, worker publishing, ACK application, alarms API, API hub (snapshot, `ground`, `hb.processing`, D-ING-27 for `ui:events`), live mission db in the API |
| C – console | 13, 14, 15, 16, 17, 18, 19 | Track 0 (`schema.d.ts`) | store, banners, audio, alarm list, command panel, event strip, chart, session panel, latency probe |
| D – data, login, docs | 20, 21, 22, 23, 24, 25, 26 | Track 0 | login hardening, sessions, report, M4 telemetry, Schemathesis spike, perf tooling, guides |
| Z – integration | 27, 28, 29, 30 | all | cross-track contract/system tests, e2e ATPs, Schemathesis CI check, run N results, doc rulings |

**File ownership after Track 0** (a path not listed and not created by a task is unowned; editing it needs a ruling):

| Track | Owns |
| --- | --- |
| 0 (frozen) | `pyproject.toml` (except Task 24's ruling), `uv.lock`, `.github/workflows/ci.yml`, `docker-compose.yml`, `Makefile`, `.env.example`, `migrations/**`, `scripts/init_env.py`, `scripts/stream_schema_notice.py`, `src/gs/common/{config,time,audit,views,logs,uievents}.py`, `src/gs/common/interfaces/**`, `src/gs/ingest/events.py`, `src/gs/api/{bodies,openapi}.py`, `src/gs/commanding/{__init__,outcome}.py`, `interfaces/**`, `ui/src/api/schema.d.ts`, `perf/__init__.py`, `tests/support/{__init__,env,pg,compose,redisx,http,sse,gen,mission}.py`, `tests/unit/{test_interface_models_strict,test_outcome,test_events,test_logs,test_stream_schema_notice,test_init_env}.py`, `tests/integration/{test_publish_ui,test_views,test_audit_helper,test_initdb,test_redis_acl}.py`, `tests/meta/{test_compose,test_compose_mission_ro,test_generated_fresh}.py` |
| A – commanding | `src/gs/simulator/**`, `src/gs/commanding/{args,decide,tracker}.py`, `src/gs/api/commands.py`, `tests/support/sim.py`, `tests/unit/test_sim_*.py`, `tests/unit/{test_cmd_args,test_cmd_decide,test_command_state}.py`, `tests/integration/{test_sim_commands,test_simulator,test_sim_fault_api,test_commands_api,test_cmd_tracker}.py` |
| B – stream | `src/gs/alarms/**`, `src/gs/ingest/{worker,fold,store,acks,mission,main,liveness}.py`, `src/gs/api/{app,deps,events,hub,alarms,server}.py`, `tests/support/{ingest,alarms}.py`, `tests/unit/{test_engine,test_checks_roc,test_checks_limit,test_acks,test_ingest_status}.py`, `tests/integration/{test_ui_publish,test_alarm_ack_api,test_alarms_api,test_sse,test_hub,test_resync,test_mission_db_api,test_health}.py` |
| C – console | `ui/**` except `ui/src/api/schema.d.ts` |
| D – data | `src/gs/api/{auth,sessions,report,telemetry}.py`, `perf/**` except `perf/__init__.py`, `docs/operator-guide.md`, `docs/dev-guide.md`, `docs/research/S2-schemathesis-spike.md`, `tests/integration/{test_auth,test_sessions,test_downsample}.py`, `tests/unit/{test_perf_stats,test_run_n,test_report_csv}.py`, `tests/meta/test_guides.py`; Task 24 may add `schemathesis` to the dev group of `pyproject.toml` + `uv.lock` (ruling in this plan) |
| Z – integration | anything after the four merges; new: `e2e/*.spec.ts`, `e2e/helpers.ts`, `tests/integration/{test_contracts,test_audit_complete,test_audit_append_only,test_rest_fuzz}.py`, `tests/system/{test_resync,test_json_logs,test_sse_contract,test_broker_banner}.py`, `docs/img/**`, `docs/perf/**`, doc rulings |

**Cross-track contract (decided now; Track 0 encodes what is code).**

| Contract | Value |
| --- | --- |
| Redis | `tc:<v>` group `sim`, consumer `sim`; `tcack:<v>` group `ingest` (owner's consumer name); `ui:events` entries = one `UiEvent` (`tm · alarm · cmd · link · resync`); `tm:dlq` also takes `schema` records for `tcack` (ingest) and `ui:events` (api); `confirm:{token}`, `cmdlock:{v}:{key}` as in the design values |
| Event kinds and keys (new) | `LATE_VERIFICATION/<cmd_id>` (vehicle, `t_onboard` = echo time, details `{cmd_id, name, failure_reason}`); `INTERFACE_MISMATCH/<tc·tcack·ui-event>/<schema_sha12>` (vehicle NULL, details `{stream, consumer, detail}`) |
| Audit `details` | every row: `{"vehicle"?, "cmd_id"?, "alarm_id"?, "request_id"?, "session_id"?, …}`, times via `iso_z`; actor = session user, or `system` for the owner and the API watcher |
| Who publishes what | worker: `tm`, `link` (every event of a committed outcome; clients dedupe by key), `alarm` (one `AlarmView` per alarm touched by a commit), `cmd` (one `CommandView` per command the tracker changed), `resync` after start and every takeover; API: nothing to `ui:events` (§3.7 ACL), it pushes `ground` and `hb` itself |
| Tracker call site (Task 3) | `_own` calls `await track_pass(ctx, conn, vehicle, caught_up)` once per loop pass after the batch and `_silence`, `caught_up = claim_idle is None and len(entries) < COUNT`; outside the Redis `except` |

**Not in this plan (Gate 4 or after the 47):** replay page and replay SSE (D-REP-*, D-UI-16), report refreeze (D-DB-10 second half, needs an `UPDATE` grant on `session_report`), the alarm-quality report section (D-ALM-15) and tuning study (D-ALM-16), p95 confidence intervals (D-PERF-10), run P and the scaling analysis, the `except`-coverage meta test (D-ENG-21 part 2), README, `docs/atp-report.md`, mode preconditions `MODE_*` (D-EXT-08), Since… summary (D-EXT-21), rendered interface docs and the duplicates meta test (D-EXT-17).

---

## Track 0 – shared contracts

### Task 1: Stream, SSE and ui:events interface models

**Files:**
- Create: `src/gs/common/interfaces/streams.py`, `src/gs/common/interfaces/ui_event.py`, `src/gs/common/interfaces/render.py`, `src/gs/common/uievents.py`, `src/gs/commanding/__init__.py`, `src/gs/commanding/outcome.py`, `scripts/stream_schema_notice.py`, `tests/unit/test_outcome.py`, `tests/unit/test_stream_schema_notice.py`, `tests/integration/test_publish_ui.py`
- Modify: `src/gs/common/interfaces/{sse,dlq,__init__,export}.py`, `src/gs/ingest/events.py`, `src/gs/ingest/worker.py` (tm publish through `publish_ui`), `tests/unit/test_interface_models_strict.py`, `tests/unit/test_events.py`, `.github/workflows/ci.yml` (lint: `fetch-depth: 0`, then `uv run python scripts/stream_schema_notice.py`), `interfaces/**`, `ui/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `StrictModel`, `UtcTime`, `VehicleId`, `U32`, `Mode` (`base.py`); `AlarmState`, `TransitionKind` from `gs.alarms.contract`; `iso_z`.
- Produces `gs.common.interfaces.streams` (all `extra="forbid"`):
  - `ArgValue = str | int | float`; `Tc`: `cmd_id: UUID`, `name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]`, `args: dict[str, ArgValue]`, `issued_by: Annotated[str, Field(min_length=1)]`, `issued_at: UtcTime`, `reason: str | None`.
  - `TcAck`: `cmd_id: UUID`, `accepted: bool`, `code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,31}$")]`, `reason: str | None`, `t_onboard: UtcTime`; constants `ACK_OK = "OK"`, `ACK_INVALID_ARGS = "INVALID_ARGS"`, `ACK_SCHEMA = "SCHEMA"`.
- Produces `gs.common.interfaces.sse` (each `_Sse`, `kind` defaulted, R4):
  - `CmdState = Literal["QUEUED","SENT","RECEIVED","VERIFIED","FAILED"]`, `FailureReason = Literal["SEND_ERROR","REJECTED","NOT_RECEIVED","NOT_VERIFIED","ACK_INVALID"]`, `Outcome = Literal["NOT_SENT","NOT_EXECUTED","UNKNOWN","RECEIVED_LATE","EXECUTED_LATE"]`.
  - `TransitionView`: `key: str`, `kind: TransitionKind`, `t: UtcTime` (trigger `t_onboard`, or the apply time of an ACK), `alert: bool`, `by_user: str`, `note: str | None`, `check_kind: str | None`, `level: str | None`, `value: float | None`.
  - `AckRequestView`: `request_id: UUID`, `by_user: str`, `status: Literal["pending","applied","superseded","rejected"]`, `status_reason: str | None`.
  - `AlarmView`: `alarm_id: UUID`, `vehicle: VehicleId`, `param: str`, `state: AlarmState`, `severity: Literal["YELLOW","RED"]`, `level: str` (`"NOMINAL"`, `"RED"`, or `"<SEV>_<SIDE>"`, side ∈ LOW HIGH RISING FALLING), `trigger_check: str`, `limits_sha256: str`, `set_sha256: str`, `opened_at: UtcTime`, `closed_at: UtcTime | None`, `last: TransitionView`, `ack_request: AckRequestView | None`.
  - `CommandView`: `cmd_id: UUID`, `vehicle: VehicleId`, `name: str`, `args: dict[str, ArgValue]`, `hazardous: bool`, `reason: str | None`, `issued_by: str`, `state: CmdState`, `failure_reason: FailureReason | None`, `queued_at: UtcTime`, `sent_at`, `received_at`, `verified_at`, `failed_at`, `late_verified_at`, `late_ack_at: UtcTime | None`, `late_ack_accepted: bool | None`, `outcome: Outcome | None`.
  - `EventView`: `key: str`, `vehicle: VehicleId | None`, `kind: str`, `t_onboard: UtcTime | None`, `details: dict[str, JsonValue]`.
  - Events: `Alarm(alarm: AlarmView)`, `Cmd(command: CommandView)`, `Link(event: EventView)`, `Ground(broker: Literal["down","up"])`; `Tm` gains `frozen: bool = False`; `Hb` gains `processing: dict[str, bool] = Field(default_factory=dict)`; `Snapshot` gains `alarms: list[AlarmView] = []`, `commands: list[CommandView] = []`, `events: list[EventView] = []` (defaults keep Gate 2 producers valid until Task 11).
- Produces `gs.common.interfaces.ui_event`: `Resync(kind="resync", vehicle: VehicleId)`; `UiEvent = RootModel[Annotated[Tm | Alarm | Cmd | Link | Resync, Field(discriminator="kind")]]`.
- Produces `gs.common.interfaces.render`: `render(model: type[BaseModel]) -> str` (moved verbatim from `export.py`), `schema_sha12(model) -> str` = first 12 hex of sha256 of `render(model)` UTF-8 bytes (= the committed file's sha256).
- Produces `gs.common.interfaces.dlq.schema_reject(raw: str, stream: str, entry_id: str, detail: str, now: datetime) -> DlqRecord` (`reason="schema"`, `deliveries=1`, `first_seen` = the entry id's ms time).
- Produces `gs.common.uievents`: `UI_EVENTS = "ui:events"`, `UI_MAXLEN = 3000`, `async def publish_ui(r: redis.asyncio.Redis, msgs: Sequence[Tm | Alarm | Cmd | Link | Resync]) -> None` (one non-transactional pipeline, `XADD ui:events MAXLEN ~ 3000 * data <model_dump_json()>`; no-op for an empty list). The worker's tm publish uses it (behaviour unchanged).
- Produces `gs.commanding.outcome.outcome(failure_reason: FailureReason | None, late_verified_at: datetime | None, late_ack_accepted: bool | None) -> Outcome | None` (D-CMD-02: None when not failed; SEND_ERROR → NOT_SENT; REJECTED → NOT_EXECUTED; NOT_RECEIVED / NOT_VERIFIED / ACK_INVALID: echo → EXECUTED_LATE, else late `accepted=false` → NOT_EXECUTED, late `accepted=true` → RECEIVED_LATE, else UNKNOWN).
- Produces `gs.ingest.events`: `EventKind` + `"LATE_VERIFICATION"`, `"INTERFACE_MISMATCH"`; `late_key(cmd_id: UUID) -> str`, `mismatch_key(stream_kind: Literal["tc","tcack","ui-event"], sha12: str) -> str`.
- `EXPORTS` adds `schemas/tc.schema.json`, `schemas/tcack.schema.json`, `schemas/ui-event.schema.json`, `schemas/sse/{alarm,cmd,link,ground}.schema.json`; `INTERFACE_MODELS` adds `Tc, TcAck, UiEvent, Alarm, Cmd, Link, Ground`.
- `scripts/stream_schema_notice.py`: `STREAMS = ("frame","tc","tcack","dlq","ui-event")`; `changed(base: Mapping[str, str], head: Mapping[str, str]) -> list[str]`; `main()` reads `git show origin/main:interfaces/schemas/<n>.schema.json` (missing = changed) and the working tree, prints `stream interface changed: restart all services together (<names>)` when non-empty, always exits 0 (D-ING-27).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_interface_models_strict.py (add)
def test_tc_requires_issued_by_and_forbids_extra():
    ok = {"cmd_id": str(uuid4()), "name": "isolate_valve", "args": {"valve": "A"},
          "issued_by": "fc1", "issued_at": "2026-11-15T10:00:00.000000Z", "reason": None}
    Tc.model_validate_json(json.dumps(ok))
    for bad in ({**ok, "issued_by": ""}, {**ok, "extra": 1}, {k: v for k, v in ok.items() if k != "cmd_id"}):
        with pytest.raises(ValidationError): Tc.model_validate_json(json.dumps(bad))
def test_tcack_code_pattern(): ...           # "OK", "INVALID_ARGS", "BUSY" pass; "ok", "", "A"*33 fail
def test_ui_event_discriminates():
    assert isinstance(UiEvent.model_validate_json('{"kind":"resync","vehicle":"NYX-TA1"}').root, Resync)
    with pytest.raises(ValidationError): UiEvent.model_validate_json('{"kind":"hb","server_ms":1}')
def test_new_defaults_keep_gate2_shapes():
    assert Hb(server_ms=1).processing == {} and Snapshot(vehicles={}).alarms == []
    assert Tm.model_validate(TM_JSON).frozen is False
def test_schema_sha12_matches_committed_file():
    data = (REPO / "interfaces/schemas/tcack.schema.json").read_bytes()
    assert schema_sha12(TcAck) == hashlib.sha256(data).hexdigest()[:12]

# tests/unit/test_outcome.py — every D-CMD-02 branch
T = datetime(2026, 11, 15, tzinfo=UTC)
@pytest.mark.parametrize("reason,late_v,late_ack,want", [
    (None, None, None, None), ("SEND_ERROR", None, None, "NOT_SENT"), ("REJECTED", T, True, "NOT_EXECUTED"),
    ("NOT_RECEIVED", T, None, "EXECUTED_LATE"), ("NOT_RECEIVED", T, False, "EXECUTED_LATE"),
    ("NOT_RECEIVED", None, False, "NOT_EXECUTED"), ("NOT_RECEIVED", None, True, "RECEIVED_LATE"),
    ("NOT_VERIFIED", None, None, "UNKNOWN"), ("ACK_INVALID", T, None, "EXECUTED_LATE"),
    ("ACK_INVALID", None, None, "UNKNOWN")])
def test_outcome_table(reason, late_v, late_ack, want): assert outcome(reason, late_v, late_ack) == want

# tests/unit/test_events.py (add)
def test_new_keys():
    u = UUID(int=7)
    assert late_key(u) == f"LATE_VERIFICATION/{u}"
    assert mismatch_key("tcack", "0123456789ab") == "INTERFACE_MISMATCH/tcack/0123456789ab"

# tests/unit/test_stream_schema_notice.py
def test_changed_schema_named(): assert changed({"tc": "a", "frame": "f"}, {"tc": "b", "frame": "f"}) == ["tc"]
def test_unchanged_is_silent(capsys): ...   # main() with equal trees prints nothing, exit 0
def test_missing_on_base_counts_as_changed(): assert changed({}, {"tcack": "x"}) == ["tcack"]

# tests/integration/test_publish_ui.py
async def test_publish_ui_one_entry_per_message_matching_schema():
    v = vid(); r = aclient("ingest")
    await publish_ui(r, [Resync(vehicle=v), Link(event=EventView(key=f"GAP/{v}/x/1", vehicle=v, kind="GAP",
                                                                 t_onboard=None, details={"lost": 3}))])
    mine = [json.loads(d["data"]) for _, d in stream_entries(UI_EVENTS) if v in d["data"]][-2:]
    assert [m["kind"] for m in mine] == ["resync", "link"]
    for m in mine: Draft202012Validator(load_schema("ui-event")).validate(m)
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/unit/test_interface_models_strict.py tests/unit/test_outcome.py tests/unit/test_events.py tests/unit/test_stream_schema_notice.py tests/integration/test_publish_ui.py -q`
Expected: import errors (`Tc`, `outcome`, `late_key`, `publish_ui` not defined).

- [ ] **Step 3: Implement** the models and functions named in Interfaces; move `render` into `render.py` and import it in `export.py`; run `make gen` and commit the regenerated `interfaces/**` and `schema.d.ts` with this task (R18).

- [ ] **Step 4: Run the gate**
Run: `uv run pytest --cov=gs --cov-fail-under=80 && uv run ruff check && uv run ruff format --check && uv run mypy --strict src tests scripts && make gen && git diff --exit-code -- interfaces ui/src/api && (cd ui && npx tsc --noEmit && npm test)`
Expected: all green.

- [ ] **Step 5: Commit**
`git commit -m "feat: add the tc, tcack, alarm, cmd, link, ground and resync interface models and the outcome rule"`

---

### Task 2: DDL, ACL, compose, JSON logs and shared helpers

**Files:**
- Create: `src/gs/common/logs.py`, `perf/__init__.py`, `tests/unit/test_logs.py`
- Modify: `migrations/02-tables.sql`, `scripts/init_env.py`, `docker-compose.yml`, `Makefile`, `.github/workflows/ci.yml` (mypy paths add `perf`), `src/gs/simulator/main.py`, `src/gs/ingest/main.py`, `src/gs/common/migrate.py`, `src/gs/api/server.py` (logging only), `tests/unit/test_init_env.py`, `tests/integration/test_redis_acl.py`, `tests/integration/test_initdb.py`, `tests/meta/test_compose.py`, `tests/meta/test_compose_mission_ro.py`, `tests/support/{pg,redisx,http}.py`

**Interfaces:**
- DDL (design §3.2): `commands.failure_reason` CHECK gains `'ACK_INVALID'` (D-ING-27; the on-disk file lags §3.2); `alarm_events` gains `value double precision` after `severity` (the triggering reading: sample value for a limit check, slope per second for a ROC check; ruling R-b, D-UI-09 text needs it).
- ACL (§3.7 final): the `api` user line gains `(~tm:dlq +xadd)`; `test_init_env` asserts each generated line equals §3.7 with the hashes substituted. Worktrees and the shared stack need `make reset` (or `docker compose restart redis`) to load it.
- Compose: `simulator` gets `MISSION_DB: /mission` and `./mission:/mission:ro` (it enforces the command arg bounds, D-ENG-17); `test_compose_mission_ro` covers `simulator`.
- `gs.common.logs`: `class JsonFormatter(logging.Formatter)` emitting one JSON object per line with `t` (`iso_z` of `record.created`), `level`, `service`, `logger`, `msg`, plus `vehicle` and `seq` when the record carries them (`extra={"vehicle": v, "seq": s}`) and `exc` when `exc_info` is set; `def setup_logging(service: str, level: int = logging.INFO) -> None` (replaces the root handlers). Every `main()` calls it instead of `basicConfig`; `build_server` passes `log_config=None` so uvicorn's loggers propagate to the JSON root (D-ENG-07, ENG-05).
- `Makefile`: `perf:` → `uv run python -m perf.run_n` (smoke: `make -n perf`, R16); dead `--profile ingest2` arguments removed if Gate 3a left any.
- Helpers: `tests/support/pg.py` adds `audit_rows(action: str | None = None, actor: str | None = None, since: datetime | None = None) -> list[dict[str, Any]]`, `commands_for(vehicle: str) -> list[dict[str, Any]]`, `ack_requests_for(vehicle: str) -> list[dict[str, Any]]`, `open_alarm(vehicle: str, param: str) -> dict[str, Any] | None`, `insert_rows(vehicle: str, rows: Sequence[Mapping[str, Any]]) -> None` (telemetry as gs_app). `tests/support/redisx.py` adds `stream_entries(stream: str, start: str = "-") -> list[tuple[str, dict[str, str]]]` (admin, decoded) and `xadd_data(stream: str, data: str, user: str = "admin") -> str`. `tests/support/http.py` adds `@asynccontextmanager async def authed(app: FastAPI, user: str = "fc1", password: str = FC1_PW) -> AsyncIterator[httpx.AsyncClient]` (ASGI transport, lifespan run, logged in, `CLIENT_HEADERS` set).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_logs.py
def test_json_line_with_vehicle_and_seq(capsys):
    setup_logging("gs-ingest"); logging.getLogger("x").warning("gap", extra={"vehicle": "NYX-TA1", "seq": 42})
    rec = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert (rec["service"], rec["vehicle"], rec["seq"], rec["msg"], rec["level"]) == ("gs-ingest", "NYX-TA1", 42, "gap", "WARNING")
    assert rec["t"].endswith("Z")
def test_exception_is_one_line(capsys): ...  # log.exception → one JSON line with "exc" holding the traceback
def test_no_vehicle_key_when_absent(capsys): ...

# tests/integration/test_initdb.py (add)
def test_ack_invalid_and_alarm_value_columns():
    with throwaway_db() as db, psycopg.connect(owner_dsn(db)) as c:
        assert column_types(c, "alarm_events")["value"] == "double precision"
        c.execute("insert into commands (cmd_id, vehicle, name, args, args_hash, hazardous, issued_by, state,"
                  " failure_reason, queued_at) values (gen_random_uuid(), 'V', 'n', '{}', 'h', false, 'u',"
                  " 'FAILED', 'ACK_INVALID', now())")

# tests/integration/test_redis_acl.py (add)
def test_api_may_dlq_but_not_frames():
    api = client("api"); api.xadd("tm:dlq", {"data": "{}"})
    with pytest.raises(redis.exceptions.NoPermissionError): api.xadd("tm:NYX-TA1", {"data": "{}"})

# tests/meta/test_compose.py: ENV["simulator"] gains "MISSION_DB"; test_compose_mission_ro parametrize adds "simulator"
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_logs.py tests/integration/test_initdb.py tests/integration/test_redis_acl.py tests/meta -q` → import error / column missing / NOPERM on `tm:dlq` / missing env.
- [ ] **Step 3: Implement** as in Interfaces; `make reset && make up`.
- [ ] **Step 4: Run the gate** (Task 1 Step 4) plus `uv run pytest tests/system -q` (stack restarted with JSON logs).
- [ ] **Step 5: Commit** `git commit -m "chore: align DDL and ACL with design 3.2/3.7, mount mission/ into the simulator, log JSON with vehicle and seq"`

---

### Task 3: REST surface, audit, view loaders and the tracker call site

**Files:**
- Create: `src/gs/api/bodies.py`, `src/gs/api/deps.py`, `src/gs/api/commands.py`, `src/gs/api/alarms.py`, `src/gs/api/sessions.py`, `src/gs/api/telemetry.py` (route stubs), `src/gs/common/audit.py`, `src/gs/common/views.py`, `src/gs/commanding/tracker.py` (stub), `tests/integration/test_views.py`, `tests/integration/test_audit_helper.py`
- Modify: `src/gs/api/app.py` (login/logout/me moved to `auth.py`; routers included into `ROUTER`; `Health.status` gains `"degraded"`; routes use `deps.mission`), `src/gs/api/auth.py`, `src/gs/ingest/worker.py` (`Ctx.counters`, call site), `src/gs/ingest/liveness.py`, `tests/unit/test_ingest_status.py`, `tests/integration/test_auth.py` (imports only), `tests/integration/test_openapi.py`, `interfaces/openapi.json`, `ui/src/api/schema.d.ts`

**Interfaces:**
- `gs.api.deps`: `User = Annotated[str, Depends(current_user)]`; `errors(*codes: int) -> dict[int | str, dict[str, object]]` (moved `_errors`); `mission(request: Request) -> MissionDb` (returns `request.app.state.mission`; Task 12 makes it live); `vehicles(request) -> tuple[str, ...]`.
- `gs.api.bodies` (all `StrictModel`, `extra="forbid"`): `ErrorBody(error, message)` (moved), `LoginRequest`, `Me`, `Health(status: Literal["ok","degraded","down"], checks)`; `CommandRequest(vehicle: VehicleId, name: str, args: dict[str, ArgValue], confirm_token: str | None = None, reason: str | None = None)`; `CommandAccepted(cmd_id: UUID)`; `CommandConflict(error, message, hazard: str | None = None, confirm_token: str | None = None, expires_in_s: int | None = None, by: str | None = None, seconds_ago: float | None = None, cmd_id: UUID | None = None)` (every 409 of `POST /api/commands`, ruling R-s); `AckRequest(request_id: UUID, note: str)`; `AckAccepted(request_id: UUID, status: Literal["pending","applied","superseded","rejected"])`; `SessionStart(vehicle: VehicleId, name: Annotated[str, Field(min_length=1, max_length=200)])`; `SessionView(id: int, vehicle, name, conductor, started_at: UtcTime, ended_at: UtcTime | None, ended_by: str | None)`; `ParamStats(unit: str, min: float | None, max: float | None, avg: float | None, n: int)`; `AlarmTransition(alarm_id: UUID, param: str, transition: TransitionView)`; `SessionReport(session: SessionView, complete: bool, note: str | None, frozen_at: UtcTime, duration_s: float, frames: int, expected: int, parameters: dict[str, ParamStats], link_events: list[EventView], alarms: list[AlarmTransition], commands: list[CommandView])`; `ReportConflict(error, message, frames: int | None = None, expected: int | None = None)`; `Bucket(t: UtcTime, lo: float, hi: float, mean: float, first: float, last: float, n: int)`.
- Route stubs (each registered on its module's `router`, included into `app.ROUTER`, depending on `User`, raising `ApiError(501, "NOT_IMPLEMENTED", …)`; signatures and `responses` are final):

| Route | Signature | Declared statuses |
| --- | --- | --- |
| `POST /api/commands` (`commands.py`) | `post_command(body: CommandRequest, request, user) -> CommandAccepted`, status 202 | 401 403 409 `CommandConflict` 422 503 |
| `GET /api/commands` | `list_commands(request, user, vehicle: VehicleId \| None = None, since: UtcTime \| None = None) -> list[CommandView]` | 401 503 |
| `GET /api/alarms` (`alarms.py`) | `list_alarms(request, user, vehicle=None, severity: Literal["YELLOW","RED"] \| None = None, state: AlarmState \| None = None) -> list[AlarmView]` | 401 503 |
| `POST /api/alarms/{alarm_id}/ack` | `post_ack(alarm_id: UUID, body: AckRequest, request, user) -> AckAccepted`, 202 | 401 403 404 409 422 503 |
| `POST /api/sessions` (`sessions.py`) | `start_session(body: SessionStart, request, user) -> SessionView`, 201 | 401 403 409 422 503 |
| `POST /api/sessions/{session_id}/stop` | `stop_session(session_id: int, request, user) -> SessionView` | 401 403 404 409 503 |
| `GET /api/sessions` | `list_sessions(request, user) -> list[SessionView]` | 401 503 |
| `GET /api/sessions/{session_id}/report` | `get_report(session_id: int, request, user) -> SessionReport` | 401 404 409 `ReportConflict` 503 |
| `GET /api/sessions/{session_id}/report.csv` | `get_report_csv(...) -> Response` (`text/csv`) | 401 404 409 `ReportConflict` 503 |
| `GET /api/telemetry/{vehicle}/{param}` (`telemetry.py`) | `get_buckets(vehicle: VehicleId, param: str, request, user, t_from: UtcTime = Query(alias="from"), t_to: UtcTime = Query(alias="to"), width: int = 1500) -> list[Bucket]` | 400 401 503 |
| `GET /api/mission-db/frame-schema` (`app.py`) | `frame_schema(request, user) -> dict[str, Any]` (D-EXT-19) | 401 503 |

- `gs.common.audit`: `Action = Literal["LOGIN","LOGIN_FAILED","LOGOUT","CMD_REQUESTED","CMD_CONFIRM_REQUIRED","CMD_REJECTED","CMD_STATE","CMD_LATE_ACK","CMD_LATE_VERIFICATION","ALARM_ACK","ALARM_ACK_APPLIED","LIMITS_RELOADED","SESSION_START","SESSION_STOP","REPORT_REFROZEN"]` (D-CMD-08 verbatim); `async def audit(conn: psycopg.AsyncConnection, actor: str, action: Action, reason: str | None = None, details: Mapping[str, Any] | None = None) -> None` (in the caller's transaction; details through `details_json`).
- `gs.common.views` (read-only SQL → interface models; one source for the worker publisher, the snapshot, REST and the report):
  - `async def command_views(conn, *, vehicle: str | None = None, since: datetime | None = None, cmd_ids: Sequence[UUID] | None = None, limit: int = 50) -> list[CommandView]` — newest `queued_at` first; `outcome` from `gs.commanding.outcome`.
  - `async def alarm_views(conn, *, vehicle: str | None = None, severity: str | None = None, state: str | None = None, alarm_ids: Sequence[UUID] | None = None, open_only: bool = True) -> list[AlarmView]` — `last` = newest `alarm_events` row of the alarm; `level` = `"NOMINAL"` for RTN_UNACK / CLEARED, else the newest non-null `alarm_events.level`; `ack_request` = the newest `alarm_ack_request` of the alarm (any status) or None; newest `opened_at` first.
  - `async def event_views(conn, *, vehicle: str | None = None, kinds: Sequence[str] | None = None, since: datetime | None = None, until: datetime | None = None, limit: int = 50) -> list[EventView]` — newest first by `coalesce(t_onboard, created_at)`; vehicle filter includes vehicle-NULL rows (LIMITS_REJECTED, INTERFACE_MISMATCH).
- `gs.commanding.tracker`: `async def track_pass(ctx: "Ctx", conn: psycopg.AsyncConnection, vehicle: str, caught_up: bool) -> None` — stub body `return` (Track A Task 7); `Ctx` imported under `TYPE_CHECKING`.
- `gs.ingest.worker.Ctx` gains `counters: Counter[str] = field(default_factory=Counter)` (keys `cmd_tracker_errors`, `interface_mismatch`); the call site of the contract table. `liveness.ingest_status(seen, owned, now, used_memory, counters: Mapping[str, int])` adds check `cmd_tracker_errors` and returns `degraded` while `counters["interface_mismatch"] > 0` (D-ING-27, D-CMD-05).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_openapi.py (add)
def test_rest_surface_is_final():
    paths = build_openapi(ROUTER.routes)["paths"]
    for p in ("/api/commands", "/api/alarms", "/api/alarms/{alarm_id}/ack", "/api/sessions",
              "/api/sessions/{session_id}/stop", "/api/sessions/{session_id}/report",
              "/api/sessions/{session_id}/report.csv", "/api/telemetry/{vehicle}/{param}",
              "/api/mission-db/frame-schema"):
        assert p in paths
    conflict = paths["/api/commands"]["post"]["responses"]["409"]["content"]["application/json"]["schema"]
    assert conflict["$ref"].endswith("/CommandConflict")
# test_auth.py::test_every_route_requires_session already walks the route table: the stubs must pass it unchanged

# tests/integration/test_audit_helper.py
async def test_audit_row_with_iso_details():
    actor = f"t-{vid()}"
    async with await psycopg.AsyncConnection.connect(app_dsn("gs"), autocommit=True) as c:
        await audit(c, actor, "CMD_REQUESTED", "test reason", {"at": datetime(2026, 11, 15, tzinfo=UTC)})
    (row,) = audit_rows(actor=actor)
    assert (row["action"], row["reason"], row["details"]["at"]) == ("CMD_REQUESTED", "test reason", "2026-11-15T00:00:00.000000Z")

# tests/integration/test_views.py — rows inserted by the test as gs_app
async def test_command_view_outcome_and_order(): ...      # two rows; newest first; FAILED NOT_RECEIVED + late_verified_at → EXECUTED_LATE
async def test_alarm_view_level_last_and_ack_request(): ...  # RAISE(level YELLOW_LOW, value 97.9) then ESCALATE(RED_LOW): level RED_LOW, last.kind ESCALATE, last.value set; pending request → ack_request.status pending; RTN_UNACK alarm → level NOMINAL
async def test_alarm_views_open_only_and_filters(): ...   # CLEARED excluded unless open_only=False; severity/state filters
async def test_event_views_include_vehicle_null(): ...    # LIMITS_REJECTED (vehicle NULL) listed for any vehicle filter
async def test_views_validate_against_schemas(): ...      # model_dump(mode="json") of each view validates with the exported sse schemas

# tests/unit/test_ingest_status.py (add)
def test_interface_mismatch_degrades_and_tracker_errors_reported(): ...
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/integration/test_openapi.py tests/integration/test_audit_helper.py tests/integration/test_views.py tests/unit/test_ingest_status.py -q` → missing paths / import errors.
- [ ] **Step 3: Implement** as in Interfaces; `make gen`; the generated `openapi.json` and `schema.d.ts` are final for the parallel phase.
- [ ] **Step 4: Run the gate** (Task 1 Step 4) and `uv run pytest tests/system -q` (the call site runs every pass).
- [ ] **Step 5: Commit** `git commit -m "feat: freeze the day-3 REST surface, add the audit helper, view loaders and the tracker call site"`

---

## Track A – commanding

### Task 4: Simulator command path, cabin effects, pause and hold_exec

**Files:**
- Create: `src/gs/simulator/commands.py`, `src/gs/commanding/args.py`, `tests/unit/test_cmd_args.py`, `tests/unit/test_sim_cabin.py`, `tests/unit/test_sim_tc.py`, `tests/integration/test_sim_commands.py`
- Modify: `src/gs/simulator/{frames,faults,main}.py`, `tests/support/sim.py`, `tests/unit/test_sim_faults.py`

**Interfaces:**
- Consumes: `Tc`, `TcAck`, `ACK_*`, `schema_sha12`, `gs.common.missiondb.load_dir`, `Command`, `EnumArg`, `NumArg`, `FrameBuffer`, `FaultBoard`.
- Produces `gs.commanding.args.check_args(cmd: Command | None, args: Mapping[str, object]) -> str | None` — None when valid; otherwise operator text: unknown command, missing required arg, unknown arg, enum value not listed, number outside `[min, max]`, wrong type. One function for the API (422 `INVALID_ARGS`) and the simulator (`INVALID_ARGS`), so the bounds are the same numbers (D-ENG-17, D-EXT-07).
- Produces `gs.simulator.commands`:
  - `def parse_tc(raw: str) -> Tc | tuple[UUID | None, str]` (model, or the readable `cmd_id` and the validation message).
  - `@dataclass(frozen=True) class Cabin: offset_kpa: float = 0.0; rate: float = 0.0; until: float | None = None` and `def cabin_tick(c: Cabin, dt: float, now: float) -> Cabin` (a positive rate stops at offset 0, never overshoots; a negative rate stops at `until`).
  - `def execute(tc: Tc, cabin: Cabin, leak_offset: float | None, now: float) -> Cabin` — `isolate_valve`: offset = current total offset (incl. the active leak's), rate `+0.5`; `vent_cabin`: rate `−2.0` until `now + duration_s`. The caller clears the `leak` fault on `isolate_valve`.
  - `class CommandBox` per vehicle: `inbox: asyncio.Queue[Tc]`, `held: tuple[tuple[float, Tc], ...]`, `last_cmd_id: UUID | None`, `schema_errors: int`; `def step(self, now: float, t: datetime, paused: bool, hold_s: float | None, dropout: bool) -> tuple[list[TcAck], Tc | None]` — at most one command executed per tick; while `paused` nothing is taken; a taken command is acked at once (`OK` or `INVALID_ARGS`); with `hold_s` it executes `hold_s` later; under `dropout` the acks are dropped but execution happens.
  - `async def tc_loop(r, vehicle: str, box: CommandBox, buf: FrameBuffer, stop: asyncio.Event) -> None` — `XREADGROUP GROUP sim sim COUNT 1 BLOCK 1000` on `tc:<v>` (group created with MKSTREAM); valid → `box.inbox`; invalid → `TcAck(accepted=False, code="SCHEMA")` appended to the buffer when `cmd_id` is readable, `schema_errors += 1`, log; XACK either way (D-ING-27, D-SIM-10).
- `tick_loop` gains `box: CommandBox | None`; each frame carries `last_cmd_id` = the command executed in an earlier tick (D-SIM-06); acks go through the same ordered `FrameBuffer` as `("tcack:<v>", json)`.
- Faults: `FaultName` adds `"pause"` and `"hold_exec"`, both `{duration_s}` required.
- Sim health: `degraded` while any `box.schema_errors > 0` (until restart, D-ING-27).
- `tests/support/sim.py` adds `send_tc(vehicle: str, name: str, args: Mapping[str, object], cmd_id: UUID | None = None) -> UUID` (XADD as `api`) and `tcacks(vehicle: str) -> list[dict[str, Any]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cmd_args.py
@pytest.mark.parametrize("name,args,ok", [
    ("isolate_valve", {"valve": "A"}, True), ("isolate_valve", {"valve": "C"}, False),
    ("isolate_valve", {}, False), ("isolate_valve", {"valve": "A", "x": 1}, False),
    ("vent_cabin", {"duration_s": 1}, True), ("vent_cabin", {"duration_s": 10.0}, True),
    ("vent_cabin", {"duration_s": 10.01}, False), ("vent_cabin", {"duration_s": "5"}, False),
    ("nope", {}, False)])
def test_check_args(name, args, ok):
    mdb = load_dir(MISSION_DIR); assert (check_args(mdb.commands.get(name), args) is None) is ok

# tests/unit/test_sim_cabin.py
def test_isolate_during_leak_recovers_at_half_kpa_per_s():
    c = execute(tc("isolate_valve", valve="A"), Cabin(), leak_offset=-4.0, now=0.0)
    for k in range(1, 81): c = cabin_tick(c, 0.1, k * 0.1)
    assert c.offset_kpa == 0.0 and c.rate >= 0          # −4.0 + 8 s × 0.5, clamped, no overshoot
def test_vent_three_seconds_drops_six(): ...         # offset −6.0 ± 1e-9, then constant

# tests/unit/test_sim_tc.py
def test_invalid_tc_nacked_with_schema_code():         # D-ING-27, requirements name
    u = uuid4(); got = parse_tc(json.dumps({"cmd_id": str(u), "name": "vent_cabin"}))
    assert isinstance(got, tuple) and got[0] == u
def test_tc_without_cmd_id_is_unreadable(): ...       # (None, msg): XACKed, no tcack, schema_errors 1
def test_one_command_per_tick(): ...                  # 3 queued → executed on 3 consecutive ticks; frame k+1 carries cmd k
def test_pause_holds_ack_and_execution(): ...         # pause 0.6 s: no ack, no execution for 6 ticks, then one per tick
def test_hold_exec_acks_now_executes_later(): ...     # ack at tick 0, last_cmd_id first seen 16 ticks later (hold 1.6 s)
def test_dropout_executes_but_loses_ack(): ...        # no tcack, frames after the dropout carry last_cmd_id
def test_invalid_args_acked_false(): ...              # vent_cabin 11 s → accepted=false INVALID_ARGS, not executed

# tests/integration/test_sim_commands.py (real Redis, in-process sim on a vid())
async def test_isolate_valve_acked_and_echoed(): ...  # tcack OK within 0.3 s, a tm frame with last_cmd_id == cmd_id
async def test_schema_invalid_tc_degrades_health(): ...  # SCHEMA tcack, entry acked, sim /healthz degraded
async def test_tcack_entries_match_schema(): ...       # every tcack validates against interfaces/schemas/tcack.schema.json
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_cmd_args.py tests/unit/test_sim_cabin.py tests/unit/test_sim_tc.py tests/integration/test_sim_commands.py -q` → import errors.
- [ ] **Step 3: Implement** as in Interfaces. The simulator loads `MISSION_DB` once at start (`SimSettings.mission_db`); a bound changed in `commands.yaml` reaches the simulator at its next restart (ruling R-o; dev guide).
- [ ] **Step 4: Run the gate** (Task 1 Step 4) and `uv run pytest tests/system -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: execute commands in the simulator with tcack, last_cmd_id, isolate/vent effects, pause and hold_exec"`

---

### Task 5: Pure command decision

**Files:**
- Create: `src/gs/commanding/decide.py`, `tests/unit/test_cmd_decide.py`, `tests/unit/test_command_state.py`

**Interfaces:**
- Consumes: `CmdState`, `FailureReason`, `Action`.
- Produces `gs.commanding.decide`:
  - `EARLIER: dict[CmdState, tuple[CmdState, ...]] = {"SENT": ("QUEUED",), "RECEIVED": ("QUEUED", "SENT"), "VERIFIED": ("QUEUED", "SENT", "RECEIVED"), "FAILED": ("QUEUED", "SENT", "RECEIVED")}` (D-CMD-11 monotonic guards).
  - `@dataclass(frozen=True) class CmdRow: cmd_id: UUID; name: str; state: CmdState; failure_reason: FailureReason | None; queued_at: datetime; sent_at: datetime | None; received_at: datetime | None; failed_at: datetime | None; late_verified_at: datetime | None; late_ack_at: datetime | None; receive_s: float; verify_s: float`.
  - `@dataclass(frozen=True) class AckSeen: accepted: bool; code: str; t: datetime; invalid: bool = False` (`invalid` = the entry failed `TcAck`, D-ING-27).
  - `@dataclass(frozen=True) class Change: state: CmdState | None = None; failure_reason: FailureReason | None = None; received_at: datetime | None = None; verified_at: datetime | None = None; failed_at: datetime | None = None; late_verified_at: datetime | None = None; late_ack_at: datetime | None = None; late_ack_accepted: bool | None = None; guard: tuple[CmdState, ...] = (); audits: tuple[Action, ...] = (); late_verification: bool = False` (unset fields stay unchanged; `guard` = `EARLIER[state]`, or `("FAILED",)` for late evidence).
  - `def decide(cmd: CmdRow, ack: AckSeen | None, echo_t: datetime | None, now: datetime, caught_up: bool, late_window_s: float = 600.0) -> Change | None`.
- Rule (evidence timestamps decide, pass timing never does; ruling R-j). `R = coalesce(sent_at, queued_at) + receive_s`, `V = sent_at + verify_s`:
  1. QUEUED/SENT: `ack.invalid` → FAILED ACK_INVALID (`failed_at = now`); accepted ack with `t ≤ R` → RECEIVED (`received_at = ack.t`), then rule 2 in the same call; rejected ack with `t ≤ R` → FAILED REJECTED (`failed_at = ack.t`); echo with `echo_t ≤ R` and no ack → VERIFIED (`verified_at = echo_t`, `received_at` stays NULL); else, only if `caught_up and now ≥ R` → FAILED NOT_RECEIVED (`failed_at = now`) plus any later ack as `late_ack_*` and any echo as `late_verified_at`.
  2. RECEIVED: echo `≤ V` → VERIFIED; else only if `caught_up and now ≥ V` → FAILED NOT_VERIFIED plus a later echo as `late_verified_at`.
  3. FAILED with NOT_RECEIVED / NOT_VERIFIED / ACK_INVALID and `now − failed_at ≤ late_window_s`: first echo → `late_verified_at` + `late_verification=True` + audit `CMD_LATE_VERIFICATION`; first ack after NOT_RECEIVED or ACK_INVALID → `late_ack_at`, `late_ack_accepted` + audit `CMD_LATE_ACK`.
  4. Every state change carries audit `CMD_STATE`; VERIFIED, REJECTED and SEND_ERROR rows return None.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cmd_decide.py — table test of every branch; S = sent_at
S = datetime(2026, 11, 15, 10, tzinfo=UTC); s = lambda x: S + timedelta(seconds=x)
def row(state="SENT", **kw): return CmdRow(UUID(int=1), "isolate_valve", state, None, s(-0.1), S, None, None, None, None, 5.0, 15.0) if not kw else replace(row(state), **kw)
CASES = [  # (row, ack, echo, now, caught_up) -> (state, failure_reason, late_verified_at, late_ack_accepted)
  (row(), AckSeen(True, "OK", s(0.3)), s(0.4), s(0.5), True, ("VERIFIED", None, None, None)),
  (row(), AckSeen(True, "OK", s(0.3)), None, s(0.5), True, ("RECEIVED", None, None, None)),
  (row(), AckSeen(False, "INVALID_ARGS", s(0.3)), None, s(0.5), True, ("FAILED", "REJECTED", None, None)),
  (row(), None, s(2.0), s(2.1), True, ("VERIFIED", None, None, None)),
  (row(), None, None, s(5.0), True, ("FAILED", "NOT_RECEIVED", None, None)),
  (row(), None, None, s(9.0), False, None),                                   # not caught up: no timeout
  (row(), AckSeen(True, "OK", s(6.0)), s(6.1), s(6.2), True, ("FAILED", "NOT_RECEIVED", s(6.1), True)),  # pause 6
  (row("RECEIVED", received_at=s(0.1)), None, None, s(15.0), True, ("FAILED", "NOT_VERIFIED", None, None)),
  (row("FAILED", failure_reason="NOT_VERIFIED", failed_at=s(15)), None, s(16.1), s(16.2), True,
   ("FAILED", "NOT_VERIFIED", s(16.1), None)),                                # hold_exec 16 → LATE_VERIFICATION
  (row(), AckSeen(False, "X", s(0.2), invalid=True), None, s(0.3), True, ("FAILED", "ACK_INVALID", None, None)),
  (row("QUEUED", sent_at=None), None, None, s(4.95), True, ("FAILED", "NOT_RECEIVED", None, None)),  # coalesce(queued_at)
  (row("FAILED", failure_reason="NOT_RECEIVED", failed_at=s(5)), None, s(700), s(700), True, None),  # outside 600 s
]
@pytest.mark.parametrize("r,ack,echo,now,cu,want", CASES)
def test_decide_table(r, ack, echo, now, cu, want): ...   # apply the Change to r; compare the 4-tuple (None = no change)
def test_backlog_evidence_wins_over_timeout():             # Review Focus 1
    c = decide(row(), AckSeen(True, "OK", s(0.3)), s(0.4), now=s(8.0), caught_up=True)
    assert (c.state, c.verified_at, c.received_at) == ("VERIFIED", s(0.4), s(0.3))
def test_late_evidence_audits_and_event(): ...             # CMD_LATE_ACK, CMD_LATE_VERIFICATION, late_verification flag
def test_guards_are_monotonic(): ...                       # every Change with a state has guard == EARLIER[state]; late fields guard ("FAILED",)

# tests/unit/test_command_state.py
def test_earlier_table_is_the_lifecycle(): ...             # no state moves backwards; FAILED reachable from QUEUED/SENT/RECEIVED only
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/unit/test_cmd_decide.py tests/unit/test_command_state.py -q` → import error.
- [ ] **Step 3: Implement** `decide` as specified (pure, stdlib only).
- [ ] **Step 4: Run the gate** (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: decide command state from stored evidence with caught-up timeouts and late evidence"`

---

### Task 6: Command API – confirm token, interlock, send

**Files:**
- Modify: `src/gs/api/commands.py`
- Create: `tests/integration/test_commands_api.py`

**Interfaces:**
- Consumes: Task 3 stubs and bodies, `check_args`, `audit`, `command_views`, `Tc`, `deps.mission`, `deps.vehicles`.
- Produces in `gs.api.commands`: `CONFIRM_TTL_MS = 60_000`, `LOCK_TTL_MS = 2_000`, `REASON_MIN = 10`; `def args_hash(vehicle: str, name: str, args: Mapping[str, object]) -> str` (sha256 hex of `json.dumps({"vehicle","name","args"}, sort_keys=True, separators=(",", ":"))`); `def lock_key(vehicle: str, name: str, hazardous: bool, ah: str) -> str` (`cmdlock:{v}:{name}` or `cmdlock:{v}:{name}:{ah}`); `async def release_lock(r, key: str, owner: str) -> None` (`WATCH`, `GET` equals `cmd_id|user`, `MULTI DEL EXEC`).
- `post_command` order (D-CMD-06/07/11, §3.1): vehicle not in `deps.vehicles` or `check_args` fails → 422 `INVALID_ARGS` (no row, no Redis write) → hazardous without `confirm_token` → `SET confirm:{token} {user}|{ah} NX PX 60000`, audit `CMD_CONFIRM_REQUIRED`, 409 `HAZARDOUS_CONFIRMATION_REQUIRED {hazard, confirm_token, expires_in_s: 60}` → hazardous with token: `reason.strip()` shorter than 10 → 422 `REASON_REQUIRED`, token **not** consumed (ruling R-k); `GETDEL`; missing, other user or other `args_hash` → audit `CMD_REJECTED` + 409 `CONFIRMATION_INVALID` → interlock `SET cmdlock… cmd_id|user NX PX 2000`; taken → audit `CMD_REJECTED` (reason `DUPLICATE_COMMAND`) + 409 `DUPLICATE_COMMAND {by, seconds_ago, cmd_id}` (`seconds_ago` from the holder's `queued_at`, 0.0 if its row is not visible yet) → one transaction: `INSERT commands … state 'QUEUED'` + audit `CMD_REQUESTED` (reason) → `UPDATE … SET state='SENT', sent_at=clock_timestamp() WHERE cmd_id=%s AND state='QUEUED'` + audit `CMD_STATE` → `XADD tc:<v> * data <Tc>` → 202 `{cmd_id}`. XADD error → `UPDATE … FAILED SEND_ERROR WHERE state IN ('QUEUED','SENT')` + audit, `release_lock`, 503 `BROKER_DOWN`. Any Redis error before the INSERT → 503 `BROKER_DOWN`, no row.
- `list_commands` → `command_views(vehicle=, since=, limit=200)`, read-only: timeouts are decided by the tracker in the owner (Task 7), never as a side effect of a GET, so a state never depends on whether someone happened to look.

- [ ] **Step 1: Write the failing tests** (API in-process with `authed`, a `vid()` vehicle; requirement names kept)

```python
async def test_hazardous_requires_confirm_and_reason(app): ...    # first POST 409 HAZARDOUS_CONFIRMATION_REQUIRED with hazard text; nothing on tc:; confirm with reason "short" → 422 REASON_REQUIRED and the same token still confirms with a 10-char reason → 202, one tc entry with that reason
async def test_hazardous_one_shot_with_computed_hash_rejected(app):
    r = await fc1.post("/api/commands", json={**VENT, "confirm_token": args_hash(v, "vent_cabin", ARGS), "reason": "a valid reason"})
    assert r.status_code == 409 and r.json()["error"] == "CONFIRMATION_INVALID"
    assert [a["action"] for a in audit_rows(actor="fc1", since=t0)] == ["CMD_REJECTED"]
async def test_confirm_token_single_use_and_user_bound(app): ...  # reuse → 409; fc2 with fc1's token → 409; each audited CMD_REJECTED; expiry with CONFIRM_TTL_MS patched to 200
async def test_confirm_args_mismatch(app): ...                    # token for 5 s, confirm with 6 s → CONFIRMATION_INVALID
async def test_duplicate_within_2s(app): ...                      # fc1 then fc2 isolate_valve A → 409 DUPLICATE_COMMAND by fc1, seconds_ago < 2, cmd_id of the first
async def test_hazardous_lock_ignores_args(app): ...              # vent 5 s then vent 6 s by fc2 within 2 s → duplicate
async def test_ordinary_lock_uses_args(app): ...                  # isolate A then isolate B within 2 s → both 202
async def test_send_error_releases_lock(app, monkeypatch): ...    # xadd raises ConnectionError → 503 BROKER_DOWN, row FAILED SEND_ERROR (outcome NOT_SENT), lock gone, retry by fc1 → 202
async def test_invalid_args_422_without_side_effects(app): ...   # no row, no tc entry, no confirm/lock key
async def test_broker_down_503(users_file): ...                   # redis_url to a closed port → 503 BROKER_DOWN, no row
async def test_queued_then_sent_before_xadd(app): ...             # row SENT with sent_at ≥ queued_at; audit CMD_REQUESTED (reason) then CMD_STATE
async def test_monotonic_transitions(app): ...                    # row set to RECEIVED by the test; the API's SENT update leaves it RECEIVED
async def test_tc_entry_matches_schema(app): ...                  # the tc entry validates against tc.schema.json, issued_by = session user
async def test_list_commands_filters(app): ...
```

- [ ] **Step 2: Run to verify they fail** — 501 from the stubs.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the gate** (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: send commands with a single-use confirm token, the 2 s interlock and SEND_ERROR lock release"`

---

### Task 7: Command tracker in the owner and the tcack rejection rule

**Files:**
- Modify: `src/gs/commanding/tracker.py`, `tests/integration/test_commands_api.py`
- Create: `tests/integration/test_cmd_tracker.py`

**Interfaces:**
- Consumes: `decide`, `CmdRow`, `AckSeen`, `Change`, `TcAck`, `schema_reject`, `schema_sha12`, `mismatch_key`, `late_key`, `insert_events`, `audit`, `command_views`, `publish_ui`, `Ctx.counters`, `missiondb` defaults/overrides for `receive_s`/`verify_s` (from `ctx.mission()` after Gate 3a Task 17).
- Produces `track_pass(ctx, conn, vehicle, caught_up)` (D-CMD-05), constants `LATE_WINDOW_S = 600.0` (ruling R-i), `TCACK_COUNT = 100`:
  1. Read `tcack:<v>`: ensure group `ingest` (MKSTREAM); `XAUTOCLAIM tcack:<v> ingest <me> 0 0-0 COUNT 100` (entries left by a previous owner), then `XREADGROUP … COUNT 100 >` without BLOCK (ruling R-z).
  2. Validate each with `TcAck` **before** the transaction (D-ING-27): invalid → `AckSeen(False, "INVALID", entry_time, invalid=True)` for a readable UUID `cmd_id`, a `schema_reject` record, `ctx.counters["interface_mismatch"] += 1`, one `INTERFACE_MISMATCH` event `mismatch_key("tcack", schema_sha12(TcAck))`. A valid ack with `code == "SCHEMA"` also writes `mismatch_key("tc", schema_sha12(Tc))` (A-R17-4; ruling R-l).
  3. One transaction on the lock connection: open rows (`state IN ('QUEUED','SENT','RECEIVED')`, plus FAILED with NOT_RECEIVED/NOT_VERIFIED/ACK_INVALID and `failed_at > now − 600 s`, for this vehicle); one echo query `SELECT last_cmd_id, min(t_onboard) FROM telemetry WHERE vehicle = %s AND t_onboard >= <min(coalesce(sent_at, queued_at)) − 1 s> AND last_cmd_id = ANY(%s) GROUP BY 1`; `decide` per row with its first ack of the pass; each `Change` → `UPDATE commands SET … WHERE cmd_id = %s AND state = ANY(guard)` (late fields also `AND late_verified_at IS NULL` / `AND late_ack_at IS NULL`); audits (actor `system`, details `{vehicle, cmd_id, name, state, failure_reason}`); `LATE_VERIFICATION` events; mismatch events; COMMIT.
  4. `MULTI`: `XADD tm:dlq` for each rejected entry, `XACK` every entry read; `EXEC` (commit, then ack, D-ING-05 order).
  5. `publish_ui`: `Cmd` for every changed `cmd_id` (`command_views(cmd_ids=…)`), `Link` for the new events.
  - Any exception in 3: rollback, `log.exception` with `extra={"vehicle": v}`, `ctx.counters["cmd_tracker_errors"] += 1`, return without XACK (entries re-read next pass); never re-raised, never touches frames (D-ING-24 (3)). Redis errors in 1/4/5 are logged and retried next pass. Acks for unknown `cmd_id` are XACKed and logged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_cmd_tracker.py (in-process worker([v]) + rows/streams written by the test)
async def test_invalid_tcack_dlq_not_retried(): ...       # {"cmd_id": <open cmd>, "accepted": "yes"} → DLQ reason schema stream tcack:<v>, XACKed, one INTERFACE_MISMATCH/tcack/<sha12>, command FAILED ACK_INVALID (not NOT_RECEIVED); after 3 more passes still one DLQ record; ingest /healthz degraded
async def test_tracker_error_never_touches_frames(): ...  # admin trigger on commands raising on UPDATE (created/dropped by the test): counters["cmd_tracker_errors"] grows, the worker keeps running, frames keep committing, tcack still pending; trigger dropped → next pass applies and XACKs
async def test_no_false_failed_after_takeover(): ...      # SENT row 8 s old + its tcack (t +0.3 s) + echo frames already in the streams; worker starts → RECEIVED then VERIFIED, never FAILED (Review Focus 1)
async def test_late_ack_after_not_received(): ...         # FAILED NOT_RECEIVED row, a late tcack accepted → late_ack_at, late_ack_accepted True, audit CMD_LATE_ACK, outcome RECEIVED_LATE
async def test_sim_schema_nack_records_tc_mismatch(): ... # tcack code SCHEMA → REJECTED (outcome NOT_EXECUTED) + INTERFACE_MISMATCH/tc/<sha12>
async def test_cmd_messages_published(): ...              # each change publishes one cmd entry that validates against ui-event.schema.json

# tests/integration/test_commands_api.py (add: API + in-process sim + worker on one vid(); ATP-03 backend)
async def test_isolate_valve_verified(): ...              # 202 → VERIFIED within 1 s, queued/sent/received/verified_at all set and ordered
async def test_not_received_5s(): ...                     # sim pause 6 → FAILED NOT_RECEIVED with failed_at − sent_at in [5.0, 6.5] s, then late ack + LATE_VERIFICATION event, outcome EXECUTED_LATE, audits CMD_LATE_ACK and CMD_LATE_VERIFICATION
async def test_not_verified_15s(): ...                    # sim hold_exec 16 → RECEIVED, FAILED NOT_VERIFIED at 15–16.5 s, then LATE_VERIFICATION, outcome EXECUTED_LATE
async def test_queued_never_sent_fails_not_received(): ...# QUEUED row inserted by the test (no tc) → FAILED NOT_RECEIVED 5 s after queued_at
async def test_command_during_dropout(): ...              # dropout 10 then isolate_valve → FAILED NOT_RECEIVED → after AOS LATE_VERIFICATION, outcome EXECUTED_LATE
```

- [ ] **Step 2: Run to verify they fail** — the stub does nothing (rows stay SENT).
- [ ] **Step 3: Implement** `track_pass` as specified; frame-path log lines carry `extra={"vehicle", "seq"}` where a frame is concerned (ENG-05).
- [ ] **Step 4: Run the gate** (Task 1 Step 4) and `uv run pytest tests/system -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: track commands in the vehicle owner from stored evidence and reject schema-invalid tcack entries"`

---

## Track B – alarms and live stream

### Task 8: Transition value and worker publishing (alarm, link, resync, frozen)

**Files:**
- Modify: `src/gs/alarms/checks/{__init__,limit,roc}.py`, `src/gs/alarms/contract.py`, `src/gs/alarms/engine.py`, `src/gs/ingest/{store,worker}.py`, `tests/unit/{test_checks_roc,test_checks_limit,test_engine}.py`
- Create: `tests/integration/test_ui_publish.py`

**Interfaces:**
- Consumes: Gate 3a Task 16/17 `fold`, `Outcome.transitions`, `write_outcome`, `load_state`; `publish_ui`, `alarm_views`, `Link`, `Alarm`, `Resync`, `EventView`.
- Produces: `Reading(level, side, value: float | None = None)` — limit check: the sample value; ROC: the slope per second. `Transition.value: float | None = None` (last field) = the trigger slot's reading value on the transition frame, None for ACK/RTN/CLEAR and when that slot gave no reading on that frame (ruling R-b). `write_outcome` writes `alarm_events.value`.
- Worker, after COMMIT and before XACK (D-ING-22 order): `publish_ui(ctx.redis, [*tm, *links, *alarms])` — `tm` for each inserted frame with `frozen = nvs.frozen.reported`; `Link` for every event of the committed outcome (redelivery may republish; clients dedupe by key); `Alarm(alarm=…)` for every `alarm_id` touched (`alarm_views(conn, alarm_ids=…, open_only=False)`). `resync{vehicle}` is published once in `_own` right after `load_state` (start and every takeover, D-UI-18). Shape version of `Slot` is unchanged (no `ENGINE_STATE_RESET` on upgrade).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_checks_roc.py (add)
def test_reading_carries_slope():
    st, rd = run_roc(leak_series(seconds=2.0))      # −0.4 kPa/s, no noise
    assert rd.value == pytest.approx(-0.4, abs=1e-9) and rd.side == "FALLING"
# tests/unit/test_engine.py (add)
def test_transition_value_from_trigger_reading(): ... # three frames of 31.0 °C → RAISE value 31.0; RTN value None

# tests/integration/test_ui_publish.py (worker([v]), frames produced by the test)
async def test_alarm_message_after_red_steps():
    v = vid(); produce_values(v, cabin_temp_c=[22.0] * 5 + [31.0] * 5)
    async with worker([v]): await until(lambda: alarm_msgs(v), 5)
    (m,) = alarm_msgs(v)
    assert (m["alarm"]["severity"], m["alarm"]["last"]["alert"], m["alarm"]["last"]["value"]) == ("RED", True, 31.0)
    validate_ui_event(m)
async def test_link_message_for_seq_jump(): ...     # SEQ_JUMP event → one link message with that key
async def test_resync_first_on_start(): ...         # the first ui:events entry for v after start is resync{v}
async def test_tm_frozen_flag(): ...                # identical values for 2.5 s of t_onboard → a tm with frozen true; changed values → false
async def test_alarm_value_stored(): ...            # alarm_events.value = 31.0 on the RAISE row
```

- [ ] **Step 2: Run to verify they fail** — no `value` attribute / no alarm messages.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the gate** (Task 1 Step 4) and `uv run pytest tests/system -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: publish alarm, link and resync messages and the frozen flag, with the triggering value on each transition"`

---

### Task 9: ACK application in the owner (D-ALM-14)

**Files:**
- Create: `src/gs/ingest/acks.py`, `tests/unit/test_acks.py`, `tests/integration/test_alarm_ack_api.py`
- Modify: `src/gs/ingest/{worker,store}.py`

**Interfaces:**
- Consumes: `engine.ack(state, vehicle, param, user, note, key, t) -> (EngineState, Transition | None)`, `SeqState`, `audit`, `alarm_views`, `publish_ui`.
- Produces `gs.ingest.acks`:
  - `@dataclass(frozen=True) class AckReq: request_id: UUID; alarm_id: UUID; param: str; by_user: str; note: str`.
  - `async def pending_acks(conn, vehicle: str) -> list[AckReq]` (status `pending`, oldest `requested_at` first, joined with `alarms.param`); read every loop pass.
  - `def apply_ack(state: EngineState, req: AckReq, vehicle: str, epoch: datetime, t: datetime) -> tuple[EngineState, Transition | None, Literal["applied","superseded"], str | None]` — engine's open alarm for `req.param` missing or with another `alarm_id` → `superseded`, `"alarm no longer open"` (ruling R-w); already ACKED → `superseded`, `"already acked"`; else `engine.ack(…, key=str(req.request_id), t=t)` → `applied`.
  - `async def write_ack(conn, vehicle: str, req: AckReq, result: str, reason: str | None, tr: Transition | None, after: SeqState | None) -> None` — inside a SAVEPOINT (`conn.transaction()` nested): the ACK row with `after_epoch`, `after_seq`, `after_t_onboard` = `after` (the vehicle's last frame before this batch), the `alarms` upsert, request `status`, `applied_at = clock_timestamp()`, audit `ALARM_ACK_APPLIED {request_id, alarm_id, result, reason}`. On a database error the SAVEPOINT rolls back, the request becomes `rejected` with the error text, audited with `result: rejected` (R2-01), and the engine keeps its pre-ACK state.
- Worker: pending requests are applied at the start of the frame transaction, before the batch's frames (D-ING-22); a pass with **no entries but pending requests** commits an ACK-only transaction with the `engine_state` upsert (ruling R-x, Review Focus 2). Touched alarms are published as `Alarm` after COMMIT.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_acks.py
def test_apply_ack_branches(): ...   # applied (ACK transition, key == str(request_id)); already ACKED → superseded; other alarm_id → superseded "alarm no longer open"; blank-note never reaches here (API 422)

# tests/integration/test_alarm_ack_api.py (worker-level: requests inserted as the API would)
async def test_ack_after_seq_is_owner_seq(): ...      # RED alarm; request; next batch → ACK row after_epoch/after_seq/after_t_onboard = seq_state before that batch; ACK row id < the batch frames' transition ids
async def test_second_ack_superseded(): ...           # two pending requests → applied + superseded "already acked", one ACK transition row, two ALARM_ACK_APPLIED audits
async def test_ack_then_rtn_is_cleared(): ...         # ACK applied, then 3 nominal frames → CLEARED by system (not RTN_UNACK)
async def test_bad_ack_request_never_blocks_frames(): ...  # admin trigger raising on alarm_events INSERT when note = 'BOOM': request rejected with the error, audited, frames of the same batch committed, the next request applies
async def test_ack_applies_during_los(): ...          # RED alarm, producer stopped (LOS), request → applied within 1.5 s, after_seq = last stored seq (Review Focus 2)
async def test_ack_published_as_alarm_message(): ...  # ui:events alarm with state ACKED, ack_request.status applied
```

- [ ] **Step 2: Run to verify they fail** — requests stay `pending`.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the gate** (Task 1 Step 4) and `uv run pytest tests/system -q`.
- [ ] **Step 5: Commit** `git commit -m "feat: apply alarm ACK requests in the owner before each batch's frames, one savepoint each"`

---

### Task 10: Alarms API – list and ACK request

**Files:**
- Modify: `src/gs/api/alarms.py`, `tests/integration/test_alarm_ack_api.py`
- Create: `tests/integration/test_alarms_api.py`

**Interfaces:**
- Consumes: Task 3 stubs, `alarm_views`, `audit`.
- `post_ack` (D-ALM-14, §3.1): note blank after strip → 422 `NOTE_REQUIRED`; unknown `alarm_id` → 404 `ALARM_NOT_FOUND`; alarm CLEARED → 409 `ALARM_CLEARED`; `INSERT INTO alarm_ack_request (request_id, alarm_id, vehicle, by_user, note) … ON CONFLICT (request_id) DO NOTHING` + audit `ALARM_ACK` (only when inserted) in one transaction; 202 `{request_id, status}` with the stored status (idempotent on `request_id`). `list_alarms` → `alarm_views(vehicle, severity, state)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_alarms_api.py
async def test_ack_202_pending_then_applied(): ...   # API + worker: 202 pending; within 1.5 s GET /api/alarms shows ACKED and ack_request.status applied
async def test_ack_idempotent_on_request_id(): ...  # same request twice → one row, one ALARM_ACK audit, same 202 body
async def test_ack_errors(): ...                     # blank note 422 NOTE_REQUIRED; unknown 404; CLEARED 409 ALARM_CLEARED; each ErrorBody
async def test_list_filters(): ...                   # vehicle / severity / state filters
async def test_ack_requires_session_and_header(): ...# 401 without cookie, 403 without X-GS-Client
```

- [ ] **Step 2–4: Run to fail (501), implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: list alarms and accept ACK requests with a note, idempotent per request id"`

---

### Task 11: API hub – one ui:events reader, snapshot, broker and heartbeats

**Files:**
- Create: `src/gs/api/hub.py`, `tests/integration/test_hub.py`, `tests/integration/test_resync.py`
- Modify: `src/gs/api/{events,app}.py`, `tests/integration/test_sse.py`, `tests/integration/test_health.py`

**Interfaces:**
- Consumes: `UiEvent`, `schema_reject`, `schema_sha12`, `mismatch_key`, `insert_events`, `alarm_views`, `command_views`, `event_views`, `Ground`, `Hb`, `Snapshot`.
- Produces `gs.api.hub` (R9's one reader + bounded queues, D-UI-01/02/04/15/18, D-ING-27):
  - `CLIENT_QUEUE = 1000`, `RETRY_S = 1.0`, `HB_S = 1.0`, `SNAPSHOT_COMMANDS = 50`, `SNAPSHOT_EVENTS = 50` (ruling R-r).
  - `async def build_snapshot(conn, vehicles: Sequence[str], params: Sequence[str], now: datetime) -> Snapshot` (moved from `events.py`; adds `alarms` = open `alarm_views`, `commands` = `command_views(limit=50)`, `events` = `event_views(limit=50)`, and `tm.frozen` from `engine_state.state #>> '{frozen,reported}'`; ruling R-e).
  - `class Client`: `queue: asyncio.Queue[bytes]` (maxsize `CLIENT_QUEUE`), `closed: asyncio.Event`.
  - `class Hub(redis, pool, vehicles: Sequence[str], params: Callable[[], Sequence[str]], queue_size: int = CLIENT_QUEUE)`: `subscribe() -> Client`, `unsubscribe(c)`, `broker_up: bool`, `processing: dict[str, bool]`, `degraded: bool`, `async def run(self, stop: asyncio.Event) -> None` — one `XREAD BLOCK 1000` loop from the stream tail; each entry → `UiEvent.model_validate_json`; invalid → `XADD tm:dlq` (`schema_reject`, stream `ui:events`), one `INTERFACE_MISMATCH/ui-event/<sha12>` event, `degraded = True`, skip; `tm` → `age_ms` set; `resync` → one fresh snapshot broadcast; others forwarded under their kind. A full client queue → `closed.set()` (the stream ends, the client watchdog reconnects and gets a snapshot). Redis error → `broker_up = False`, broadcast `ground{broker:"down"}`, retry every 1 s; success → `ground{broker:"up"}` + snapshot broadcast. Every 1 s `MGET hb:veh:<v>…` → `processing`.
- `events._stream`: `retry: 1000`, `hello`, `subscribe()` **before** the snapshot (R2-08 order), `snapshot`, `ground{down}` if the broker is down, then queued messages and an `hb` (with `processing`) every second; `unsubscribe` in `finally`.
- `app.py`: lifespan starts `Hub.run` (stopped by `app.state.shutdown`); pool `max_size` 8; `/healthz` returns `degraded` while `hub.degraded` (D-ING-27).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_hub.py (live_api)
async def test_invalid_ui_event_dlq(live_api):      # requirements name (test_sse.py in requirements.md; recorded at Task 30)
    async with sse(live_api) as s1, sse(live_api) as s2:
        await s1.next_of("snapshot"); await s2.next_of("snapshot")
        xadd_data("ui:events", '{"kind":"tm","vehicle":"NYX-TA1"}')
        publish_tm(live_api.vehicle, 1)
        assert (await s1.next_of("tm"))[1]["seq"] == 1 and (await s2.next_of("tm"))[1]["seq"] == 1
    assert len(dlq_since("ui:events", t0)) == 1 and len(events_for(None, "INTERFACE_MISMATCH", since=t0)) == 1
    assert (await get_json(live_api, "/healthz"))["status"] == "degraded"
async def test_slow_client_overflow_closes_then_snapshot(live_api): ...  # queue_size 5, client not reading, 20 entries → that stream ends; the other client keeps receiving; a reconnect gets a snapshot holding the open alarm (Review Focus 5)
async def test_broker_down_ground_then_up_and_snapshot(live_api, monkeypatch): ...  # hub xread raises ConnectionError 3×: ground down, hb continues, then ground up + snapshot
async def test_hb_carries_processing(live_api): ...  # hb:veh:<v> set as ingest → processing[v] true; expired → false
# tests/integration/test_resync.py
async def test_snapshot_race_no_missed_alarm(live_api): ...  # alarm row committed and published between subscribe() and the snapshot query (hook) → the client sees it in the snapshot or as a delta; after key dedupe exactly once
async def test_resync_entry_sends_snapshot(live_api): ...
async def test_snapshot_has_alarms_commands_events_frozen(live_api): ...
# tests/integration/test_sse.py: existing tests keep passing on the hub (event committed during snapshot is a delta, heartbeat, shutdown hook)
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4) and `uv run pytest tests/system -q` (shutdown and reconnect still inside the S6 budget).
- [ ] **Step 5: Commit** `git commit -m "feat: one ui:events reader in the API with bounded client queues, broker state, heartbeats and stream rejection"`

---

### Task 12: Live mission db in the API, LIMITS_RELOADED and frame schema

**Files:**
- Modify: `src/gs/ingest/{mission,main}.py`, `src/gs/api/{deps,app}.py`, `tests/unit/test_mission_watch.py`
- Create: `tests/integration/test_mission_db_api.py`

**Interfaces:**
- `MissionWatcher(mission_dir, dsn, consumer, active, on_apply: Callable[[psycopg.AsyncConnection, MissionDb, MissionDb], Awaitable[None]] | None = None, on_events: Callable[[Sequence[GroundEvent]], Awaitable[None]] | None = None)` — `on_apply(conn, old, new)` after `insert_set`; `on_events(events)` after the `LIMITS_REJECTED` insert.
- API lifespan: `app.state.watcher = MissionWatcher(MISSION_DB, DATABASE_URL, "api", start_set, on_apply=_reloaded)`; `deps.mission(request)` returns `watcher.current`; `_reloaded` writes audit `LIMITS_RELOADED` (actor `system`, details `{set_sha256, limits_sha256, commands_sha256, parameters_sha256, hazardous_changed: [names]}`, D-EXT-02, EX-25).
- Ingest `main` passes `on_events` = `publish_ui` of `Link` messages, so a rejected set reaches the event strip (D-UI-19).
- `frame_schema` → `build_frame_model(mission.param_names).model_json_schema()` (D-EXT-19).

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_mission_db_api.py (API on a mission_copy)
async def test_definitions_follow_reload(tmp_path): ...       # vent_cabin max 10 → 8 in the copy: within 5 s POST duration 9 → 422 INVALID_ARGS; GET /api/mission-db shows the new set_sha256
async def test_hazardous_downgrade_needs_restart(tmp_path): ...# vent hazardous true → false: LIMITS_REJECTED event naming commands.yaml; hazardous still enforced (409 confirm)
async def test_hazardous_change_audited(tmp_path): ...        # isolate_valve false → true (+hazard text): LIMITS_RELOADED audit lists ["isolate_valve"]
async def test_frame_schema_served(): ...                     # properties hold every mission parameter; validates a simulator frame
# tests/unit/test_mission_watch.py (add): on_apply and on_events called once per applied / rejected set
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: follow mission-db reloads in the API, audit LIMITS_RELOADED, publish rejected sets, serve the frame schema"`

---

## Track C – console

Every component below is driven by the one store; components hold no copy of server state. Times shown with `toISOString()` (Z, D-UI-13); no `toLocale*` anywhere (UI-08). Text and glyph for every state (UI-03); grey when nominal (D-UI-07, G25 deviation for action buttons only).

### Task 13: Store for every SSE kind, row states and banners

**Files:**
- Create: `ui/src/states.ts`, `ui/src/banners.ts`, `ui/src/states.test.ts`, `ui/src/banners.test.ts`, `ui/src/freshness.test.ts`
- Modify: `ui/src/store.ts`, `ui/src/sse.ts`, `ui/src/status.ts`, `ui/src/LiveTable.tsx`, `ui/src/VehicleCard.tsx`, `ui/src/App.tsx`, `ui/src/store.test.ts`, `ui/src/sse.test.ts`, `ui/src/components.test.tsx`

**Interfaces:**
- Consumes: `components["schemas"]` `Tm`, `Snapshot`, `Alarm`, `Cmd`, `Link`, `Ground`, `Hb`, `AlarmView`, `CommandView`, `EventView`.
- `sse.ts`: listens to `hello snapshot tm alarm cmd link ground hb`; `onEvent(kind, data)` for each; on `error` also calls `onError` (unchanged watchdog, D-UI-03).
- `store.ts` adds: `applyAlarm(a: AlarmView): void` (upsert by `alarm_id`, CLEARED removed; keys seen kept in a set), `applyCmd(c: CommandView)`, `applyLink(e: EventView)` (dedupe by key, newest first, keep 200), `setGround(b: "up" | "down")`, `setProcessing(p: Record<string, boolean>)`, `noteMessage()`, `noteError()`; selectors `alarms()`, `commands()`, `events()`, `link(): {connected: boolean; broker: "up" | "down"; processing: Record<string, boolean>}`, `hasSnapshot(): boolean`, `openAlarm(v, param): AlarmView | undefined`. `applySnapshot` replaces alarms, commands and events. `connected` is false from the first `error` until the next message, and whenever 3 s pass without a message (`SILENCE_MS = 3000`).
- `states.ts`: `STALE_MS = 2000`; `paramState(value: number | null, ageMs: number | null, frozen: boolean, alarm: AlarmView | undefined): ParamState` — `acq` = NO_DATA (null value) · FROZEN · STALE (`ageMs ≥ 2000`) · OK; `level`/`side`/`check_kind` from an UNACK/ACKED alarm's `level` (`"RED_HIGH"` → RED, HIGH); RTN_UNACK and no alarm → NOMINAL. `status.ts` `Side` adds `RISING | FALLING`.
- `banners.ts`: `type Condition = Banner | "FROZEN" | "STALE" | null`; `conditionFor(c: {connected: boolean; broker: "up" | "down"; processing: boolean | undefined; ageMs: number | null; frozen: boolean}): Condition` — precedence NO LIVE CONNECTION > GROUND PROCESSING DOWN – BROKER > GROUND PROCESSING DOWN (`processing === false`) > LOSS OF SIGNAL (`ageMs ≥ 2000`, owner healthy, broker up) > FROZEN > STALE (D-UI-04/15).
- `LiveTable` passes each row's `paramState` and `statesWired = store.hasSnapshot()`, and the vehicle's banner; no state is passed before the first snapshot (G26 fill gate).

- [ ] **Step 1: Write the failing tests**

```ts
// banners.test.ts
it("orders the conditions", () => {
  const base = { connected: true, broker: "up" as const, processing: true, ageMs: 100, frozen: false };
  expect(conditionFor({ ...base, connected: false, broker: "down" })).toBe("NO LIVE CONNECTION");
  expect(conditionFor({ ...base, broker: "down", processing: false })).toBe("GROUND PROCESSING DOWN – BROKER");
  expect(conditionFor({ ...base, processing: false, ageMs: 5000 })).toBe("GROUND PROCESSING DOWN");
  expect(conditionFor({ ...base, ageMs: 2000, frozen: true })).toBe("LOSS OF SIGNAL");
  expect(conditionFor({ ...base, frozen: true })).toBe("FROZEN");
  expect(conditionFor(base)).toBeNull();
});
it("broker down shows BROKER on every vehicle, never LOS", () => { /* two vehicles, ageMs 10 000 */ });
// freshness.test.ts — requirements name: an old t_onboard with a fresh receipt is STALE
it("old t_onboard + fresh receipt → STALE", () => { expect(paramState(1, ageAt(2500, 0, 0), false, undefined).acq).toBe("STALE"); });
it("STALE at 2.0 s, OK at 1.9 s", () => { /* boundary */ });
// states.test.ts: RED_HIGH alarm → {level RED, side HIGH}; RTN_UNACK → NOMINAL; FALLING roc side; null value → NO_DATA
// store.test.ts: snapshot replaces alarms/commands/events; alarm upsert and CLEARED removal; link dedupe by key; 3 s without a message → connected false; error → false until the next message
// components.test.tsx: no summary and no row state before the first snapshot; after it, the summary counts RED rows
```

- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: wire every SSE kind into the console store with row states and banner precedence"`

---

### Task 14: Audio – RED beep, loss-of-monitoring tone, SOUND OFF

**Files:**
- Create: `ui/src/keySet.ts`, `ui/src/audio.ts`, `ui/src/beepSet.test.ts`, `ui/src/toneSet.test.ts`, `ui/src/audio.test.ts`
- Modify: `ui/src/App.tsx`, `ui/src/Login.tsx` (arm audio in the login click), `ui/src/shell.test.tsx`

**Interfaces:**
- `keySet.ts`: `createKeySet(name: string, storage: Pick<Storage, "getItem" | "setItem"> | null): { has(k: string): boolean; add(k: string): boolean /* true when new */; delete(k: string): void }` (mirrored to `sessionStorage` under `nyx.<name>`; storage errors → memory only).
- `audio.ts`: `armAudio(): AudioContext | null` (created and `resume()`d inside the login click, D-UI-08); `beep(ctx)` — 880 Hz square, 10 ms attack, 600 ms; `tone(ctx)` — 440 Hz sine, 200 ms linear attack, 800 ms (lower pitch, slower onset, D-UI-21); `beepsFor(alarms: AlarmView[], seen: KeySet): string[]` (keys of `last.alert` transitions not yet beeped; applied to snapshots and deltas alike); `toneKeys(prev: ToneState, next: ToneState, now: number): { play: boolean; keys: string[] }` — onset keys: LOS and FROZEN use their `link` event key; `<vehicle>|GPD`, `*|BROKER`, `*|NLC` while active and removed when the state ends (ruling R-n); onsets within the same second play one tone; single-parameter STALE never plays.
- `App` root element exposes `data-beeps` (count) and `data-last-tone` (`<kind>:<key>`) for tests; a visible **SOUND OFF – click to enable** badge while `ctx.state !== "running"`.

- [ ] **Step 1: Write the failing tests**

```ts
// beepSet.test.ts — requirements name
it("beeps once per alert key, also after a reload", () => {
  const store = memoryStorage(); const s1 = createKeySet("beeps", store);
  expect(beepsFor([red("k1")], s1)).toEqual(["k1"]); expect(beepsFor([red("k1")], s1)).toEqual([]);
  expect(beepsFor([red("k1")], createKeySet("beeps", store))).toEqual([]);   // reload
});
it("a resync snapshot with an unbeeped RED alert beeps once", () => { /* D-UI-18 */ });
it("yellow and RTN never beep", () => {});
// toneSet.test.ts — requirements name
it("one tone per onset; LOS uses its event key; dedupe across reload", () => {});
it("BROKER and NLC are console-wide and coalesce within one second", () => {});
it("a STALE parameter is silent", () => {});
it("an ended state plays again on its next onset", () => {});
// audio.test.ts: tone and beep use different frequencies and attacks (fake AudioContext records the calls)
```

- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: beep once per RED alert key and play one quieter tone at each loss-of-monitoring onset"`

---

### Task 15: Alarm list and ACK dialog

**Files:**
- Create: `ui/src/AlarmList.tsx`, `ui/src/AckDialog.tsx`, `ui/src/alarmText.ts`, `ui/src/alarmText.test.ts`, `ui/src/alarms.test.tsx`
- Modify: `ui/src/api.ts`, `ui/src/api.test.ts`

**Interfaces:**
- `alarmText(a: AlarmView, m: MissionDb | null): string` (D-UI-09): ROC → `"YELLOW · RATE FALLING −0.40 kPa/s (limit 0.15)"`; limit → `"RED HIGH · 31.20 degC (limit 30)"` (the matching red/yellow bound of the `limit` check); RTN_UNACK → `"RETURNED · was RED HIGH"`; true minus, 2 decimals, unit from the mission db. `hashText(a): string` → `"limits 3f9a1c2e · set 5d07b8a1"`.
- `ackAlarm(alarmId: string, requestId: string, note: string): Promise<{ ok: true; status: string } | { ok: false; error: string; message: string }>` (request id from `crypto.randomUUID()`).
- `AlarmList`: open alarms (sticky until CLEARED), filters vehicle / severity / state, each row: glyph + severity text, vehicle, parameter label, `alarmText`, `hashText`, opened time, state, ACK button. ACK opens `AckDialog` (native `<dialog>`, required note, inline errors; never `window.confirm`, which can neither show the alarm nor take a note and blocks the live page). Row status text: **ACK PENDING (by fc1)** while the request is pending (from the 202 or `ack_request`), **ACK NOT APPLIED – processing down** while that vehicle's `processing` is false, the `status_reason` of a superseded or rejected request.

- [ ] **Step 1: Write the failing tests**

```ts
// alarmText.test.ts — requirements name
it("rate alarm names the check, slope and limit", () =>
  expect(alarmText(roc({ level: "YELLOW_FALLING", value: -0.4 }), MDB)).toBe("YELLOW · RATE FALLING −0.40 kPa/s (limit 0.15)"));
it("limit alarm on a value outside its band", () => {});
it("hash prefixes are 8 hex digits", () => {});
// alarms.test.tsx: filters; ACK button disabled with a blank note; 202 → ACK PENDING (by fc1); 409 ALARM_CLEARED message shown inline; processing false → NOT APPLIED text; an RTN_UNACK row stays listed
```

- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: alarm list with filters, trigger text, hash prefixes and an ACK dialog with pending state"`

---

### Task 16: Command panel, hazardous confirm dialog and timeline

**Files:**
- Create: `ui/src/CommandPanel.tsx`, `ui/src/ConfirmDialog.tsx`, `ui/src/Timeline.tsx`, `ui/src/commandText.ts`, `ui/src/commandText.test.ts`, `ui/src/commands.test.tsx`
- Modify: `ui/src/api.ts`, `ui/src/api.test.ts`

**Interfaces:**
- `sendCommand(req: CommandRequest): Promise<SendResult>` with `SendResult = { kind: "accepted"; cmdId: string } | { kind: "confirm"; token: string; hazard: string; expiresInS: number } | { kind: "rejected"; error: string; message: string; by?: string; secondsAgo?: number }`; `listCommands(vehicle?: string): Promise<CommandView[]>` (called after every 202, so QUEUED/SENT show at once; ruling R-h).
- `confirmEnabled(typed: string, vehicle: string, reason: string): boolean` = `typed === vehicle && reason.trim().length >= 10` (exact match, no trimming or case folding; D-UI-20).
- `outcomeText(c: CommandView): string | null` (D-CMD-02, Q3): EXECUTED_LATE → `"FAILED (no verification in 15 s) – vehicle executed it at <late_verified_at> – do NOT resend"` (the parenthesis names the failure: `not received in 5 s`, `no verification in 15 s`, `vehicle answer failed its interface check`); NOT_EXECUTED → `"… – vehicle did not execute it – safe to resend after checking why"`; RECEIVED_LATE → `"… – vehicle accepted it late, not yet seen executing – wait"`; UNKNOWN → `"… – outcome unknown: check mode, last_cmd_id and the affected parameters; never blind-resend a hazardous command; tell the test conductor"`; NOT_SENT → `"not sent (broker unavailable) – safe to retry"`.
- `CommandPanel`: vehicle, command (from the mission db), typed arg inputs (`<select>` for enums, `<input type="number" min max step="any">` for numbers, unit shown), reason. A 409 HAZARDOUS_CONFIRMATION_REQUIRED opens `ConfirmDialog` (native `<dialog>`): vehicle, command, args, hazard text, reason (≥ 10), the typed-vehicle field labelled "Type NYX-TA2 to confirm"; Confirm disabled until `confirmEnabled`. DUPLICATE_COMMAND shows `"<by> sent the same command <n> s ago"`. `Timeline` per command: QUEUED, SENT, RECEIVED, VERIFIED times or "—" (D-UI-10), FAILED with its reason and `outcomeText`.

- [ ] **Step 1: Write the failing tests**

```ts
// commandText.test.ts
it.each([["EXECUTED_LATE", /do NOT resend/], ["UNKNOWN", /never blind-resend/], ["NOT_SENT", /safe to retry/],
         ["RECEIVED_LATE", /wait/], ["NOT_EXECUTED", /safe to resend after checking why/]])("%s", (o, re) =>
  expect(outcomeText(failed(o))).toMatch(re));
it("confirm needs the exact vehicle id and a 10-char reason", () => {
  for (const t of ["NYX-TA", "nyx-ta2", " NYX-TA2", "NYX-TA1"]) expect(confirmEnabled(t, "NYX-TA2", "reason long")).toBe(false);
  expect(confirmEnabled("NYX-TA2", "NYX-TA2", "too short")).toBe(false);
  expect(confirmEnabled("NYX-TA2", "NYX-TA2", "venting now")).toBe(true);
});
// commands.test.tsx: confirm flow posts token + reason; 409 CONFIRMATION_INVALID shown inline; duplicate message; timeline shows four times and "—"
```

- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: command panel with typed args, typed-vehicle hazardous confirm and a state timeline with outcome text"`

---

### Task 17: Event log strip

**Files:**
- Create: `ui/src/EventStrip.tsx`, `ui/src/eventText.ts`, `ui/src/eventText.test.ts`

**Interfaces:**
- `eventText(e: EventView, all: readonly EventView[]): string` (D-UI-19, newest first in the strip): AOS → `"NYX-TA1 AOS – 100 frames lost 14:03:12Z–14:03:22Z"` (start from the LOS with the same key suffix, `lost_frames: null` → `"unknown number of frames lost"`, G13); LOS → `"NYX-TA1 LOS since 14:03:12Z"`; GAP → `"NYX-TA1 GAP – 100 frames lost after seq 1234"`; SEQ_JUMP; SEQ_RESET → `"NYX-TA1 SEQ RESET – gap of unknown size"`; FROZEN; LIMITS_APPLIED → `"mission db applied: limits 3f9a1c2e · commands 5d07b8a1 · parameters 91c4e620"`; LIMITS_REJECTED → `"mission db REJECTED – <file>: <error>"`; LATE_VERIFICATION, INTERFACE_MISMATCH, POISON, ENGINE_STATE_RESET with their key fields. Times `HH:MM:SSZ` from `toISOString()`.

- [ ] **Step 1: Write the failing tests** — one `it` per kind above with the exact strings; an unknown kind falls back to `"<vehicle> <kind>"`.
- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: event log strip with lost-frame counts and mission-db changes in operator text"`

---

### Task 18: Parameter chart (uPlot, M4)

**Files:**
- Create: `ui/src/Chart.tsx`, `ui/src/chartSeries.ts`, `ui/src/chartSeries.test.ts`, `ui/src/chart.test.tsx`
- Modify: `ui/package.json`, `ui/package-lock.json` (`uplot` 1.6.32), `ui/src/api.ts`, `ui/src/api.test.ts`

**Interfaces:**
- `fetchBuckets(vehicle, param, fromIso, toIso, width): Promise<Bucket[]>` (`width` = the plot's pixel width, D-DB-04).
- `WINDOWS = { "2 min": 120_000, "1 h": 3_600_000, "24 h": 86_400_000 }`; refresh every `max(1000, windowMs / 120)` ms.
- `toSeries(buckets: Bucket[], bucketMs: number): { x: number[]; line: (number | null)[]; lo: (number | null)[]; hi: (number | null)[] }` — per bucket two x points (`t` with `first`, `t + bucketMs − 1` with `last`, M4, D-UI-11), band from `lo`/`hi`; a missing bucket (next `t` > previous `t + bucketMs`) inserts one `null` point (gap, never a line across).
- `limitLabels(limits: Limits, yPx: (v: number) => number): { value: number; tone: "red" | "yellow"; at: number }[]` — red labels outside the band, yellow inside, a label within 14 px of a kept one dropped, so limit numbers never print over each other on a short plot.
- `Chart`: window buttons, `ResizeObserver` → refetch with the new width, one request per range (min/max/first/last from one query, so band and line always describe the same buckets); the min/max band keeps a short dip visible at 24 h that a line alone would average away.

- [ ] **Step 1: Write the failing tests**

```ts
// chartSeries.test.ts — requirements name
it("line through each bucket's first and last, band from min/max", () => {
  const s = toSeries([b(0, { first: 1, last: 3, lo: 0.5, hi: 4 })], 100);
  expect(s.x).toEqual([0, 99]); expect(s.line).toEqual([1, 3]); expect(s.lo).toEqual([0.5, 0.5]);
});
it("gap gives null", () => { expect(toSeries([b(0), b(300)], 100).line).toContain(null); });   // Review Focus 4
it("limit labels skip collisions within 14 px", () => {});
// chart.test.tsx: requests carry width = the container width; a resize refetches with the new width
```

- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: uPlot chart with min/max band, first/last line, gaps and pixel-width buckets"`

---

### Task 19: Session panel and latency probe

**Files:**
- Create: `ui/src/SessionPanel.tsx`, `ui/src/probe.ts`, `ui/src/probe.test.ts`, `ui/src/sessions.test.tsx`
- Modify: `ui/src/api.ts`, `ui/src/api.test.ts`, `ui/src/store.ts` (probe receipt), `ui/src/VehicleCard.tsx` (probe paint)

**Interfaces:**
- API calls: `startSession(vehicle, name)`, `stopSession(id)`, `listSessions()`, `getReport(id): Promise<{ kind: "ready"; report: SessionReport } | { kind: "pending"; frames: number; expected: number } | { kind: "running" }>`; CSV link `/api/sessions/{id}/report.csv` (D-UI-17).
- `SessionPanel`: start (vehicle + name), the active session per vehicle with Stop, the list with report view (or "pending: n of m frames") and CSV download.
- `probe.ts` (D-UI-14): `createProbe(now = () => performance.now(), raf = requestAnimationFrame)`: `receipt(vehicle: string, seq: number, ageMs: number)`, `painted(vehicle: string, seq: number)` — on the second `requestAnimationFrame` after commit pushes `{vehicle, seq, age_ms, paint_ms}` (`paint_ms = paint − receipt`) to `window.__nyxLatency` (bounded to 100 000 samples).

- [ ] **Step 1: Write the failing tests** — `probe.test.ts`: fake clock and rAF: one sample per frame, `paint_ms` from receipt to the second rAF, a superseded frame still gets its sample, bound respected; `sessions.test.tsx`: start → listed active; stop → pending text; ready → report table; CSV link.
- [ ] **Step 2–4: Run to fail, implement, run** `cd ui && npx tsc --noEmit && npm test`.
- [ ] **Step 5: Commit** `git commit -m "feat: session panel with report and CSV, and a per-frame screen latency probe"`

---

## Track D – data, login, docs

### Task 20: Login hardening

**Files:**
- Modify: `src/gs/api/auth.py`, `tests/integration/test_auth.py`

**Interfaces:**
- `LOGIN_LIMIT = 5`, `LOGIN_WINDOW_S = 60`; `async def failed_logins(conn, user: str) -> int` = `count(*) FROM audit_log WHERE action = 'LOGIN_FAILED' AND actor = %s AND at > now() − 60 s AND details->>'rate_limited' IS NULL` (ruling R-a: the §3.7 ACL gives the API no counter key, and the audit row is written anyway).
- `login`: count ≥ 5 → audit `LOGIN_FAILED {rate_limited: true}` + 429 `LOGIN_RATE_LIMITED` (also for the right password); wrong password → audit `LOGIN_FAILED` (actor = submitted name, truncated to 64) + 401; success → audit `LOGIN` + session. `logout` → audit `LOGOUT`. A database error → 503 `UNAVAILABLE` ("database unreachable").

- [ ] **Step 1: Write the failing tests**

```python
async def test_login_rate_limited_after_5_failures(api_client):
    for _ in range(5): assert (await login(api_client, "fc1", "wrong")).status_code == 401
    r = await login(api_client, "fc1", FC1_PW)
    assert (r.status_code, r.json()["error"]) == (429, "LOGIN_RATE_LIMITED")
    assert (await login(api_client, "fc2", FC2_PW)).status_code == 204       # per user
async def test_window_passes(api_client, monkeypatch): ...      # LOGIN_WINDOW_S patched to 1 → allowed again after 1.1 s
async def test_login_logout_audited(api_client): ...            # LOGIN, LOGIN_FAILED, LOGOUT rows with actor and time
async def test_rate_limited_attempts_audited_not_counted(api_client): ...  # 10 attempts → 10 LOGIN_FAILED rows, only 5 counted
async def test_login_503_when_db_unreachable(users_file): ...
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: rate-limit failed logins per user and audit login, failure and logout"`

---

### Task 21: Test sessions – start, stop, list

**Files:**
- Modify: `src/gs/api/sessions.py`
- Create: `tests/integration/test_sessions.py`

**Interfaces:**
- `start_session`: vehicle not configured → 422 `INVALID_ARGS`; `INSERT INTO test_session (vehicle, name, conductor)` with conductor = session user; unique-index violation → 409 `SESSION_ACTIVE`; audit `SESSION_START` in the same transaction; 201 `SessionView`.
- `stop_session`: `UPDATE … SET ended_at = clock_timestamp(), ended_by = %s WHERE id = %s AND ended_at IS NULL`; no row → 404 or 409 `SESSION_STOPPED`; audit `SESSION_STOP`.
- `list_sessions`: newest `started_at` first.

- [ ] **Step 1: Write the failing tests** (requirements names)

```python
async def test_start_stop(app): ...                # conductor = fc1, started_at < ended_at, ended_by = fc2 when fc2 stops; audits SESSION_START/STOP
async def test_one_active_per_vehicle(app): ...    # second start on the vehicle → 409 SESSION_ACTIVE; another vehicle → 201
async def test_stop_twice_409(app): ...
async def test_unknown_vehicle_422(app): ...
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: start, stop and list named test sessions per vehicle with their conductor"`

---

### Task 22: Session report – watermark freeze, JSON and CSV

**Files:**
- Create: `src/gs/api/report.py`, `tests/unit/test_report_csv.py`
- Modify: `src/gs/api/sessions.py`, `tests/integration/test_sessions.py`

**Interfaces:**
- `FREEZE_AFTER_S = 30`.
- `async def build_report(conn, s: SessionView, mdb: MissionDb, complete: bool, now: datetime) -> SessionReport` — one aggregate query over `[started_at, ended_at)`: `count(*)`, per parameter `min/max/avg/count`; `expected = round(duration_s / period_s)` with `period_s` from `seq_state` (0.1 when absent); `link_events` = `event_views(kinds=GAP SEQ_JUMP SEQ_RESET LOS AOS, since, until)` (a SEQ_RESET is a gap of unknown size, D-ING-16); `alarms` = transitions by `processed_at` in the window; `commands` = `command_views` queued in the window; `note = "incomplete: n of m frames"` when not complete (D-DB-06).
- `async def frozen_report(conn, session_id: int, mdb: MissionDb, now: datetime) -> SessionReport | ReportConflict` (D-DB-10): stored row → it; running → `SESSION_RUNNING`; `seq_state.last_t ≥ ended_at` → complete; else `now ≥ ended_at + 30 s` → incomplete; else `REPORT_PENDING {frames, expected}`. Freezing = `INSERT INTO session_report … ON CONFLICT (session_id) DO NOTHING`, then the stored row is returned, so concurrent readers get one frozen copy (ruling R-p).
- `def report_csv(r: SessionReport) -> str` — first column `section` ∈ `summary · parameter · link_event · alarm · command`, every time via `iso_z`, NULL as an empty field.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_report_csv.py
def test_sections_and_z_times(): ...      # every time cell ends in Z; one parameter row per parameter; empty field for None
# tests/integration/test_sessions.py (add; rows inserted by the test on a vid())
async def test_report_contents(app): ...                 # exact min/max/avg per param, frames vs expected, GAP lost 100, SEQ_RESET listed, alarm transitions, commands with outcome
async def test_report_pending_until_watermark(app): ...  # seq_state.last_t < ended_at → 409 REPORT_PENDING {frames, expected}; last_t ≥ ended_at → 200 complete
async def test_report_frozen(app): ...                   # rows inserted after the freeze leave the report unchanged
async def test_report_incomplete_after_30s(app, monkeypatch): ...  # FREEZE_AFTER_S 1 → note "incomplete: 50 of 100 frames"
async def test_report_running_409(app): ...
async def test_report_csv_route(app): ...                # text/csv, same numbers as the JSON
async def test_silent_session_report(app, monkeypatch): ...  # no frames at all → incomplete "0 of m", stats None, CSV empty fields, 200 (Review Focus 3)
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: freeze the session report at the vehicle watermark or after 30 s, with JSON and CSV"`

---

### Task 23: M4 telemetry buckets

**Files:**
- Modify: `src/gs/api/telemetry.py`
- Create: `tests/integration/test_downsample.py`

**Interfaces:**
- `MIN_BUCKET = timedelta(milliseconds=100)`, `MAX_WINDOW = timedelta(hours=24)`, `WIDTH_MIN, WIDTH_MAX, WIDTH_DEFAULT = 100, 4000, 1500`.
- `get_buckets`: vehicle not configured or param not in the live mission db → 400 `UNKNOWN_PARAM`; `width` outside 100–4000 → 400 `WIDTH_OUT_OF_RANGE` (checked in the handler, so 400, not 422; ruling R-t); `to ≤ from` or window > 24 h → 400 `WINDOW_TOO_LARGE`; one query `SELECT time_bucket(%s, t_onboard) t, min(p), max(p), avg(p), first(p, t_onboard), last(p, t_onboard), count(p) FROM telemetry WHERE vehicle = %s AND t_onboard >= %s AND t_onboard < %s AND p IS NOT NULL GROUP BY 1 ORDER BY 1` with bucket = `max((to − from) / width, 100 ms)` and `p` as `sql.Identifier` (D-DB-04).

- [ ] **Step 1: Write the failing tests** (requirements names)

```python
async def test_first_last_per_bucket(app): ...    # ramp + one spike: each bucket's first/last equal its first/last stored rows; spike in hi
async def test_bucket_count_from_width(app): ...  # 1 h, width 1500 → ≤ 1500 buckets of 2.4 s; 2 min, width 4000 → 100 ms floor
async def test_24h_under_2s(app): ...             # 864 000 rows for a vid() (generate_series insert); band correct; elapsed printed (judged on the reference machine in ATP-08, A-R2R-8)
async def test_empty_bucket_absent(app): ...      # a 10 s hole → no bucket inside it (Review Focus 4)
async def test_errors_400(app): ...               # width 99 / 4001, window 24 h + 1 s, unknown param; ErrorBody
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "feat: serve M4 chart buckets sized to the chart's pixel width"`

---

### Task 24: Schemathesis spike (D-EXT-18, R17 F2)

**Files:**
- Create: `docs/research/S2-schemathesis-spike.md`, `tests/integration/test_rest_fuzz.py` (marked `pytest.mark.spike`, not collected by the gate until Task 29)
- Modify: `pyproject.toml` (dev group: `schemathesis==<newest release ≥ 2 weeks old>`), `uv.lock` — ruling for this task only

**Interfaces:** `schemathesis.openapi.from_asgi("/openapi.json", app)` with an auth hook that logs in through `/api/login` and sends the `session` cookie and `X-GS-Client: console` on every case; checks `not_a_server_error`, `response_schema_conformance`, `status_code_conformance`, `content_type_conformance`; `POST /api/commands` excluded unless the target vehicle is a `vid()` on the test stack.

- [ ] **Step 1:** Write the spike test against the current branch (handlers may still be stubs: 501s are expected and recorded, the spike judges the tool, not the API).
- [ ] **Step 2:** Run `uv run pytest -m spike tests/integration/test_rest_fuzz.py -q`; record in the S2 note: the version, whether OpenAPI 3.1 / JSON Schema 2020-12 and pydantic's hoisted `$defs` load without errors, whether the auth hook works, the number of generated cases per operation, the raw output.
- [ ] **Step 3:** Decision line in the note: **PASS** → Task 29 turns it into the CI check `test_schemathesis_asgi` (replacing EX-24's informational test); **FAIL** → EX-24's informational `test_rest_responses_match_openapi` stays, F2 waits (A-R17-3).
- [ ] **Step 4:** Gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "test: spike Schemathesis against the ASGI app and record the result"`

---

### Task 25: Perf run N tooling

**Files:**
- Create: `perf/stats.py`, `perf/run_n.py`, `perf/ui_latency.spec.ts`, `perf/playwright.config.ts`, `tests/unit/test_perf_stats.py`, `tests/unit/test_run_n.py`

**Interfaces:**
- `perf.stats`: `nearest_rank(xs: Sequence[float], p: float) -> float` (rank ⌈p·n⌉, stdlib, D-PERF-03); `p95(xs) -> float`; `after_warmup(rows, t0: datetime, warmup_s: float = 10.0) -> list` (first 10 s excluded and counted); `write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None` (datetimes via `iso_z`).
- `perf.run_n`: `DURATION_S = 600`, `STEP_EVERY_S = 5.0`, `STEP_S = 1.0`, `STEP_VALUE = 31.0` (`cabin_temp_c` red_high 30); `def step_schedule(t0: float) -> list[float]` (120 offsets); `main()` — records machine spec, image digests, load average before/after (D-PERF-08), posts the `step` faults on schedule to NYX-TA1, runs `perf/ui_latency.spec.ts` alongside for the whole run, then writes `docs/perf/run-n-<UTC date>/` with `ingest.csv` (`t_received − t_onboard`, both vehicles pooled), `alarm.csv` (`processed_at − trigger_t_onboard` of every transition to RED of the stepped parameter; first 20 and all 120 reported), `ui.csv` (`age_ms + paint_ms` per frame), `query.csv` (60 timed 1 h range queries of one parameter) and `summary.md` (n, p95, pass/fail at 200 ms, 300 ms, 1 s, 300 ms; D-PERF-04/05).
- `perf/ui_latency.spec.ts`: logs in, waits for `DURATION_S`, reads `window.__nyxLatency`, writes `ui.csv` rows.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_perf_stats.py — D-ENG-20 name
def test_nearest_rank_p95(): assert p95(list(range(1, 101))) == 95 and nearest_rank([5.0], 0.95) == 5.0
def test_warmup_excluded(): ...
def test_csv_times_end_in_z(tmp_path): ...
# tests/unit/test_run_n.py
def test_schedule_gives_120_steps_inside_the_run(): s = step_schedule(0.0); assert len(s) == 120 and s[-1] + STEP_S <= DURATION_S
```

- [ ] **Step 2–4: Run to fail, implement, run** the gate (Task 1 Step 4) and `cd e2e && npx tsc --noEmit -p ../perf` (spec type-checks).
- [ ] **Step 5: Commit** `git commit -m "perf: add the run N driver, nearest-rank stats and the screen latency spec"`

---

### Task 26: Operator guide and developer guide

**Files:**
- Create: `docs/operator-guide.md`, `docs/dev-guide.md`, `tests/meta/test_guides.py`

**Interfaces:**
- Operator guide (one page, spec §6): **Read the console** (rows, STALE/FROZEN/NO DATA, grey = nominal, hash prefixes), **Banners and tones** (precedence NO LIVE CONNECTION > GROUND PROCESSING DOWN > LOS > FROZEN > STALE, BROKER, what the tone means, R2-13), **Acknowledge an alarm** (note, ACK PENDING, ACK NOT APPLIED), **Send a command** (typed vehicle ID, reason, duplicate message), **When a command shows FAILED** (each outcome and what to do; UNKNOWN procedure from Q3), **Run a test session** (start, stop, pending report, CSV). The replay section is added at Gate 4.
- Developer guide: **Add a parameter** (D-DB-11, `make migrate`, Q18), **Add a limit check type** (one file + `@register`, ALM-07), **Add a command** (`mission/commands.yaml`, arg bounds shared by API and simulator, restart the simulator after a bound change: ruling R-o), **Change a stream interface** (D-ING-27 upgrade rule: restart all services together, `make up`; the CI notice), **Run the tests** (unit, integration, system, e2e, `make perf`).
- `tests/meta/test_guides.py`: each guide has its headings above; every time example ends in `Z`.

- [ ] **Step 1: Write the failing test** (headings) — **Step 2:** run → FAIL (files missing) — **Step 3:** write the guides — **Step 4:** gate — **Step 5: Commit** `git commit -m "docs: add the operator guide and the developer guide with the stream upgrade rule"`

---

## Track Z – integration (after D, A, C, B are merged)

### Task 27: Cross-track contracts, audit completeness and system tests

**Files:**
- Create: `tests/integration/test_audit_complete.py`, `tests/integration/test_audit_append_only.py`, `tests/system/test_resync.py`, `tests/system/test_json_logs.py`, `tests/system/test_sse_contract.py`, `tests/system/test_broker_banner.py`
- Modify: `tests/integration/test_contracts.py`, `tests/integration/test_redis_acl.py`

**Interfaces:** Consumes everything; `post_fault`, `compose`, `recreate`, `FAST_ENV`, `sse`.

- [ ] **Step 1: Write the tests**

```python
# tests/integration/test_contracts.py (add; requirements/D-EXT-18 names)
async def test_tc_tcack_match_schema(): ...      # API-produced tc and sim-produced tcack (OK, INVALID_ARGS, SCHEMA) validate against the exported files with jsonschema
async def test_ui_events_match_schema(): ...     # one worker + sim run producing tm, alarm (step), link (seq_jump), cmd (isolate), resync: every ui:events entry validates against ui-event.schema.json; each kind seen
# tests/integration/test_audit_complete.py
async def test_audit_complete(): ...             # 1 failed login, 1 login, 2 ordinary commands, 1 hazardous (required + confirmed), 1 duplicate, 2 acks (1 superseded), 1 session start/stop, 1 logout → exactly the expected (action, actor) multiset, every row with time; CMD_REQUESTED carries the reason
# tests/integration/test_audit_append_only.py — requirements name
def test_update_delete_truncate_fail(): ...      # as gs_app and as gs_owner (trigger), each raises
# tests/integration/test_redis_acl.py (add)
def test_api_command_set_permitted(): ...        # as api: SET NX PX on cmdlock/confirm, GETDEL, WATCH/GET/MULTI/DEL/EXEC, XADD tc:NYX-*, XADD tm:dlq, XREAD ui:events, MGET hb:veh:* → no NOPERM; ACL LOG empty
# tests/system/test_resync.py — requirements name
def test_resync_after_crash(fast_ingest): ...    # SSE client open; CRASH_AT=after_commit_before_publish while a RED step commits → after restart/takeover the client's resync snapshot holds the alarm with alert true
# tests/system/test_json_logs.py (ENG-05)
def test_every_service_logs_json(): ...          # last 50 lines of simulator, ingest-1, ingest-2, api, migrate parse as JSON with t ending in Z
def test_frame_path_lines_carry_vehicle_and_seq(): ...  # seq_jump fault → an ingest line with vehicle NYX-TA1 and an int seq
# tests/system/test_sse_contract.py (D-EXT-18)
def test_every_sse_kind_matches_schema(): ...    # provokes alarm (step), cmd (isolate), link (dropout 3), ground (redis stop 3 s); every message of the 8 kinds validates
# tests/system/test_broker_banner.py (UI-07 backend, D-UI-04)
def test_redis_stop_ground_down_no_los_event(): ...  # stop redis 10 s: SSE ground down then up + snapshot; no LOS event row for either vehicle in the window
```

- [ ] **Step 2: Run** `make up && uv run pytest tests/integration tests/system -q && uv run python scripts/check_acl_log.py` — expected PASS; a failure is a defect in its owning code (failing test first, bug-log row).
- [ ] **Step 3–4:** Fix found defects, run the full gate (Task 1 Step 4).
- [ ] **Step 5: Commit** `git commit -m "test: add cross-track contract, audit completeness, resync, JSON log and broker system tests"`

---

### Task 28: End-to-end ATPs in the browser

**Files:**
- Create: `e2e/helpers.ts`, `e2e/atp01-session.spec.ts`, `e2e/atp02-leak.spec.ts`, `e2e/atp04-los.spec.ts`, `e2e/reconnect.spec.ts`, `e2e/hazardous.spec.ts`, `e2e/hazardous-confirm.spec.ts`, `e2e/monitoring-tone.spec.ts`, `e2e/chart.spec.ts`, `docs/img/console-leak.png`
- Modify: `e2e/smoke.spec.ts` (helpers), `docs/operator-guide.md` (screenshot link)

**Interfaces:** `helpers.ts`: `login(page, user)`, `fault(vehicle, fault, params)` (POST to the simulator port), `beeps(page)` / `lastTone(page)` (read `data-beeps` / `data-last-tone`), `editLimits(old, new)` / `restoreLimits()` (host file, restored in `afterEach`), `compose(...args)`.

- [ ] **Step 1: Write the specs** (requirements names; each asserts text and glyph, never colour alone)

```ts
// atp02-leak.spec.ts (ATP-02, §3.4 timeline, 60 s budget)
test("leak → yellow → red → ack → isolate → verified → clear", async ({ page }) => {
  // leak on NYX-TA1; ≈1.3 s "YELLOW · RATE FALLING −0.40 kPa/s (limit 0.15)", no beep
  // editLimits (cabin_temp yellow_high 27.0 → 26.5): LIMITS_APPLIED in the strip; the RED escalation's hash prefix is the new one
  // RED: beeps === 1; ACK with note → "ACK PENDING (by fc1)" → ACKED ≤ 1 s
  // send isolate_valve A → timeline shows QUEUED, SENT, RECEIVED, VERIFIED times
  // DEESCALATE YELLOW, then CLEARED (row leaves the list); beeps still 1
});
// atp04-los.spec.ts: dropout 10 → every row STALE at 2 s ± 0.5 s, LOSS OF SIGNAL banner, one tone; AOS strip line "100 frames lost" (±1); recovers on its own
// reconnect.spec.ts: restart api → NO LIVE CONNECTION from the first error, data flowing < 5 s, a snapshot first; stop redis 10 s → "GROUND PROCESSING DOWN – BROKER" on both vehicles, no LOSS OF SIGNAL
// hazardous.spec.ts (ATP-06): vent_cabin with typed id + reason → VERIFIED; without confirm the command never appears; fc1 and fc2 send isolate_valve A within 2 s → fc2 sees "fc1 sent the same command …"; audit rows present via the API test helper
// hazardous-confirm.spec.ts: wrong / partial / lowercase vehicle id → Confirm disabled; right id → enabled, two-step completes
// monitoring-tone.spec.ts: stop ingest-1 and ingest-2 → exactly one tone per vehicle within the D-UI-15 detection time, none after (ruling R-m: one stopped worker is covered by failover); stop api → one NO LIVE CONNECTION tone; one parameter missing from frames (NO DATA) → no tone; a RED step during GROUND PROCESSING DOWN of the other vehicle still beeps once
// chart.spec.ts: chart request carries width = the plot's pixel width; 2 min window draws; a dropout leaves a gap
// atp01-session.spec.ts (ATP-01 short): start sessions on TA1 and TA2, run 2 min, stop, report pending then ready, CSV downloaded with Z times
```

- [ ] **Step 2: Run** `make up && cd e2e && npx playwright test` — expected PASS; take `docs/img/console-leak.png` with `page.screenshot` during the RED step of `atp02-leak.spec.ts` (the operator guide shows the console in the state an operator must recognise) and link it from the operator guide.
- [ ] **Step 3–4:** Fix found defects (owning code, failing test first), run the full gate and `uv run python scripts/check_acl_log.py`.
- [ ] **Step 5: Commit** `git commit -m "test: add the ATP-01 short, 02, 04, 06 browser specs, reconnect, tone and chart specs"`

---

### Task 29: Schemathesis CI check and perf run N

**Files:**
- Modify: `tests/integration/test_rest_fuzz.py` (spike marker removed, named `test_schemathesis_asgi`), `tests/integration/test_openapi.py` (EX-24's informational test removed only if the spike passed)
- Create: `docs/perf/run-n-<UTC date>/**`

- [ ] **Step 1:** If Task 24 recorded PASS: make `test_schemathesis_asgi` part of the `test` job; run it; every finding is a defect in its route (fix with a failing test first). If FAIL: leave the informational test, record why in the S2 note.
- [ ] **Step 2:** On the reference machine: `make up && make perf`; commit the run folder (raw CSVs, `summary.md`, machine spec, digests, load average). A p95 over its threshold is reported to the coordinator as a finding for an NCR decision at Gate 5, never tuned.
- [ ] **Step 3:** Full gate.
- [ ] **Step 4: Commit** `git commit -m "test: fuzz the REST API from openapi.json in CI and record perf run N"`

---

### Task 30: Record the rulings and update the trace

**Files:**
- Modify: `docs/design.md` (one sentence per affected row, citing this plan: D-CMD-02/05/06, D-ALM-14, D-UI-02/04/08/09/18/19/20/21, D-ENG-07/08, D-DB-10, D-ING-27, D-EXT-18/19, §3, §3.1, §3.2; the G25 brand-theme decision row if Gate 3a's docs track did not add it), `docs/assumptions.md` (rulings as `lead` assumptions), `docs/requirements.md` (Planned evidence: real test names for CMD-01..05, UI-01..08, DAT-01/02, ENG-05, ALM-06; `test_invalid_ui_event_dlq` lives in `test_hub.py`), `docs/bug-log.md` (rows for real bugs caught in this gate)

- [ ] **Step 1:** Write the rulings (list R-a … R-z below) into the files named.
- [ ] **Step 2:** `uv run pytest tests/meta -q` (guides and README checks stay green).
- [ ] **Step 3: Commit** `git commit -m "docs: record gate 3b rulings and update the requirements trace"`

---

## Rulings made by this plan (for the coordinator)

- **R-a** Login rate limit counts recent `LOGIN_FAILED` audit rows (rate-limited attempts flagged and not counted): the §3.7 ACL has no counter key for the API.
- **R-b** `alarm_events.value` + `Reading.value` + `Transition.value`: D-UI-09's "RATE FALLING −0.40 kPa/s" needs the triggering reading; no state-shape change.
- **R-c** `commands.failure_reason` CHECK gains `ACK_INVALID` in `02-tables.sql` (file lagged §3.2); `make reset`.
- **R-d** The generated ACL gains `api (~tm:dlq +xadd)` (§3.7 already has it); Redis must reload the ACL.
- **R-e** FROZEN reaches the console as `tm.frozen` (worker's `FrozenState.reported`) and the snapshot reads `engine_state`; no FROZEN end event.
- **R-f** `hb.processing` carries per-vehicle `hb:veh` presence for GROUND PROCESSING DOWN.
- **R-g** One `ui:events` reader per API process with bounded client queues (D-UI-02's "per-client queue overflow", R9); replaces Gate 2's per-connection XREAD.
- **R-h** The API cannot write `ui:events` (§3.7): the requesting console shows ACK PENDING from the 202 and refetches commands after a 202; other consoles see ACK PENDING via the snapshot.
- **R-i** Late evidence is looked for 600 s after FAILED.
- **R-j** `decide()` judges windows by evidence timestamps; an echo inside the receive window with no `tcack` gives VERIFIED with `received_at` NULL.
- **R-k** 422 `REASON_REQUIRED` for a short confirm reason; the token is not consumed.
- **R-l** `INTERFACE_MISMATCH/tc/<sha12>` uses the ingest image's `Tc` schema hash (the simulator has no DB).
- **R-m** `e2e/monitoring-tone.spec.ts` stops both ingest workers: with failover, one stopped worker never makes a vehicle lose its owner.
- **R-n** Tone keys for client-detected states are `(vehicle, state)` while active (persisted), so a reload does not replay; LOS and FROZEN use event keys.
- **R-o** The simulator reads `commands.yaml` at start; a bound change reaches it at its next restart (dev guide).
- **R-p** The report freezes on the first read that meets the rule (idempotent insert), not in a background task.
- **R-q** Track 0 defines every REST body and route signature (stubs), so `openapi.json` and `schema.d.ts` are frozen while tracks run in parallel.
- **R-r** Snapshot carries the 50 newest commands and events.
- **R-s** One `CommandConflict` body for every 409 of `POST /api/commands`.
- **R-t** `width` is checked in the handler (400 per D-DB-04, not FastAPI's 422).
- **R-w** An ACK for an alarm that is no longer open is `superseded` ("alarm no longer open").
- **R-x** A pass with no entries but pending ACK requests commits an ACK-only transaction.
- **R-z** The tracker claims `tcack` entries with `XAUTOCLAIM … 0` then reads `>` each pass.

## Self-review notes

- Day-3 row coverage: D-CMD-01 (T5, T7), 02 (T1 `outcome`, T5, T16), 03 (T5, T7), 04 (T5, T7), 05 (T3 call site, T7), 06 (T6, T16, T28), 07 (T6), 08 (T3 `audit`, T6, T7, T9, T10, T20, T21, T27), 09 (T6, T12), 10 (T6 identity from session), 11 (T5 guards, T6); D-SIM-06/10 and `pause`/`hold_exec` (T4); interface models + contract tests (T1, T4, T6, T7, T8, T27); D-ALM-14 (T9, T10, T15); D-UI-01..15 (T11, T13, T14, T15, T16, T18, T19), 17 (T19), 18 (T8, T11, T27), 19 (T17), 20 (T16, T28), 21 (T14, T28); D-ENG-07 (T2, T27), 08 (T20); D-DB-04 (T23), 06/07/10 (T21, T22); D-ING-27 (T4 sim, T7 tracker, T11 API, T1 CI notice); D-EXT-18 spike + CI (T24, T29), D-EXT-19 frame schema (T12); D-PERF-04/05 run N (T25, T29); guides (T26).
- Depends on Gate 3a's final interfaces (Tasks 16–17): `VehicleState.engine`, `Outcome.transitions`, `fold(…, cset)`, `write_outcome`'s alarm upsert and `alarm_events` insert (Task 8 adds `value`, Task 9 the `after_*` columns), `load_state` with the G18 rebuild, `Ctx.mission` (Task 7's per-command timeouts, Task 12), `MissionWatcher` in ingest `main`, `engine.ack(state, vehicle, param, user, note, key, t)`, helpers `produce_values` / `stream_values` / `worker(mission_dir=)`. If any of these lands differently, Track 0's Task 3 adjusts the call site and Tasks 8/9 their Interfaces before the tracks branch.
