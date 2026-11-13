# Nyx Ground Segment – What We Build

Design and decision register · draft for Gate 2 · 2026-11-13

This file is the single answer to "what do we build, and why that way". Gate 1 gap review (same day) added D-SIM-10/11, D-ING-24/25, D-DB-10/11, D-ARC-10, D-UI-15..17, D-ENG-12/13, §3.1–3.4 and §5.1–5.2. Every decision has an ID, the choice, the reason, the alternative it beat, the requirements it serves (spec §4, `GS-` prefix dropped) and the research note it comes from. Open choices and their defaults live in [assumptions.md](assumptions.md); the evidence lives in [research/](research/).

**Build levels.** **v1** = build first; required to pass a must-requirement. **v1+** = only if time remains after the v1 slice for every gate. If a v1+ item is not built, it gets an NCR (non-conformance report) in `docs/ncr/`, not a silent gap.

**Status tags.** `lead` = decided at the Gate 1 review, not by a research note. `default` = operator-visible choice the owner may still flip (see assumptions.md).

---

## 1. System in one picture

```mermaid
flowchart LR
  subgraph Bench["Test article (simulated)"]
    SIM["gs-sim<br/>NYX-TA1, NYX-TA2<br/>10 Hz + fault injection"]
  end
  subgraph Redis["Redis 8.10 (AOF always)"]
    TM["tm:&lt;vehicle&gt;"]
    TC["tc:&lt;vehicle&gt;"]
    ACK["tcack:&lt;vehicle&gt;"]
    DLQ["tm:dlq"]
    UIE["ui:events"]
    LOCK["cmdlock:* (2 s)"]
  end
  subgraph Workers["gs-ingest ×2 (one owner per vehicle)"]
    ING["validate → seq tracker → store<br/>alarm engine → command tracker"]
  end
  DB[("TimescaleDB<br/>telemetry · events · alarms<br/>commands · audit · sessions")]
  ARC["gs-archiver<br/>hourly Parquet"]
  S3[("MinIO<br/>archive/&lt;v&gt;/YYYY/MM/DD/HH.parquet<br/>+ .sha256")]
  API["gs-api (FastAPI)<br/>REST + SSE + replay + UI files"]
  UI["Console<br/>React + TS"]

  SIM -- XADD frames --> TM
  TC -- commands --> SIM
  SIM -- accept/reject --> ACK
  TM -- XREADGROUP ingest --> ING
  ACK --> ING
  ING -- bad / poison --> DLQ
  ING -- COMMIT then XACK --> DB
  ING -- after commit --> UIE
  UIE -- XREAD --> API
  API -- SSE --> UI
  UI -- REST: command, ack, session --> API
  API -- interlock --> LOCK
  API -- XADD --> TC
  DB --> ARC --> S3
  S3 -- replay window --> API
  DB -- charts, reports --> API
```

**One-paragraph story.** The simulator writes one JSON frame per tick into `tm:<vehicle>`. Two ingest workers share the vehicles; each vehicle has exactly one owner, enforced by a Postgres advisory lock. The owner validates the frame, classifies its sequence number, stores it idempotently, runs the alarm engine and the command tracker, commits, then acknowledges the Redis entry and publishes a UI event. The API streams UI events to the console over SSE and takes commands, acks and session actions over REST. The archiver turns each finished vehicle-hour into a verified Parquet object in MinIO and only then deletes the hot rows. Replay reads archived (or still hot) frames and pushes them through the same alarm code with the limits that applied at the time.

---

## 2. Decisions by component

Columns: **ID** · **We build** · **Why** · **Instead of** (rejected, and why not) · **Reqs** · **Src**.

### 2.1 Simulator `gs-sim` (consolidated spec — new, gathered from six notes)

The simulator is ours, so its behaviour is a design decision. Downstream code must not depend on anything a real bench would not send (spec §3).

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-SIM-01 | 10 Hz per vehicle on an **absolute schedule** (next tick = start + n·period), `t_onboard` stamped **before** `XADD` | Open-loop producer → latency samples need no coordinated-omission correction | Sleep-after-send loop (drifts, hides stalls) | ING-09, ALM-05 | [R10](research/R10-latency-measurement.md) |
| D-SIM-02 | `t_onboard` as ISO 8601 UTC with **µs** and `Z` | ING-09 is unmeasurable with whole seconds | ms or s precision | ING-09, UI-08 | R10 (A-R10-8) |
| D-SIM-03 | `seq` uint32, +1 per frame, reset to 0 on restart; `t_onboard` = wall clock, stays monotonic across restarts | Lets the tracker tell reset from duplicate with data only | Adding `boot_id` to the frame (spec §3 forbids downstream reliance on new fields) | ING-05 | [R5](research/R5-idempotency-gaps-resets.md) (A-R5-3/5) |
| D-SIM-04 | Each stream entry carries **one field `data`** = JSON frame | Types survive the trip; "unparsable JSON" (`malformed`) has a meaning | Flat string fields (strict validation cannot type them) | ING-02 | [R11](research/R11-stack-and-delivery.md) (A-R11-1) |
| D-SIM-05 | **Buffer frames in order while Redis is down** (bounded deque ≥ 200 = 10 s × 10 Hz × 2), flush on reconnect | Without it, the "stop Redis 10 s" test creates real gaps and fails ING-01 "0 frames lost" | Dropping frames during outage | ENG-02, ING-01 | [R3](research/R3-redis-streams.md) (A-R3-1) |
| D-SIM-06 | Reads `tc:<vehicle>`, executes **≤ 1 command per tick**, writes `{cmd_id, accepted, code, reason, t_onboard}` to **`tcack:<vehicle>`**, then reports `last_cmd_id` in the next frame | RECEIVED needs a source (the §3 frame has none); one-per-tick keeps `last_cmd_id` from skipping a command | Inferring receipt from the simulator's XACK (proves a process read it, not that the vehicle accepted it) | CMD-01, 02 | [R2](research/R2-standards-and-command-safety.md) (A-R2-1 `default`, A-R2-6) |
| D-SIM-07 | Faults: `leak` (−0.4 kPa/s), `overheat`, `dropout N` (**on-board counter keeps running**), `freeze` (**seq and time advance, values repeat**), `seq_jump`, `duplicate` (**byte-identical resend**), `malformed`, `pause N` (for ATP-03) | Each fault's semantics decides which detector must catch it; pinning them makes the tests exact (e.g. 100 ± 1 lost frames) | Leaving fault semantics implicit | ING-02..05, UI-02, CMD-02 | R5 (A-R5-1/2), [R9](research/R9-operator-console.md) (A-R9-3) |
| D-SIM-08 | Cabin pressure noise **σ ≤ 0.027 kPa** | The 0.15 kPa/s rate-of-change limit false-alarms with more noise | Arbitrary noise | ALM-04 | [R8](research/R8-alarm-logic.md) |
| D-SIM-09 | Small HTTP control on the health port: `GET /healthz`, `POST /sim/fault` | Playwright and ATP scripts inject faults the same way every time | Manual fault injection | ENG-03, ENG-05 | R9, R11 |
| D-SIM-10 | **Command effects**: `isolate_valve {valve}` stops an active `leak` and repressurises the cabin at +0.5 kPa/s back to 101.3; `vent_cabin {duration_s}` drops cabin pressure 2 kPa/s for `duration_s`; unknown command or bad args → `tcack accepted=false, code=INVALID_ARGS` | OPS-2/ATP-02 need the alarm to clear after `isolate_valve`; the REJECTED path is needed to test FAILED / NOT_EXECUTED | Commands without effect (the alarm would never clear) | CMD-01, 02, ALM-06 | `lead` |
| D-SIM-11 | Nominal values, noise and fault rates per parameter **exactly as in §3.4**, the same numbers as `limits.yaml` | If simulator and limits disagree, ATP timings are random and tests flake | Separately chosen numbers | ALM-01..04 | `lead`, R8 |

### 2.2 Ingest worker `gs-ingest` (×2)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ING-01 | Redis **Streams**, one stream per vehicle `tm:<vehicle>`, one group named `ingest` on each, fixed consumer names `ingest-1/2` | Already in the stack; ~1000× headroom; COSMOS runs the same layout in production | Kafka (new JVM service, no DLQ, more durability than a bench tool needs); NATS JetStream (new service, no DLQ) | ING-01, 06 | R3, [R1](research/R1-prior-art-mission-control.md) |
| D-ING-02 | **XACK only after the DB COMMIT** | At-least-once delivery; a crash before ack leaves the entry pending, never lost | Ack on read (loses frames on crash) | ING-07 | R3 |
| D-ING-03 | On start **and after every reconnect**, read own pending entries (`XREADGROUP … 0`) before new ones (`>`) | Frames read before a crash come back to the same consumer | Reading only `>` (stranded pending entries) | ING-07 | R3 |
| D-ING-04 | **Processing errors retry in-process 3×, then DLQ + `POISON` event, vehicle continues**; frames that crash the whole worker are caught by `XAUTOCLAIM` (30 s idle) + delivery count > 3 → DLQ | A bad frame costs milliseconds, not a 90 s blind console; the crash case still terminates | Stall vehicle ~90 s until DLQ; blind skip | ING-08 | R3, [R4](research/R4-per-vehicle-ordering.md) (A-R4-5 `default`) |
| D-ING-05 | DLQ write and XACK in **one `MULTI`**; DLQ record `{reason, detail, raw, stream, entry_id, deliveries, first_seen, dlq_at}`, reason ∈ `json_invalid · schema · vehicle_mismatch · t_onboard_range · poison` | A frame is never both acked-and-lost or DLQ'd twice; the reason is inspectable | Separate calls (crash window) | ING-02, 08 | R3, R11 (R13) |
| D-ING-06 | Trim with `XADD … ACKED MAXLEN ~ 360000` (~10 h at 10 Hz, ~40 MB per vehicle) | `ACKED` never deletes un-acked entries; default trimming silently does | Default trimming (frame loss); no trimming (RAM) | ING-07 | R3 (A-R3-3/5) |
| D-ING-07 | Redis config: AOF `appendfsync always` + RDB + `noeviction`, image pinned `redis:8.10.3` | PEL and entries survived graceful restart and `kill -9` in the R3 test; `everysec` loses up to 1 s | `everysec` (breaks "0 frames lost") | ENG-02, ING-07 | R3 |
| D-ING-08 | Worker loop: `BLOCK 1000`, catch `ConnectionError`/`TimeoutError`, back off 0.5 s doubling to 5 s, set `backlog=True` | redis-py gives up after ~5 s; `BLOCK` ≥ socket timeout fails after 62 s | Default client settings (worker crashes on the Redis stop test) | ENG-02 | R3 |
| D-ING-09 | **Vehicle ownership = Postgres session advisory lock** `pg_try_advisory_lock(hashtext('tm:'‖vehicle))` on the same connection that writes that vehicle's data | Lock and writes share one session, so a dead owner can't commit after takeover (lease + fence in one primitive); survives Redis restart | Redis lease (lost on Redis restart, needs a DB check anyway); stateless CAS workers (can't tell lost from in-flight frames); split stateful stage (needs single owner anyway) | ING-06, 07 | R4 |
| D-ING-10 | **Fair share**: each worker locks up to `ceil(V / live_workers)` vehicles, `live_workers` from `pg_stat_activity` | Makes "2 workers share load" true even when one started first | First worker grabs everything | ING-06 | R4 (A-R4-2) |
| D-ING-11 | **Takeover protocol**: lock → load `seq_state`, open alarms, last K frames → warm checks → `XAUTOCLAIM … 0` until cursor `0-0` → process in ID order → then new entries | Pending before new keeps per-vehicle order; no false GAP after a kill | Reading new entries first | ING-06, 07, ALM-02/03 | R4, R8 |
| D-ING-12 | **v1+:** steal path for a hung-but-connected owner (idle > 30 s → `pg_terminate_backend`) | Covers SIGSTOP/deadlock; needs one shared DB role | — (v1 relies on crash → lock release) | ING-08 | R4 (A-R4-4) |
| D-ING-13 | **Storage key `(vehicle, t_onboard, seq)`**, `INSERT … ON CONFLICT DO NOTHING RETURNING` | Timescale requires the time column in unique keys; CCSDS recommends counter + time; a wrong classification can raise a wrong event but never drop or double a frame | `(vehicle, seq)` (drops frames after every restart); `(vehicle, epoch, seq)` (a misdetected epoch loses data) | ING-03, 05, 07 | R5 (A-X-1) |
| D-ING-14 | **Pure sequence tracker** `step(state, seq, t) → (state, events)` with wrap-safe compare (mod 2³²) and the 7-rule table: IN_ORDER, GAP, SEQ_JUMP, DUP_OR_LATE, SEQ_RESET (seq ≤ 50 and time forward), SEQ_ANOMALY | Uses `t_onboard` to split duplicate from reset and dropout from jump; deterministic, so replay and reclaim give the same events | RTP fixed misorder window (calls a quick restart a duplicate) | ING-03, 04, 05 | R5 |
| D-ING-15 | Epoch = `t_onboard` of the first frame after a reset; kept in `seq_state` (updated **in the same transaction** as the frames) and on event rows | Derived from data only → any worker or replay computes the same value | Worker-local boot counter (non-deterministic) | ING-04, 05 | R5 |
| D-ING-16 | `SEQ_JUMP` is its own event, not a GAP; a `SEQ_RESET` shows in the session report as a gap of unknown size | Counter jump ≠ lost frames; the report stays honest (review Q5) | Counting a jump as lost frames | ING-04, DAT-02 | R5 (A-R5-4/6, `lead`) |
| D-ING-17 | Event keys: GAP/SEQ_JUMP `(vehicle, epoch, from_seq)`, SEQ_RESET `(vehicle, epoch)`; only frames classed **new** reach the alarm engine | A redelivered frame raises nothing twice (OPS-4 "no alarm duplicated") | Relying on the frame insert alone | ING-07, ALM-06 | R5 |
| D-ING-18 | **Strict pydantic frame model**: `strict=True`, `allow_inf_nan=False`, uint32 bounds, UTC-only aware time, UUID, `vehicle` = stream suffix, `t_onboard` within [now − 7 d, now + 5 min]; parameter fields generated from the registry with `create_model` | Rejects NaN, `"5"`, naive times, wrong vehicle; adding a parameter needs no code | Lax parsing (hides simulator bugs) | ING-02, ENG-06 | R11 (R13) |
| D-ING-19 | Unknown frame field → kept in `model_extra`, **counted, warned once, not stored** | Simulator ahead of config must not DLQ every frame | `extra="forbid"` | ING-02, ENG-06 | R6, R11 (A-R6-3) |
| D-ING-20 | Registered parameter missing from a frame → **stored as NULL, counted, warned** | Adding a parameter to config before the simulator sends it must not quarantine every frame | DLQ (breaks the add-a-parameter guide) | ING-02, ENG-06 | R11 (A-R11-3 `default`) |
| D-ING-21 | **FROZEN detector**: 20 consecutive frames with bit-identical parameter values → `acq=FROZEN` (shown like STALE) | `freeze` keeps frames arriving, so arrival-based staleness never fires; "no old value shown as current" | Treating arrival as freshness | UI-02, OPS-3 | R9 (A-R9-3) |
| D-ING-22 | One DB transaction per `XREADGROUP` batch: psycopg 3 `executemany` (pipeline mode) + `ON CONFLICT`, then COMMIT, then XACK, then `XADD ui:events` | COPY can't skip duplicates; 200 rows/s needs no more | COPY + staging table; asyncpg (no type hints for mypy strict) | ING-07, 09 | R6, R5 |
| D-ING-23 | `t_received DEFAULT clock_timestamp()` (not `now()`) | `now()` is transaction start → batched inserts understate latency | `now()` | ING-09 | R10 (A-R10-1) |
| D-ING-24 | **Postgres outage**: on any DB error the worker rolls back, drops all vehicle ownership, does **not** XACK, backs off 0.5 → 5 s, reconnects, re-locks and runs the takeover protocol | Frames wait safely in Redis (`ACKED` trimming keeps them); never write on a connection that doesn't hold the lock (R4 rule 4) | Retrying on a fresh connection without re-locking (breaks the fence) | ENG-02, ING-07 | R4, `lead` |
| D-ING-25 | Each worker sets `hb:ingest:<consumer>` in Redis with `EX 3` every second | The console can tell "ground processing down" from "vehicle silent" | Guessing from missing frames | UI-02, ENG-05 | `lead` |

### 2.3 Alarm engine (library used by ingest and replay)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ALM-01 | **On a limit = the more severe band** (`v ≤ low`, `v ≥ high`); load-time check `red_low < yellow_low < yellow_high < red_high` | EPICS and COSMOS precedent; gives ALM-01's "exactly on" test a defined answer | Yamcs/XTCE default (on-limit = nominal) | ALM-01 | R8, R1 |
| D-ALM-02 | **Hysteresis = % of span** (`red_high − red_low`) → absolute deadband `d`; only delays moves to a *less* severe band; `hysteresis_abs` for one-sided limits | % of limit value is 0 at a limit of 0 (rates, °C) and flips for negative limits | % of limit value | ALM-02 | R8 (A-R8-2 `default`) |
| D-ALM-03 | **Persistence**: ring of the last N raw levels; escalate when all N ≥ L, de-escalate when all N ≤ L; red needs no own N | On-delay + off-delay over the deadband (series design in the literature); a 1-frame spike never alarms | Persistence on raise only | ALM-03 | R8 (A-R8-3) |
| D-ALM-04 | GAP, SEQ_RESET and STALE **reset counters and the ROC window but never clear an alarm** | A value can be RED **and** STALE; silence is not recovery | Clearing on loss of data | ALM-03, UI-02 | R8 (A-R8-4), R1 |
| D-ALM-05 | **Rate of change = least-squares slope** (`statistics.linear_regression`) over the last `window_s` = 1 s of `t_onboard`; needs ≥ 80 % of expected samples; optional `direction: rising·falling·both` (default both) | ~1.4× less noisy than end-point difference, 13× less than frame-to-frame; alarms the 0.4 kPa/s leak ~7.5 s before yellow; vehicle time, not arrival time | Frame-to-frame difference; arrival-time slope | ALM-04 | R8 (A-R8-8 `default`) |
| D-ALM-06 | **One alarm per (vehicle, param)**; severity = max of its checks; `trigger_check` recorded | A leak gives one escalating alarm, not three | One alarm per check | ALM-06, UI-05 | R8 |
| D-ALM-07 | Life cycle **UNACK → ACKED → CLEARED**, plus **RTN_UNACK** ("cleared – not acked"); ACK requires a note; who/when/why stored on every transition | Matches the spec's ACTIVE→ACKED→CLEARED and ISA-18.2; RTN before ack must stay visible | Auto-clear on return to normal | ALM-06 | R8 (A-R8-7), R1 |
| D-ALM-08 | **Acked yellow that escalates to red becomes UNACK and alerts once**; red→yellow→red after an ack does not alert again (`acked_sev` kept) | Answers review Q2 ("I acked and it got worse"); Yamcs keeps the ack here, we deliberately don't | Keeping the ack (Yamcs) | ALM-06, UI-05 | R8 (A-R8-5 `default`) |
| D-ALM-09 | Only **RED** alerts audibly (`alert` column on the transition row); yellow is visual | OPS-2 "an alarm sounds once" | Sounding on every transition | UI-05 | R8 (A-R8-6) |
| D-ALM-10 | **Pure checks**: `Check` Protocol with `kind`, pydantic `Config`, `initial()`, `step(state, sample, current) → (state, level)`; `@register` into `CHECKS`; package auto-imports every module | "New class + registration only" = add one file, no worker edit | Plugin framework; if/else in the worker | ALM-07 | R8, R1 |
| D-ALM-11 | Every per-check debounced level change logged as a **`LEVEL` event**; rebuild after takeover or for replay = seed from LEVEL events, then warm up the last K frames | Hysteresis has memory; re-feeding from NOMINAL can differ from live | Re-feeding frames from NOMINAL | DAT-06, ING-06 | R8, R7 |
| D-ALM-12 | **Limits reload**: poll `limits.yaml` every 1 s (`stat`, then sha256 of bytes), validate with pydantic (`extra="forbid"`), store in `limits_versions(sha256 PK, content)`, emit `LIMITS_APPLIED(first_seq, sha)`; invalid file keeps the old set + `LIMITS_REJECTED` banner | New limit active < 5 s; every alarm carries the hash; replay switches at the same frame as live (review Q6) | inotify/watchdog dependency; restart to reload | ALM-08 | R8 |
| D-ALM-13 | Out of scope: shelving, latching, suppression, re-alarm timers, limit sets, 5 severity levels | Not in the spec; each adds UI + audit paths | — | — | R8 (A-R8-9), R1 |

### 2.4 Commanding (API + vehicle owner)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-CMD-01 | States **QUEUED → SENT → RECEIVED → VERIFIED**, or **FAILED**; RECEIVED from `tcack` (accepted), VERIFIED when `last_cmd_id == cmd_id` (a telemetry-based verifier, like XTCE Complete / Yamcs algorithm verifier) | Spec names kept; maps cleanly to PUS service 1 acceptance/completion | Extra start/progress states (no source in our frame) | CMD-01 | R2, R1 |
| D-CMD-02 | FAILED carries `failure_reason` (SEND_ERROR · REJECTED · NOT_RECEIVED · NOT_VERIFIED) and **`outcome`** (NOT_SENT · NOT_EXECUTED · **UNKNOWN**); UI text says which | Answers review Q3 "did the vehicle do it?"; PUS: silence is not failure; never blind-resend a hazardous command after UNKNOWN | A bare FAILED | CMD-02, UI-06 | R2 |
| D-CMD-03 | **Both windows from SENT**: not received in 5 s, not verified in 15 s; per-command overrides in `commands.yaml` | Simple to explain; worst case is 15 s, not 20 s | Yamcs chaining from previous stage | CMD-02 | R2, R1 (A-R1-1) |
| D-CMD-04 | **FAILED is terminal**; a later echo writes `LATE_VERIFICATION` + `late_verified_at` | History is append-only; investigators need to know the operator saw FAILED; ATP-03's 16 s pause must expect FAILED **then** LATE_VERIFICATION | Overwriting to VERIFIED (Yamcs) | CMD-02, 04 | R2 (A-R1-2) |
| D-CMD-05 | **Command tracking runs in the vehicle's owner worker**: it reads `tcack:<v>` alongside `tm:<v>`, matches `last_cmd_id`, and checks timeouts every loop pass (≤ 1 s resolution because `BLOCK 1000` returns even when the vehicle is silent); timers keep running during LOS | The owner already sees frames in order and is the single writer for that vehicle; no extra service or scheduler | A separate tracker service; timers in the API process | CMD-01, 02 | `lead` (A-R2-7) |
| D-CMD-06 | **Hazardous two-step enforced by the API**: first POST rejected with `HAZARDOUS_CONFIRMATION_REQUIRED`, hazard text and `args_hash` = sha256 of canonical `{vehicle, name, args}`; re-POST must carry matching `confirm_args_hash` + `reason` (≥ 10 chars) | curl, scripts and UI all hit the same rule; you can't confirm A and send B | Client-only dialog (fails "cannot be sent without both"); server-stored tokens (more state, no extra protection) | CMD-03 | R2, R1 |
| D-CMD-07 | **Double-command interlock**: `SET cmdlock:{vehicle}:{name}:{args_hash} {cmd_id}\|{user} NX PX 2000` as the last check; key includes args, excludes user | Atomic, expiry is the window; also stops one user's double-click; Redis down blocks commanding anyway (fail closed) | Postgres time-bucket unique (bucket edges leak); XTCE interlock (would block an urgent `isolate_valve`) | CMD-05 | R2 (A-R2-4 `default`) |
| D-CMD-08 | **Append-only `audit_log`**: actions `CMD_REQUESTED · CMD_CONFIRM_REQUIRED · CMD_REJECTED · CMD_STATE · CMD_LATE_VERIFICATION · ALARM_ACK · LIMITS_RELOADED · SESSION_START/STOP · LOGIN`; app role `INSERT, SELECT` only + triggers that raise on UPDATE/DELETE/TRUNCATE | Two independent layers; the UI-06 timeline reads from it; tested by trying to UPDATE | Hash chain / WORM storage (tamper evidence against an admin is out of scope) | CMD-04, UI-06 | R2, R6 |
| D-CMD-09 | `commands.yaml` validated with pydantic at load; per command `description`, `hazardous`, `hazard` text, typed `args`; file sha256 stored on each command row | Same versioning idea as limits; typed args catch bad input at the API | XTCE (XML schema with ~100 element types) | CMD-03, ENG-06 | R2 |
| D-CMD-10 | **No roles**: any logged-in user may command and run sessions; identity always from the session cookie, never the request body | A simple login is in scope, roles are not; second-person approval is the documented upgrade | Conductor/controller roles; second-user approval (COSMOS Enterprise) | CMD-03, 04 | R11 (A-R11-5 `default`), R2 |

### 2.5 Storage (TimescaleDB)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-DB-01 | **One TimescaleDB** (`timescale/timescaledb:2.32.0-pg17`) for telemetry, events, alarms, commands, sessions, audit | One store, one backup, reports join in one query; 5–100× margin on measured targets | InfluxDB 3 / QuestDB / ClickHouse (second store, no verified row delete) | DAT-03 | [R6](research/R6-timeseries-store.md) |
| D-DB-02 | **Wide hypertable**: one row per frame, one typed column per parameter, 1 h chunks aligned with the hourly archive | Measured: 1 h of one param 2–24 ms (target 300 ms), 24 h band 58–424 ms (target 2 s) | JSONB (24 h band 2.3–6.1 s, fails UI-04); narrow rows (4.75× disk, 2–4× slower) | DAT-03, UI-04 | R6 |
| D-DB-03 | Parameter columns created **by `gs-migrate`** from the registry (D-DB-11): `ADD COLUMN IF NOT EXISTS` (name checked `^[a-z][a-z0-9_]{0,62}$`) | Adding a parameter = one YAML entry; 2 ms in the spike | Hand-written migrations per parameter | ENG-06 | R6 |
| D-DB-04 | Downsampling with `time_bucket` min/max/avg/count, bucket = max(window / 1500, 100 ms); empty buckets absent (chart shows a gap) | Server-side band for any window; gaps stay visible | Continuous aggregates (non-Apache licence, not needed at 0.45 GB/day) | UI-04 | R6 |
| D-DB-05 | **Apache-2 features only** (no compression, no continuous aggregates) | Keeps plain Postgres (`date_bin`) as a fallback | TSL features | DAT-03 | R6 (A-R6-6) |
| D-DB-06 | Session report = one SQL query over the session window (min/max/avg per param, count vs expected, GAP events, alarms, commands) + CSV export | 15–31 ms for 6 h in the spike; answers review Q5 | Precomputed rollups | DAT-02 | R6, R1 |
| D-DB-07 | `test_session(vehicle, name, conductor, started_at, ended_at, ended_by)`; **partial unique index: one active session per vehicle**; frames always stored, linked to sessions by time window | Clear 409 on double start; out-of-session frames are not lost | FK from telemetry to session (breaks storing out-of-session frames); exclusion constraint (more than needed) | DAT-01 | R11 (R13) |
| D-DB-08 | `events` table with `severity ∈ {INFO, LOW, MEDIUM, HIGH}` and `source ∈ {GROUND, ONBOARD}` (PUS service 5 vocabulary) | A future CCSDS decoder maps on-board events into the same table | Separate tables per event type | ING-04, CMD-04 | R2 |
| D-DB-09 | `alarm_events` (incl. LEVEL, SEQ_RESET, LIMITS_APPLIED) and `limits_versions` are **never pruned** | Replay needs them to rebuild state; they are small | Pruning with telemetry | DAT-06, ALM-08 | R7 (A-R7-5) |
| D-DB-10 | At session stop the report is **frozen** into `session_report(session_id, report, csv, sha256)`; `POST /api/sessions/{id}/sign-off {note}` (stopped sessions only) writes audit `REPORT_SIGNED` with that sha256 | "Get a report signed off after the test" (conductor role, §2); the signature points at exact bytes that can't change after late frames or hot delete | Recomputing the report on every view | DAT-02, CMD-04 | R6 (A-R6-4), `lead` |
| D-DB-11 | **Parameter registry = the `parameters:` section of `limits.yaml`** (name, unit, optional checks). `gs-migrate` (owner role) adds missing columns; workers refuse to start if a registered parameter has no column; a hot reload that adds an unknown parameter is rejected with `LIMITS_REJECTED: run make migrate` | The app role can't `ALTER TABLE` (role design of D-CMD-08); one file is the one place to add a parameter | A separate `parameters.yaml`; `ALTER` from the app role | ENG-06, ALM-08 | R6, R8, `lead` |

### 2.6 Archive and replay

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ARC-01 | One **Parquet** object per vehicle-hour, pyarrow, zstd, one row group, schema from the hot table, footer metadata (`limits_sha256`, row count, seq range, schema version) | ~0.91 MB per vehicle-hour, ~10× smaller than hot rows | snappy (1.13 MB); CSV | DAT-04 | [R7](research/R7-archive-replay.md) |
| D-ARC-02 | **Hash the uploaded bytes** (sha256 recorded in Postgres **before** upload) | Parquet bytes are only reproducible with the same pyarrow version | Byte-reproducible files as the integrity basis | DAT-05 | R7 |
| D-ARC-03 | **Single PUT** with `x-amz-checksum-sha256` (MinIO rejects a mismatch); then **full re-read** against the recorded hash; then sidecar `<HH>.parquet.sha256` in `sha256sum` format | Three independent checks before any delete; a killed upload leaves no visible object (shown in the R7 spike) | Multipart (objects are below the 5 MB part minimum; leaves parts behind); ETag as hash (not MD5 for multipart) | DAT-04, 05 | R7 (A-R7-1) |
| D-ARC-04 | Job state in **`archive_object`**: WRITTEN → UPLOADED → VERIFIED → HOT_DELETED (PENDING computed from eligible hours); every state safely re-runnable | Kill mid-upload → re-run finishes the job, nothing lost, no manual cleanup | Stateless cron | DAT-05 | R7 |
| D-ARC-05 | Eligible = vehicle-time watermark + 2 min (or wall clock + 30 min if silent); **hot delete only when hour_end ≤ now − 25 h**, per (vehicle, hour), in one transaction, commits only if deleted rows = `archive_object.hot_rows` | The 24 h chart is always hot; the row-count check catches late frames | `add_retention_policy` / TTL (deletes unverified data) | DAT-05, UI-04 | R7, R6 (A-R7-4) |
| D-ARC-06 | **v1: late frames** into an archived hour stay hot and raise an alert (the delete check refuses). **v1+:** write an immutable `<HH>.v<N>.parquet`; readers take the latest verified version | Nothing is lost in v1; versioning is extra code for a rare case | Overwriting `<HH>.parquet` (data/sidecar disagree for a window); separate `late/` objects every reader must merge | DAT-04, 05 | R7 (A-R7-2), `lead` |
| D-ARC-07 | Scheduler: plain **asyncio loop every 5 min** in `gs-archiver` | One loop does not justify a dependency | APScheduler (4.x still alpha) | DAT-04 | R7 |
| D-ARC-08 | S3 client: **minio-py** | Ships type hints (mypy strict) | boto3 (needs stubs) | ENG-09 | R7 |
| D-ARC-09 | MinIO image **`bitnamilegacy/minio:2025.5.24` pinned by digest**; listed in README limitations | Upstream stopped publishing images (repo archived 2026-04-25); this one is built from official source and passed the R7 spike | `pgsty/minio` (current but third party, contents unverified); building from source | DAT-04, ENG-01 | R7, R11 (A-X-3 `default`) |
| D-ARC-10 | Archiver `/healthz` reports `oldest_unarchived_hour`; older than 72 h (config) → `degraded` + `ARCHIVE_BACKLOG` event (HIGH) shown as a console banner; MinIO unreachable → retry with backoff, never delete | Hot data grows while MinIO is down and DAT-05 forbids deleting it, so someone has to find out | Silent growth until the disk fills | DAT-05, ENG-05 | R6 (A-R6-5) |
| D-REP-01 | **Replay** reads the verified objects (hash checked) or the hot table for hours not yet archived, seeds state from `alarm_events`, runs the **same pure `step()`** as live, switches limits at the recorded `LIMITS_APPLIED` frame | Same function + same inputs ⇒ same alarms (spec: "same alarms as live"); Yamcs replays with current limits, we use the recorded ones | DuckDB over S3 (downloads an extension at runtime, skips our hash check); shadow tables | DAT-06 | R7, R8, R1 |
| D-REP-02 | Pacing by `t_onboard` deltas at 1× or 10× | Replays vehicle time; a live backlog burst is not replayed as a burst | `t_received` pacing | DAT-06 | R7 (A-R7-8) |
| D-REP-03 | **Overlay live ACKs** at their `after_seq` (read-only); replay never sounds and never accepts acks | Re-alarm-on-escalation depends on ack state; without ACKs replay alarms would differ from live | No overlay (R9's first proposal) | DAT-06 | R7, R9 (A-X-4) |
| D-REP-04 | Output only to SSE stream `ui:replay:<id>` (same message shapes, `replay: true`), into a separate store in the UI; purple **REPLAY** banner that can't be dismissed; never writes live tables | Replay can't be mistaken for live or corrupt live data | WebSocket channel (second transport); writing to tables | DAT-06 | R9, R7 (A-X-2, A-R5-7) |
| D-REP-05 | Equality test: alarm transition keys from a live run = keys from replaying the same window | Turns "same alarms as live" into one list comparison | Visual comparison | DAT-06 | R7 |
| D-REP-06 | STALE is display-only in replay; its keyed equivalent (GAP) is in the equality test | The STALE clock lives in the browser | Making STALE a stored event | DAT-06 | R7 (A-R7-7, `lead`) |

### 2.7 Operator console (React + TypeScript)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-UI-01 | **SSE** from FastAPI fed by Redis stream **`ui:events`** (worker `XADD` after commit, `MAXLEN ~ 3000`; API reads with plain `XREAD`, no group) | Built-in reconnect + resume; one-way is enough (commands go over REST); a stream loses no alarm/command event across restarts; no consumer group, so `ACKED` trimming of `tm:*` is unaffected | WebSocket (hand-written reconnect/resume/heartbeat); Redis pub/sub (at-most-once); reading `tm:*` (UI would duplicate alarm logic) | UI-01, 07 | R9 (A-R9-1) |
| D-UI-02 | On connect: resume from `Last-Event-ID` / `?since=` if still in the stream, else send a **snapshot** then deltas; `retry: 1000`; per-client bounded queue, overflow → reconnect + snapshot | Measured 0.87 s from API back to data (budget 5 s) | Always full reload | UI-07 | R9 |
| D-UI-03 | **Client watchdog** that opens a new EventSource with `?since=<lastId>` | A proxy 502 during restart closes EventSource for good; a new object doesn't send `Last-Event-ID` | Trusting built-in reconnect only | UI-07 | R9 |
| D-UI-04 | **Heartbeat every 1 s**; banners: **LOSS OF SIGNAL** = no frame for 2 s while heartbeat alive; **NO LIVE CONNECTION** = no message for 3 s | Separates "vehicle silent" from "my link to the server is down" | One combined STALE state | UI-02, 07 | R9 (A-R9-4) |
| D-UI-05 | **Freshness clock in the browser** (`performance.now()` seeded with server `age_ms`, 100 ms tick, STALE at 2.0 s) | Immune to clock skew; a backlog replayed after reconnect arrives already STALE; works when the link is down | Server-pushed STALE only (fails when the link is down) | UI-02 | R9 (A-R9-2) |
| D-UI-06 | Each value has **two fields**: acquisition (`OK · STALE · FROZEN · NO_DATA`) × limit (`NOMINAL · YELLOW · RED` + LOW/HIGH); a STALE row keeps its last limit as an outline "was RED LOW" badge, value in italic + clock glyph | A value can be RED and STALE; COSMOS's STALE-as-colour loses the last limit during LOS | One merged enum | UI-02, 03 | R1, R9 (A-R1-3, A-R9-5) |
| D-UI-07 | **Grey when nominal, colour only for abnormal**, every state also as text + glyph; palette verified WCAG AA (e.g. red `#B3141B` with white 6.92:1; yellow with `#7A5C00` border 5.25:1); REPLAY = purple, used for nothing else; light theme only | ISA-101 high-performance HMI; no state by colour alone | Green-for-nominal (COSMOS) | UI-03 | R9, R1 (A-R9-9) |
| D-UI-08 | Audio armed by the login click (`AudioContext.resume()`); **one beep per `alert:true` transition key**; visible SOUND OFF badge if audio isn't running | Browser autoplay policy needs a gesture; "sounds once" | Beeping per frame | UI-05 | R9 (A-R9-7) |
| D-UI-09 | Alarm list sticky until acked, filters by vehicle/severity/state, ACK dialog with required note | Spec UI-05 + COSMOS Limits Monitor pattern | — | UI-05 | R1, R9 |
| D-UI-10 | Command timeline with all four state times in UTC, read from the audit log | Spec UI-06 | — | UI-06 | R2, R9 |
| D-UI-11 | Charts with **uPlot** (23.5 KB gz, canvas, native min/max bands), windows 2 min · 1 h · 24 h, server downsampling | Fast and small | ECharts (~198 KB), Recharts/visx (SVG, slow when streaming) | UI-04 | R9 |
| D-UI-12 | **No UI kit**; plain store with `useSyncExternalStore` (per-row subscriptions); native `<dialog>`; hooks shaped like Open MCT (`useTelemetry`, `useStaleness`) | 10 Hz updates without re-rendering the table; fewer dependencies | Redux; Open MCT itself (Vue, contradicts ENG-10) | UI-01, ENG-10 | R9, R1 |
| D-UI-13 | Times formatted with `toISOString()` (always `Z`) | UTC everywhere without a date library | moment/dayjs | UI-08 | R9 |
| D-UI-14 | Latency probe: each rendered value exposes `data-seq`/`data-t-onboard`; a stamp in the second `requestAnimationFrame` after commit; Playwright collects with `page.evaluate` | Every frame gets a sample, including ones superseded before paint | Playwright polling the DOM | UI-01 | R10, R9 |
| D-UI-15 | Heartbeat carries `ingest_ok` (any `hb:ingest:*` alive); banner **GROUND PROCESSING DOWN** replaces LOS when no worker is alive | A dead worker must not look like loss of signal; wrong diagnosis for the flight controller (OPS-4) | Showing LOS | UI-02, 07 | `lead` |
| D-UI-16 | **Replay page**: vehicle, from/to as `<input type="datetime-local">` labelled and interpreted as **UTC**, speed 1× / 10×, start/stop, progress (current replay `t_onboard`); purple frame + banner; separate store | OPS-6 needs a way to pick "14:00–14:20 last Tuesday" | Date-picker library | DAT-06, UI-08 | `lead` |
| D-UI-17 | **Session panel**: start (vehicle + name), stop, report view, CSV download, sign-off with note | The conductor's whole job in OPS-1 | — | DAT-01, 02 | `lead` |

### 2.8 Delivery and engineering

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ENG-01 | **One Python package `gs`** (`src/gs/{common,simulator,ingest,archiver,api}`), entry points `gs-sim · gs-ingest · gs-archiver · gs-api · gs-migrate` | All services share dependencies | uv workspace (N× config for nothing) | ENG-10 | R11 |
| D-ENG-02 | **One Dockerfile**, two stages (Node builds `ui/dist`, Python 3.14-slim + uv runs everything); FastAPI serves the built UI | One image, one origin, no CORS, no nginx | nginx container | ENG-01 | R11 |
| D-ENG-03 | **docker compose**: `restart: unless-stopped`, healthchecks, `depends_on` conditions, one-shot `migrate` and `minio-init`; `make up` creates `.env` with a random session secret, then `docker compose up -d --build --wait` | Fresh clone → `make up` → demo works first try | Manual setup steps | ENG-01, 02 | R11 |
| D-ENG-04 | Config from **env only** into a frozen dataclass (fails fast); `.env.example` committed; no hard-coded hosts | Spec ENG-09; ~10 lines | pydantic-settings | ENG-09 | R11 |
| D-ENG-05 | **Tests at 3 levels**: unit (pure functions: tracker, checks, state machines), integration against the **same compose** Redis + Postgres, E2E with **Playwright in TypeScript** | One set of image pins for local, CI and E2E | testcontainers / GitHub `services:` (duplicate pins) | ENG-03 | R11 |
| D-ENG-06 | **CI** (one workflow): `lint` (ruff check + format, `mypy --strict`, `tsc --noEmit`) ∥ `test` (unit + integration) → `e2e`; same workflow green on the `v*` tag = release evidence | Spec ENG-04 + §6 "green run on the release tag" | Separate workflows | ENG-04 | R11 |
| D-ENG-07 | `/healthz` on every service (small asyncio HTTP server in workers); **stdlib logging with a JSON formatter**, every line with `vehicle` and `seq` where relevant | Spec ENG-05 with no new dependency | structlog | ENG-05 | R11 |
| D-ENG-08 | **Login**: fixed users in `users.yaml` with stdlib scrypt hashes; Starlette signed session cookie (`SameSite=strict`, 12 h); CSRF = required header `X-GS-Client: console` + JSON-only writes | "A simple login is enough" (spec §1), done safely | JWT; SSO (out of scope) | CMD-04 | R11 |
| D-ENG-09 | Versions pinned as in [R11's pin table](research/R11-stack-and-delivery.md) (Python 3.14, React 19, Vite 8, **TypeScript 6.0.3**, FastAPI 0.162, pydantic 2.14, redis-py 8.1, psycopg 3.3, Playwright 1.65) | Reproducible builds; TS 7 breaks typescript-eslint | Floating versions | ENG-01, 09 | R11 |
| D-ENG-10 | **Conventional commits**, small commits from the first day (research is the first commits) | Spec ENG-07 | One giant first commit | ENG-07 | — |
| D-ENG-11 | Kill-a-worker tests: `docker compose kill ingest-1` proves **takeover** by the other worker; `kill -9 1` inside the container proves **self-restart** | `docker kill` does not trigger the restart policy; a crash inside the container does | Assuming both behave the same | ING-07, ENG-02 | R11 (A-R11-6) |
| D-ENG-12 | **Migrations** = numbered `migrations/NNN_*.sql`, applied in order by `gs-migrate` as `gs_owner`, one transaction each, recorded in `schema_migrations(version, sha256, applied_at)`; refuses to run if an applied file changed; then adds registry columns (D-DB-11) | ~30 lines; with no ORM, Alembic adds nothing | Alembic; `CREATE IF NOT EXISTS` scattered across services | ENG-01, 09 | `lead` |
| D-ENG-13 | **Demo users**: `make up` copies `users.example.yaml` → `users.yaml` if missing (`conductor`, `fc1`, `fc2`), generates one password into `.env` and prints it once | CMD-05 needs two operators at once; ENG-01 "demo works first try" | Manual user setup | ENG-01, CMD-05 | `lead` |

### 2.9 Measurement (method A requirements)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-PERF-01 | All stamps except UI-01 in **one clock** (Docker VM kernel): `t_onboard`, Redis entry ID ms, `t_received`, `raised_at` (`clock_timestamp()`) | Containers share the kernel clock → no offset error; the entry ID splits onboard→Redis from Redis→stored | Comparing across clock domains | ING-09, ALM-05, ING-10 | R10 |
| D-PERF-02 | UI-01 corrected by a browser **min-RTT offset ping** before and after the run; run invalid if the offset moves > 5 ms | Only metric crossing VM → macOS clock | Ignoring offset | UI-01 | R10 |
| D-PERF-03 | **Raw samples to CSV** in `docs/perf/`, nearest-rank percentiles (stdlib), first 10 s excluded and reported | Spec §5 asks for raw numbers | HdrHistogram (not needed for ≤ 60 k samples) | all A-method reqs | R10 (A-R10-7) |
| D-PERF-04 | **ALM-05 on 60 alarms** (report 20 and 60); latency from the N-th (triggering) frame, alarm row stores `trigger_seq`, `trigger_t_onboard` | 20 samples can't bound a p95 (n ≥ 59 needed) | 20 only | ALM-05 | R10 (A-R10-2/3) |
| D-PERF-05 | **Run N** = 2 × 10 Hz, 10 min (pass/fail for ING-09, ALM-05, UI-01, DAT-03); **run P** = 2 × 100 Hz, 5 min (ING-10, others informative); optional **P′** with `appendfsync everysec` to price the fsync | Pass/fail at the specified rate; fsync cost measured, not guessed | Judging everything at 100 Hz | ING-09, 10, ALM-05, UI-01 | R10 (A-R10-6) |
| D-PERF-06 | ING-10 sampler at 1 Hz: `XINFO GROUPS` lag + pending and time lag (`clock_timestamp() − max(t_onboard)`); pass = max time lag < 1 s | Spec's "lag" read as time | Entry count only | ING-10 | R10 (A-R10-5) |
| D-PERF-07 | Takeover-window samples reported **separately**, never silently dropped | Honest p95 | Excluding them quietly | ING-09, ALM-05 | R4 (A-R4-3) |
| D-PERF-08 | Report: machine spec, image digests, load average before/after, raw CSV, `make perf` to reproduce | Spec §5 "on what machine, raw numbers" | Numbers without context | all A-method reqs | R10 |

---

## 3. Interfaces (consolidated; ★ = added to spec §3)

**Redis streams and keys**

| Name | Writer → reader | Content | Notes |
| --- | --- | --- | --- |
| `tm:<vehicle>` | sim → ingest (group `ingest`) | field `data` = JSON frame (§3 fields) | `ACKED MAXLEN ~ 360000` |
| `tc:<vehicle>` | api → sim | `cmd_id, name, args, issued_by, issued_at, reason` | §3 |
| ★ `tcack:<vehicle>` | sim → owner worker | `cmd_id, accepted, code, reason, t_onboard` | source of RECEIVED |
| `tm:dlq` | ingest → humans/tests | `reason, detail, raw, stream, entry_id, deliveries, first_seen, dlq_at` | one stream for malformed + poison |
| ★ `ui:events` | ingest → api | `tm · alarm · cmd · link` messages (R9 shapes) | `MAXLEN ~ 3000`, plain `XREAD` |
| ★ `ui:replay:<id>` | api replay task → api SSE | same shapes, `replay: true` | dropped when the replay ends |
| ★ `cmdlock:{v}:{name}:{args_hash}` | api | `cmd_id\|user`, `PX 2000` | CMD-05 interlock |

**SSE** `GET /api/events` (live), `GET /api/replay/<id>/events`; events `hello · snapshot · tm · alarm · cmd · link · hb` (shapes in [R9](research/R9-operator-console.md)).

**Postgres tables**: `telemetry` (hypertable) · `seq_state` · `events` · `alarms` + `alarm_events` · `limits_versions` · `commands` · `audit_log` · `test_session` · `archive_object`. DDL drafts: R6 (telemetry, audit), R11 (test_session), R7 (archive_object), R5 (seq_state).

**Config files**: `limits.yaml` (schema in [R8](research/R8-alarm-logic.md)) · `commands.yaml` (schema in [R2](research/R2-standards-and-command-safety.md)) · `users.yaml` (scrypt hashes) · `.env`.

**Object store**: `archive/<vehicle>/<YYYY>/<MM>/<DD>/<HH>.parquet` + `<HH>.parquet.sha256`; v1+: `<HH>.v<N>.parquet`.

**Decoder plug point** (review Q13): `Decoder = Callable[[bytes], Frame]`, raises `DecodeError(reason)` → DLQ; registry `{"json": decode_json}`; a CCSDS decoder would map APID → vehicle, 14-bit count → `seq` (wrap at 2¹⁴), PUS secondary-header time → `t_onboard` ([R2](research/R2-standards-and-command-safety.md)).


### 3.1 HTTP API (`gs-api`, one origin with the UI)

All writes need the session cookie and header `X-GS-Client: console`, take JSON, and take the user from the session. Errors are `{"error": "<CODE>", "message": "<text for the operator>"}`.

| Method · path | Body / query | Success | Errors | Reqs |
| --- | --- | --- | --- | --- |
| `POST /api/login` · `POST /api/logout` · `GET /api/me` | `{user, password}` | 204 / `{user}` | 401 | CMD-04 |
| `GET /api/events` (SSE) | `?since=` / `Last-Event-ID` | `hello, snapshot, tm, alarm, cmd, link, hb` | — | UI-01, 02, 07 |
| `GET /api/vehicles` · `GET /api/limits` | — | vehicles + params · current limits + sha256 | — | ALM-08 |
| `GET /api/telemetry/{vehicle}/{param}` | `?from&to` (UTC `Z`) | `[{t, lo, hi, mean, n}]` | 400 unknown param, window > 24 h hot | UI-04, DAT-03 |
| `GET /api/commands/catalog` | — | `commands.yaml` view + sha256 | — | CMD-03 |
| `POST /api/commands` | `{vehicle, name, args, confirm_args_hash?, reason?}` | 202 `{cmd_id}` | 409 `HAZARDOUS_CONFIRMATION_REQUIRED` `{hazard, args_hash}` · 409 `CONFIRMATION_MISMATCH` · 409 `DUPLICATE_COMMAND` `{by, seconds_ago, cmd_id}` · 422 `INVALID_ARGS` · 503 `BROKER_DOWN` | CMD-01..05 |
| `GET /api/commands` | `?vehicle&since` | commands + state timeline | — | UI-06 |
| `GET /api/alarms` | `?vehicle&severity&state` | alarms | — | UI-05 |
| `POST /api/alarms/{alarm_id}/ack` | `{request_id, note}` | 200 (idempotent on `request_id`) | 409 already cleared · 422 empty note | ALM-06 |
| `POST /api/sessions` · `POST /api/sessions/{id}/stop` | `{vehicle, name}` · — | 201 · 200 | 409 `SESSION_ACTIVE` · 409 already stopped | DAT-01 |
| `GET /api/sessions` · `GET /api/sessions/{id}/report` · `…/report.csv` | — | list · JSON · CSV | 409 session still running | DAT-02 |
| `POST /api/sessions/{id}/sign-off` | `{note}` | 200 | 409 not stopped | DAT-02 |
| `POST /api/replays` · `DELETE /api/replays/{id}` · `GET /api/replays/{id}/events` (SSE) | `{vehicle, from, to, speed: 1·10}` | 201 `{replay_id}` | 404 no data in window · 409 archive object failed hash check | DAT-06 |
| `GET /healthz` (every service) | — | `{status: ok·degraded, checks}` | 503 when down | ENG-05 |
| Simulator `POST /sim/fault` · `DELETE /sim/fault` (port 8081, compose-internal, bound to 127.0.0.1 for tests) | `{vehicle, fault, params}` | 204 | 422 | ATPs |

### 3.2 Remaining table drafts (others: R6 `telemetry`, `audit_log`; R11 `test_session`; R7 `archive_object`)

```sql
CREATE TABLE seq_state (vehicle text PRIMARY KEY, epoch timestamptz NOT NULL, last_seq bigint NOT NULL,
  last_t timestamptz NOT NULL, period_s double precision NOT NULL);                  -- R5, same txn as frames

CREATE TABLE events (            -- GAP SEQ_JUMP SEQ_RESET SEQ_ANOMALY POISON LOS AOS FROZEN LATE_VERIFICATION
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   --  LIMITS_APPLIED LIMITS_REJECTED ARCHIVE_BACKLOG
  key text NOT NULL UNIQUE,                             -- deterministic, e.g. 'GAP/NYX-TA1/<epoch>/<from_seq>' (R5)
  vehicle text, kind text NOT NULL,
  severity text NOT NULL CHECK (severity IN ('INFO','LOW','MEDIUM','HIGH')),
  source text NOT NULL DEFAULT 'GROUND' CHECK (source IN ('GROUND','ONBOARD')),
  t_onboard timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  details jsonb NOT NULL DEFAULT '{}');                 -- epoch, from_seq, lost, lost_by_time, sha256, first_seq …

CREATE TABLE limits_versions (sha256 text PRIMARY KEY, content text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE alarms (                                    -- current state; one open alarm per (vehicle, param)
  alarm_id uuid PRIMARY KEY, vehicle text NOT NULL, param text NOT NULL,
  state text NOT NULL CHECK (state IN ('UNACK','ACKED','RTN_UNACK','CLEARED')),
  severity text NOT NULL CHECK (severity IN ('YELLOW','RED')), acked_sev text,
  trigger_check text NOT NULL, opened_at timestamptz NOT NULL, closed_at timestamptz);
CREATE UNIQUE INDEX alarms_one_open ON alarms (vehicle, param) WHERE state <> 'CLEARED';

CREATE TABLE alarm_events (                              -- history; never pruned (D-DB-09)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key text NOT NULL UNIQUE,                              -- vehicle/param/epoch/trigger_seq/transition, or ACK request_id
  alarm_id uuid REFERENCES alarms, vehicle text NOT NULL, param text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('RAISE','ESCALATE','DEESCALATE','RTN','ACK','CLEAR','LEVEL')),
  check_kind text, level text, severity text,
  trigger_seq bigint, trigger_t_onboard timestamptz,     -- ALM-05 start stamp (R10)
  after_seq bigint,                                      -- on ACK: last seq the operator saw (R7 replay overlay)
  processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),   -- ALM-05 end stamp
  by_user text NOT NULL DEFAULT 'system', note text, alert boolean NOT NULL DEFAULT false,
  limits_sha256 text NOT NULL REFERENCES limits_versions);

CREATE TABLE commands (                                  -- current state; full history lives in audit_log
  cmd_id uuid PRIMARY KEY, vehicle text NOT NULL, name text NOT NULL, args jsonb NOT NULL,
  args_hash text NOT NULL, hazardous boolean NOT NULL, reason text, issued_by text NOT NULL,
  state text NOT NULL CHECK (state IN ('QUEUED','SENT','RECEIVED','VERIFIED','FAILED')),
  failure_reason text CHECK (failure_reason IN ('SEND_ERROR','REJECTED','NOT_RECEIVED','NOT_VERIFIED')),
  outcome text CHECK (outcome IN ('NOT_SENT','NOT_EXECUTED','UNKNOWN')),
  commands_sha256 text NOT NULL, queued_at timestamptz NOT NULL, sent_at timestamptz,
  received_at timestamptz, verified_at timestamptz, failed_at timestamptz, late_verified_at timestamptz);

CREATE TABLE session_report (session_id bigint PRIMARY KEY REFERENCES test_session,
  report jsonb NOT NULL, csv text NOT NULL, sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp());

GRANT SELECT, INSERT ON events, limits_versions, alarm_events, session_report TO gs_app;   -- history: append-only
GRANT SELECT, INSERT, UPDATE ON seq_state, alarms, commands TO gs_app;                     -- current-state rows
```

### 3.3 Parameter values (simulator **and** `limits.yaml` — one source, D-SIM-11)

Defaults: `hysteresis_pct: 2` (of span), `persistence: 3`. Noise is Gaussian σ per frame.

| Parameter | Unit | Nominal | σ | red_low | yellow_low | yellow_high | red_high | Extra | Faults / commands |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cabin_pressure_kpa` | kPa | 101.3 | 0.02 | 95 | 98 | 104 | 106 | ROC 0.15 kPa/s, `direction: falling`, window 1 s, YELLOW | `leak` −0.4/s · `isolate_valve` +0.5/s to nominal · `vent_cabin` −2/s |
| `cabin_temp_c` | °C | 22.0 | 0.05 | 15 | 18 | 27 | 30 | — | `overheat` +0.5/s |
| `bus_voltage_v` | V | 28.0 | 0.05 | 24 | 26 | 30 | 32 | — | — |
| `bus_current_a` | A | 5.0 | 0.05 | — | — | 8 | 10 | one-sided, `hysteresis_abs: 0.1` | — |
| `tank_pressure_bar` | bar | 20.0 | 0.02 | 15 | 17 | 23 | 25 | — | — |
| `rate_x_dps` · `rate_y_dps` · `rate_z_dps` | deg/s | 0.0 | 0.01 | −5 | −2 | 2 | 5 | `hysteresis_pct: 1` | — |

Checks on these numbers: σ 0.02 ≤ 0.027 kPa needed by the ROC limit (R8). Cabin-pressure deadband = 2 % × 11 = 0.22 kPa, larger than ±1 % of span noise (±0.11), so ALM-02's "noise around a limit raises 1 alarm" holds. ROC is falling-only so repressurisation after `isolate_valve` does not re-alarm (closes A-R8-8 for this parameter).

### 3.4 ATP-02 expected timeline (10 Hz, `leak` injected at t = 0 from 101.3 kPa)

| t (s) | Pressure (kPa) | Expected |
| --- | --- | --- |
| ≈ 1.3 | ≈ 100.8 | ROC check: alarm **RAISE YELLOW LOW** (visual only, no sound) |
| 8.25 + 0.3 | 98.0 | absolute yellow joins the same alarm (no new alarm) |
| 15.75 + 0.3 | 95.0 | **ESCALATE RED**, one beep (`alert: true`) |
| operator | ~93 | ACK with note → ACKED; send `isolate_valve` → QUEUED → SENT → RECEIVED → VERIFIED within ~0.3 s |
| +≈ 4.5 after isolate | > 95.22 | DEESCALATE YELLOW (deadband 0.22) |
| +≈ 10.5 after isolate | > 98.22 | RTN → **CLEARED** (by system) |

Playwright timeout for the whole flow: 60 s.

---

## 4. Deliberately not built

| Not built | Why | Where it is written down |
| --- | --- | --- |
| Deploying Yamcs or COSMOS | The exercise is the core logic; a real programme would start with a make-or-buy trade (answer to review Q15 in [R1](research/R1-prior-art-mission-control.md)) | README, design |
| CCSDS / PUS decoding | Spec §1 out of scope; plug point only | §3 above |
| Shelving, latching, suppression, limit sets, 5 severities | Not in spec; each adds UI and audit paths | README next steps |
| Roles, second-person approval for hazardous commands | Simple login is in scope; roles are not | README next steps, Q14 |
| Tamper-evident audit (hash chain, WORM) | Table owner/superuser can still bypass role + trigger; out of scope | README limitations, Q14 |
| Compression, continuous aggregates | Non-Apache licence, not needed at 0.45 GB/day; a 1 min rollup becomes necessary at 10 vehicles × 100 Hz (Q11) | README scaling notes |
| Kafka / NATS | Redis meets the load; switch triggers in [R3](research/R3-redis-streams.md) | design, Q7 |
| Dark theme, cloud / Kubernetes | Not asked; no credible evidence in 25 h | README next steps |

---

## 5. Effort, cut order and build order

### 5.1 Effort estimate (hours; fill **Actual** at each gate)

The spec's gates add up to ~27 h ("about 25"). This design estimates **≈ 30.5 h**, so about 4–6 h must come from the cut list below. Estimates are judgment, not measured; the Actual column is how we learn.

| Gate | Work | Est. | Actual |
| --- | --- | --- | --- |
| 2 – Design review | repo, compose, Makefile, migrations runner (1.5) · CI (0.5) · simulator v0, 2 vehicles nominal (1.0) · thin worker: validate, store, ack, `ui:events` (1.5) · API SSE + login + live table (1.0) · prove-early checks (0.5) | 6.0 | |
| 3 – day 2 | seq tracker + DLQ + poison (1.5) · ownership + takeover (1.5) · alarm engine: checks, persistence, hysteresis, ROC, life cycle (2.5) · limits reload (0.5) · simulator faults (0.5) | 6.5 | |
| 3 – day 3 | commanding: API, tracker, interlock, audit (2.0) · simulator command effects (0.5) · console: alarm list, ack, timeline, STALE/LOS, banners, audio (2.5) · sessions, report, sign-off (1.0) · Playwright ATP-02/04 (1.0) | 7.0 | |
| 4 – Test readiness | archiver (1.5) · replay engine + page (1.5) · charts: uPlot + downsample (1.0) · perf runs (1.0) · docs (1.0) | 6.0 | |
| 5 – Acceptance | fresh clone, all ATPs, ATP report, NCRs, demo recording, review answers | 3.0 | |
| **Total** | | **28.5 + 2.0 buffer = 30.5** | |

### 5.2 Cut order (cut from the top; each cut → one NCR in `docs/ncr/`)

| # | Cut | Saves | What remains / what fails | Prio hit |
| --- | --- | --- | --- | --- |
| 1 | D-ARC-06 versioned late objects (v1+) | 0.5 | late rows stay hot + alert; nothing lost | none |
| 2 | D-ING-12 steal path for a hung owner (v1+) | 0.5 | crash takeover still works; a SIGSTOPed owner blocks its vehicle | ING-08 partial (reclaim by own restart only) |
| 3 | GS-ALM-08 hot reload → reload on restart; hash still stored with every alarm | 0.5 | Q6 still answerable | **S** (1 of 2 allowed should-fails) |
| 4 | GS-ING-10 100 Hz run → run once, publish numbers, NCR if lag ≥ 1 s | 0.5 | ING-09 at 10 Hz unaffected | **S** (2 of 2) |
| 5 | D-ING-10 fair share → static vehicle-to-worker map + takeover when a worker dies | 0.5 | ING-06 still passes (2 workers share, order kept) | none |
| 6 | D-REP-03 live-ACK overlay in replay | 0.5 | replay differs from live only after ack-then-escalate; documented | DAT-06 partial |
| 7 | D-UI-16 replay page → start replay via API, console shows the stream | 0.5 | DAT-06 demo by API call | none |

**Never cut**: anything on a must-requirement's pass condition; D-ING-02/13 (frame loss); D-CMD-06 (hazardous commands); D-CMD-08 (audit). Cuts 3 and 4 use the whole should budget (≥ 5 of 7 must pass), so ALM-04, CMD-05, ENG-05, ENG-06 and ENG-07 must pass.

### 5.3 Build order (gates from spec §8)

| Gate | Build | Proves early (open checks from research) |
| --- | --- | --- |
| **2 – Design review** (thin slice) | repo, compose (Redis, TimescaleDB, MinIO), Makefile, migrations, CI with one passing test, demo users; sim (2 vehicles, D-SIM-01..04) → one worker (D-ING-01..03, 13, 22) → `telemetry` → `ui:events` → SSE → live table | MinIO image contents and bucket init · `compose up --wait` with one-shot services · SSE reconnect through compose · Docker VM clock sync · cost of `appendfsync always` |
| **3 – Implementation** day 2 | ingest depth (D-ING-04..25), alarm engine (D-ALM-*), limits reload, simulator faults (D-SIM-07/08/11) | ATP-04, ATP-05 pass |
| **3 – Implementation** day 3 | commanding (D-CMD-*, D-SIM-06/10), console (D-UI-*), sessions + report + sign-off (D-DB-06/07/10) | ATP-01, 02, 03, 06 pass |
| **4 – Test readiness** | archiver (D-ARC-*), replay (D-REP-*), perf runs (D-PERF-*), all §6 docs | ATP-07, ATP-08; every requirement has a test or inspection note ([requirements.md](requirements.md)) |
| **5 – Acceptance** | fresh clone, all ATPs, `atp-report.md`, NCRs (incl. cuts taken), demo recording, review answers | acceptance rule met or gaps documented |

---

## 6. Requirement → decision index (all 47)

| Req | Decisions |
| --- | --- |
| ING-01 | D-ING-01, D-SIM-05 |
| ING-02 | D-ING-05, 18, 19, 20 · D-SIM-04, 07 |
| ING-03 | D-ING-13, 14 · D-SIM-07 |
| ING-04 | D-ING-14, 15, 16 · D-DB-08 · D-SIM-07 |
| ING-05 | D-ING-13, 14, 15 · D-SIM-03, 07 |
| ING-06 | D-ING-01, 09, 10, 11 |
| ING-07 | D-ING-02, 03, 06, 07, 09, 13, 17, 22 · D-ENG-11 · D-ING-24 |
| ING-08 | D-ING-04, 05, 12 |
| ING-09 | D-ING-22, 23 · D-PERF-01, 05, 07 · D-SIM-01, 02 |
| ING-10 | D-PERF-05, 06 |
| ALM-01 | D-ALM-01 · D-SIM-11 |
| ALM-02 | D-ALM-02 · D-SIM-11 |
| ALM-03 | D-ALM-03, 04 · D-SIM-11 |
| ALM-04 | D-ALM-05 · D-SIM-08 · D-SIM-11 |
| ALM-05 | D-PERF-01, 04, 05 |
| ALM-06 | D-ALM-06, 07, 08 · D-ING-17 · D-SIM-10 |
| ALM-07 | D-ALM-10 |
| ALM-08 | D-ALM-12 · D-DB-09 · D-DB-11 |
| CMD-01 | D-CMD-01, 05 · D-SIM-06 · D-SIM-10 |
| CMD-02 | D-CMD-02, 03, 04, 05 · D-SIM-06, 07 · D-SIM-10 |
| CMD-03 | D-CMD-06, 09, 10 |
| CMD-04 | D-CMD-08 · D-ENG-08 · D-DB-10 |
| CMD-05 | D-CMD-07 · D-ENG-13 |
| UI-01 | D-UI-01, 12, 14 · D-PERF-02 |
| UI-02 | D-UI-04, 05, 06 · D-ING-21 · D-ALM-04 · D-SIM-07 · D-ING-25, D-UI-15 |
| UI-03 | D-UI-06, 07 |
| UI-04 | D-UI-11 · D-DB-02, 04 · D-ARC-05 |
| UI-05 | D-UI-08, 09 · D-ALM-06, 08, 09 |
| UI-06 | D-UI-10 · D-CMD-02, 08 |
| UI-07 | D-UI-01, 02, 03, 04 · D-UI-15 |
| UI-08 | D-UI-13 · D-SIM-02 · D-UI-16 |
| DAT-01 | D-DB-07 · D-UI-17 |
| DAT-02 | D-DB-06 · D-ING-16 · D-UI-17, D-DB-10 |
| DAT-03 | D-DB-01, 02, 05 |
| DAT-04 | D-ARC-01, 03, 06, 07, 09 |
| DAT-05 | D-ARC-02, 03, 04, 05 · D-ARC-10 |
| DAT-06 | D-REP-01..06 · D-ALM-11 · D-DB-09 · D-UI-16 |
| ENG-01 | D-ENG-02, 03, 09 · D-ENG-12, 13 |
| ENG-02 | D-ING-07, 08 · D-SIM-05 · D-ENG-03, 11 · D-ING-24 |
| ENG-03 | D-ENG-05 · D-SIM-09 |
| ENG-04 | D-ENG-06 |
| ENG-05 | D-ENG-07 · D-ARC-10, D-ING-25 |
| ENG-06 | D-DB-03 · D-ING-18, 19, 20 · D-CMD-09 · D-DB-11 |
| ENG-07 | D-ENG-10 |
| ENG-08 | §6 deliverables: README, this file, requirements trace, ATP report, guides (no research needed) |
| ENG-09 | D-ENG-04, 09 · D-ARC-08 · D-ENG-12 |
| ENG-10 | D-ENG-01 · D-UI-12 |
