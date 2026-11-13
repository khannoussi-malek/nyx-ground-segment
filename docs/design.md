# Nyx Ground Segment – What We Build

Design and decision register · draft for Gate 2 · 2026-11-13 · enhancement round 1 applied (§9)

This file is the single answer to "what do we build, and why that way". Design review 1 fixes are listed in §7. Enhancement round 1 (simplification review SR-01..09 and alternatives review ALT-A/B/C) is listed in §9. Gate 1 gap review (same day) added D-SIM-10/11, D-ING-24/25, D-DB-10/11, D-ARC-10, D-UI-15..17, D-ENG-12/13, §3.1–3.4 and §5.1–5.2. Every decision has an ID, the choice, the reason, the alternative it beat, the requirements it serves (spec §4, `GS-` prefix dropped) and the research note it comes from. Open choices and their defaults live in [assumptions.md](assumptions.md); the evidence lives in [research/](research/).

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

**One-paragraph story.** The simulator writes one JSON frame per tick into `tm:<vehicle>`. Two ingest workers share the vehicles; each vehicle has exactly one owner, enforced by a Postgres advisory lock, and the other worker waits on that lock to take over the moment the owner's session ends. The owner validates the frame, classifies its sequence number, stores it idempotently, runs the alarm engine, saves the engine state, commits, then publishes a UI event and acknowledges the Redis entry. Command tracking runs after that, in its own transaction, from stored evidence. The API streams UI events to the console over SSE and takes commands, acks and session actions over REST. The archiver turns each finished vehicle-hour into a verified Parquet object in MinIO and only then deletes the hot rows. Replay shows archived (or still hot) frames together with the alarm transitions recorded live.

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
| D-SIM-05 | **Buffer frames in order while Redis is down**: bounded deque of 6000 frames (5 min at 2 × 10 Hz), reconnect backoff capped at 1 s; any overflow is counted, logged and shows downstream as a GAP | From the producer's side the "stop Redis 10 s" outage is ~15 s (restart + AOF load + reconnect); 200 frames had no margin (RV-10) | Dropping frames; a 200-frame buffer | ENG-02, ING-01 | R3 (A-R3-1), RV-10 |
| D-SIM-06 | Reads `tc:<vehicle>`, executes **≤ 1 command per tick**, writes `{cmd_id, accepted, code, reason, t_onboard}` to **`tcack:<vehicle>`**, then reports `last_cmd_id` in the next frame | RECEIVED needs a source (the §3 frame has none); one-per-tick keeps `last_cmd_id` from skipping a command | Inferring receipt from the simulator's XACK (proves a process read it, not that the vehicle accepted it) | CMD-01, 02 | [R2](research/R2-standards-and-command-safety.md) (A-R2-1 `default`, A-R2-6) |
| D-SIM-07 | Faults: `leak` (−0.4 kPa/s), `overheat`, `dropout N` (**on-board counter keeps running**), `freeze` (**seq and time advance, values repeat**), `seq_jump`, `duplicate` (**byte-identical resend**), `malformed`, **`pause N`** (frames keep flowing; commands neither acknowledged nor executed for N s, then processed), **`hold_exec N`** (commands acknowledged at once, executed N s later), **`step {param, value, duration_s}`** (forces a value; alarm-latency runs) | Each fault's semantics decides which detector must catch it. ATP-03: `pause 6` → FAILED NOT_RECEIVED, then LATE_VERIFICATION; `hold_exec 16` → RECEIVED → FAILED NOT_VERIFIED, then LATE_VERIFICATION, so both CMD-02 cases are reachable (RV-02). `step` gives 120 alarms in a 10 min run (RV-19) | One `pause` for both cases (only ever produces NOT_RECEIVED) | ING-02..05, UI-02, CMD-02, ALM-05 | R5, R9, RV-02, RV-19 |
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
| D-ING-04 | **Poison handling, one rule on every read path** (new, own-PEL, takeover claim, 30 s claim): before processing an entry, read its `times_delivered` (`XPENDING`); **> 3 → DLQ + `POISON` event**. A failing batch is rolled back and re-run one frame per transaction to isolate the bad frame; that frame is retried 3× in-process, then DLQ'd; the rest commit | A frame that crashes the worker every time reaches the DLQ after 3 deliveries instead of crashing both workers in turn (RV-06); one bad statement can't roll back good frames (RV-27) | Stalling the vehicle ~90 s; different thresholds per note (≥ 3 vs > 3) | ING-08 | R3, R4, RV-06, RV-27 |
| D-ING-05 | DLQ write and XACK in **one `MULTI`**; DLQ record `{reason, detail, raw, stream, entry_id, deliveries, first_seen, dlq_at}`, reason ∈ `json_invalid · schema · vehicle_mismatch · t_onboard_range · poison` | A frame is never both acked-and-lost or DLQ'd twice; the reason is inspectable | Separate calls (crash window) | ING-02, 08 | R3, R11 (R13) |
| D-ING-06 | Trim with `XADD … ACKED MAXLEN ~ 360000` (~10 h at 10 Hz, ~40 MB per vehicle) | `ACKED` never deletes un-acked entries; default trimming silently does | Default trimming (frame loss); no trimming (RAM) | ING-07 | R3 (A-R3-3/5) |
| D-ING-07 | Redis config: AOF `appendfsync always` + RDB + `noeviction`, image pinned `redis:8.10.3` | PEL and entries survived graceful restart and `kill -9` (R3); measured cost at 2 × 100 Hz paced: XADD p50 1.2 ms, p99 4.7 ms (S1-S4), well inside ING-09; `everysec` loses up to 1 s. README caveat: whether fsync inside the Docker Desktop VM reaches the Mac's disk on host power loss is unverified | `everysec` (breaks "0 frames lost") | ENG-02, ING-07 | R3, [S1](research/S1-gate2-spikes.md) S4 |
| D-ING-08 | Worker loop: `BLOCK 1000`, catch `ConnectionError`/`TimeoutError`, back off 0.5 s doubling to 5 s, set `backlog=True` | redis-py gives up after ~5 s; `BLOCK` ≥ socket timeout fails after 62 s | Default client settings (worker crashes on the Redis stop test) | ENG-02 | R3 |
| D-ING-09 | **Vehicle ownership = Postgres session advisory lock** `pg_advisory_lock(hashtext('tm:'‖vehicle))` (blocking, D-ING-10) on the same connection that writes that vehicle's data | Lock and writes share one session, so a dead owner can't commit after takeover (lease + fence in one primitive); survives Redis restart | Redis lease (lost on Redis restart, needs a DB check anyway); stateless CAS workers (can't tell lost from in-flight frames); split stateful stage (needs single owner anyway) | ING-06, 07 | R4 |
| D-ING-10 | **Blocking standby lock + Postgres-only hand-back** (ALT-A): `ingest-1` has home vehicle NYX-TA1, `ingest-2` NYX-TA2 (config). Each worker runs one task per (worker, vehicle), each on its own connection (`application_name` = consumer name), calling the **blocking** `pg_advisory_lock`; the home vehicle is requested at once, the others after a 2 s start delay, so the home worker wins at startup. The waiting task's call returns the instant the owner's session ends. A task holding a **non-home** vehicle releases it (after acking its current batch) when `pg_locks` shows that vehicle's home worker waiting on the key (`granted = false`), checked on the 5 s reclaim tick (D-ING-26); it then waits on the lock again | 2 workers share load and order is kept; takeover happens when Postgres frees the lock, not on a poll; no ownership decision depends on Redis; a peer, not the restart policy, covers `docker kill` (A-R11-6) | Polled try-lock + "free > 2 s" timer + Redis-heartbeat release (four timers, two stores, A-F2); fixed vehicle per worker with no failover (SR-01: a `docker kill`ed worker is not restarted, so its vehicle stops until a manual `up`, failing OPS-4 / ATP-05) | ING-06, 07 | R4, ALT-A, A-E1-1/2 |
| D-ING-11 | **Takeover protocol** (runs when the blocking lock call returns, at start and on takeover): load `seq_state`, open alarms and the **`engine_state` snapshot** (D-ALM-11) → `XAUTOCLAIM … 0` until cursor `0-0` → process in ID order → then new entries | Pending before new keeps per-vehicle order; no false GAP after a kill; alarm state is exact with no warm-up | Reading new entries first; warming checks up from the last K frames (ALT-C) | ING-06, 07, ALM-02/03 | R4, R8, ALT-C |
| D-ING-12 | **Steal path (v1)**: every 5 s each worker checks, for each vehicle it does not own, the lock holder's consumer in `XINFO CONSUMERS tm:<v> ingest`; **`idle` > 30 s** (time since its last attempted read; a healthy owner reads at least once per second, D-ING-08) → look up the holder's pid in `pg_locks`, `pg_terminate_backend`, and the waiting task takes over (D-ING-11). The stolen worker gets a DB error when it wakes and exits (D-ING-24). All workers share one DB role | A hung-but-connected owner (SIGSTOP, deadlock) would otherwise block its vehicle and fail ING-08 (RV-03); the `idle` trigger also catches an owner that hangs between XACK and its next read with an empty PEL, which "an entry pending > 30 s" misses (A-F1) | v1+ / cut list (it hits a must); "an entry pending > 30 s" trigger (never fires with an empty PEL) | ING-08 | R4 (A-R4-4), RV-03, ALT-A, A-E1-1 |
| D-ING-13 | **Storage key `(vehicle, t_onboard, seq)`**, `INSERT … ON CONFLICT DO NOTHING RETURNING` | Timescale requires the time column in unique keys; CCSDS recommends counter + time; a wrong classification can raise a wrong event but never drop or double a frame | `(vehicle, seq)` (drops frames after every restart); `(vehicle, epoch, seq)` (a misdetected epoch loses data) | ING-03, 05, 07 | R5 (A-X-1) |
| D-ING-14 | **Pure sequence tracker** `step(state, seq, t) → (state, events)` with wrap-safe compare (mod 2³²) and the 7-rule table: IN_ORDER, GAP, SEQ_JUMP, DUP_OR_LATE, SEQ_RESET (seq ≤ 50 and time forward), SEQ_ANOMALY | Uses `t_onboard` to split duplicate from reset and dropout from jump; deterministic, so replay and reclaim give the same events | RTP fixed misorder window (calls a quick restart a duplicate) | ING-03, 04, 05 | R5 |
| D-ING-15 | Epoch = `t_onboard` of the first frame after a reset; kept in `seq_state` (updated **in the same transaction** as the frames) and on event rows | Derived from data only → any worker or replay computes the same value | Worker-local boot counter (non-deterministic) | ING-04, 05 | R5 |
| D-ING-16 | `SEQ_JUMP` is its own event, not a GAP; a `SEQ_RESET` shows in the session report as a gap of unknown size | Counter jump ≠ lost frames; the report stays honest (review Q5) | Counting a jump as lost frames | ING-04, DAT-02 | R5 (A-R5-4/6, `lead`) |
| D-ING-17 | Event keys: GAP/SEQ_JUMP `(vehicle, epoch, from_seq)`, SEQ_RESET `(vehicle, epoch)`; only frames classed **new** reach the alarm engine | A redelivered frame raises nothing twice (OPS-4 "no alarm duplicated") | Relying on the frame insert alone | ING-07, ALM-06 | R5 |
| D-ING-18 | **Strict pydantic frame model**: `strict=True`, `allow_inf_nan=False`, uint32 bounds, UTC-only aware time, UUID, `vehicle` = stream suffix, `t_onboard` within [now − 7 d, now + 5 min]; parameter fields generated from the registry with `create_model` | Rejects NaN, `"5"`, naive times, wrong vehicle; adding a parameter needs no code | Lax parsing (hides simulator bugs) | ING-02, ENG-06 | R11 (R13) |
| D-ING-19 | Unknown frame field → kept in `model_extra`, **counted, warned once, not stored** | Simulator ahead of config must not DLQ every frame | `extra="forbid"` | ING-02, ENG-06 | R6, R11 (A-R6-3) |
| D-ING-20 | Registered parameter missing from a frame → **stored as NULL, counted, warned** | Adding a parameter to config before the simulator sends it must not quarantine every frame | DLQ (breaks the add-a-parameter guide) | ING-02, ENG-06 | R11 (A-R11-3 `default`) |
| D-ING-21 | **FROZEN detector**: **all** analog parameters (enums excluded) bit-identical for ≥ 2 s of `t_onboard` → `acq=FROZEN`, shown like STALE; no per-parameter flag (SR-08). Cut candidate #4 | `freeze` keeps frames arriving, so arrival-based staleness never fires; time-based so 100 Hz doesn't trip it after 0.2 s (RV-24); requiring every value to match makes a quantized sensor harmless, so no opt-out is needed | 20 frames (0.2 s at 100 Hz); a `frozen_check` registry flag per parameter | UI-02, OPS-3 | R9, RV-24, SR-08 |
| D-ING-22 | **One frame transaction per `XREADGROUP` batch**: psycopg 3 `executemany` (pipeline mode) + `ON CONFLICT`. It contains frames, `seq_state`, sequence events, alarm transitions, applied ACKs (D-ALM-14) and the `engine_state` upsert (D-ALM-11), **nothing else**; then **COMMIT → `XADD ui:events` → XACK**. Command tracking runs afterwards in its own transaction (D-CMD-05) | Publishing before XACK means a crash before the publish leaves the frame pending; the takeover then resyncs the console (D-UI-18); keeping the tracker out means a tracker fault can never roll back frames or send them to the DLQ (B-F1) | COPY + staging table; asyncpg; publishing after XACK (lost alarm messages, RV-05); command updates inside the frame transaction (ALT-B) | ING-07, 09, UI-05 | R6, R5, RV-05, ALT-B |
| D-ING-23 | `t_received DEFAULT clock_timestamp()` (not `now()`) | `now()` is transaction start → batched inserts understate latency | `now()` | ING-09 | R10 (A-R10-1) |
| D-ING-24 | **Postgres error = crash-only** (SR-01, accepted part): on any Postgres error the worker rolls back, does **not** XACK and exits non-zero (`sys.exit(1)`); its sessions end, so its locks free and the peer's waiting task takes those vehicles at once (D-ING-10); `restart: unless-stopped` brings the worker back through the normal startup path, and it gets its home vehicle back by hand-back. Redis errors keep the in-process reconnect (D-ING-08) | Frames wait safely in Redis (`ACKED` trimming keeps them); never write on a connection that doesn't hold the lock (R4 rule 4); one recovery path (startup) instead of an in-process drop-all-ownership/re-lock path | In-process re-lock and takeover after a DB error; retrying on a fresh connection without re-locking (breaks the fence); crash-only for Redis errors too (Docker's restart backoff could stretch a 15 s outage, SR "considered") | ENG-02, ING-07 | R4, SR-01, A-E1-3 |
| D-ING-25 | Each worker sets **`hb:veh:<v>` = 1 with `EX 3`** every second for each vehicle it owns | Lets the API decide per vehicle whether a healthy owner exists (RV-16); with crash-only (D-ING-24) a live worker always has a working DB, so a `db_ok` field adds nothing (SR-02) | A bare "alive" flag; `hb:ingest:<consumer>` = `{owned, db_ok}` | UI-02, ENG-05 | `lead`, RV-16, SR-02 |
| D-ING-26 | **30 s reclaim loop (v1)**: every 5 s the owner runs `XAUTOCLAIM tm:<v> ingest <me> 30000` on each stream it owns, collecting entries left pending under any other consumer name (then the D-ING-04 delivery-count rule); the test creates the idle entry with a `ghost` consumer that reads without acking | Gives ING-08's "entry idle > 30 s is reclaimed" an actor in v1 (RV-03); the same 5 s tick runs the hand-back check (D-ING-10) and the steal check (D-ING-12) | Reclaim only at takeover (min-idle 0, never 30 s) | ING-08 | R3, RV-03 |

### 2.3 Alarm engine (library used by ingest and by the replay determinism test)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ALM-01 | **On a limit = the more severe band** (`v ≤ low`, `v ≥ high`); load-time check `red_low < yellow_low < yellow_high < red_high` | EPICS and COSMOS precedent; gives ALM-01's "exactly on" test a defined answer | Yamcs/XTCE default (on-limit = nominal) | ALM-01 | R8, R1 |
| D-ALM-02 | **Hysteresis = % of span** (`red_high − red_low`) → absolute deadband `d`; only delays moves to a less severe band; `hysteresis_abs` for one-sided limits. Cabin pressure uses **10 %** (d = 1.1 kPa) so ALM-02 passes under **both** readings of "±1 %": of span (±0.11 kPa) and of the limit value (±0.98 kPa). "1 alarm" = one RAISE transition | % of limit value is 0 at a limit of 0; the customer hasn't confirmed which reading the spec means, so the test must not depend on it (RV-12) | % of limit value; a 2 % deadband that passes only one reading | ALM-02 | R8, RV-12 |
| D-ALM-03 | **Persistence**: ring of the last N raw levels; escalate when all N ≥ L, de-escalate when all N ≤ L; red needs no own N | On-delay + off-delay over the deadband (series design in the literature); a 1-frame spike never alarms | Persistence on raise only | ALM-03 | R8 (A-R8-3) |
| D-ALM-04 | GAP, SEQ_RESET and **data-STALE** (`t_onboard` gap > 2 s between consecutive frames) reset counters and the ROC window but **never clear an alarm**. The browser STALE clock (D-UI-05) is display-only | A value can be RED and STALE; the engine's STALE comes from data only, so live and replay compute the same thing (RV-07) | Clearing on loss of data; using wall-clock STALE in the engine | ALM-03, UI-02, DAT-06 | R8, RV-07 |
| D-ALM-05 | **Rate of change = least-squares slope** (`statistics.linear_regression`) over the last `window_s` = 1 s of `t_onboard`; needs ≥ 80 % of expected samples; optional `direction: rising·falling·both` (default both) | ~1.4× less noisy than end-point difference, 13× less than frame-to-frame; alarms the 0.4 kPa/s leak ~7.5 s before yellow; vehicle time, not arrival time | Frame-to-frame difference; arrival-time slope | ALM-04 | R8 (A-R8-8 `default`) |
| D-ALM-06 | **One alarm per (vehicle, param)**; severity = max of its checks; `trigger_check` recorded | A leak gives one escalating alarm, not three | One alarm per check | ALM-06, UI-05 | R8 |
| D-ALM-07 | Life cycle **UNACK → ACKED → CLEARED**, plus **RTN_UNACK** ("cleared – not acked"); ACK requires a note; who/when/why stored on every transition | Matches the spec's ACTIVE→ACKED→CLEARED and ISA-18.2; RTN before ack must stay visible | Auto-clear on return to normal | ALM-06 | R8 (A-R8-7), R1 |
| D-ALM-08 | **Acked yellow that escalates to red becomes UNACK and alerts once**; red→yellow→red after an ack does not alert again (`acked_sev` kept) | Answers review Q2 ("I acked and it got worse"); Yamcs keeps the ack here, we deliberately don't | Keeping the ack (Yamcs) | ALM-06, UI-05 | R8 (A-R8-5 `default`) |
| D-ALM-09 | Only **RED** alerts audibly (`alert` column on the transition row); yellow is visual | OPS-2 "an alarm sounds once" | Sounding on every transition | UI-05 | R8 (A-R8-6) |
| D-ALM-10 | **Pure checks**: `Check` Protocol with `kind`, pydantic `Config`, a frozen JSON-serialisable `State` dataclass, `initial()`, `step(state, sample, current) → (state, level)`; `@register` into `CHECKS`; package auto-imports every module; one generic JSON (de)serializer for check states keyed by `kind` (ALT-C). Property test: serialize at every batch boundary, reload, continue → same transitions as the uninterrupted run | "New class + registration only" = add one file, no worker edit; serialisable state makes the takeover snapshot exact (D-ALM-11) | Plugin framework; if/else in the worker | ALM-07, ING-07 | R8, R1, ALT-C |
| D-ALM-11 | **Engine-state snapshot** (ALT-C): in every frame transaction (D-ING-22) the owner upserts `engine_state(vehicle, last_seq, epoch, limits_sha256, schema_version, state)` (§3.2); takeover and restart load it (D-ING-11); no row → `initial()`. If `schema_version` differs (state shape changed by a code change): open alarms from `alarms`, checks from NOMINAL, plus an `ENGINE_STATE_RESET` event (known approximation, A-E1-5). No `LEVEL` events, no K-frame warm-up | Hysteresis has memory; the snapshot is committed with the frames it reflects, so the rebuilt state equals live by construction. The LEVEL + K-frame warm-up was measured to diverge from live in 0.48 % of takeovers (K too small, C-F1); re-feeding from NOMINAL in 28–29 % | LEVEL events + warm-up of the last K frames (C-F1); re-feeding frames from NOMINAL | ING-07, ALM-02/03, UI-05 | R8, ALT-C |
| D-ALM-12 | **Limits reload**: hash `limits.yaml` (sha256 of the bytes, a few KB) every 1 s, validate with pydantic (`extra="forbid"`), store in `limits_versions`, emit `LIMITS_APPLIED(first_seq, sha)`; an invalid file keeps the old set + `LIMITS_REJECTED` banner. **At worker start an invalid file falls back to the newest valid `limits_versions` row** (+ LIMITS_REJECTED); refuse to start only if no valid version exists | New limit active < 5 s; every alarm row and `LIMITS_APPLIED` say which limits applied from which frame (Q6), which the replay determinism test reuses (D-REP-05); one typo plus one crash must not stop ingest for both vehicles (RV-17) | inotify dependency; a `stat` pre-check (SR-09); refusing to start on any invalid file | ALM-08 | R8, RV-17, SR-09 |
| D-ALM-13 | Out of scope: shelving, latching, suppression, re-alarm timers, limit sets, 5 severity levels | Not in the spec; each adds UI + audit paths | — | — | R8 (A-R8-9), R1 |
| D-ALM-14 | **ACK path; the owner worker is the only writer of alarm state.** The API validates the ACK (alarm open, note not empty), inserts `alarm_ack_request(request_id PK, alarm_id, vehicle, user, note, requested_at, applied_at)`, writes audit `ALARM_ACK`, returns **202**. The owner reads pending requests for its vehicles every loop pass (≤ 1 s), applies them in its batch transaction (ACK row in `alarm_events` with `after_seq` = **last seq the owner had processed**), sets `applied_at`, and the ACKED transition goes out over SSE | ACK then RTN must give CLEARED, not RTN_UNACK; a second writer would be overwritten by the engine's in-memory state; `after_seq` matches R7, so replay shows the recorded ACK at the frame where it took effect and the determinism test (D-REP-05) applies it there (RV-01) | API updating `alarms` directly | ALM-06, UI-05, DAT-06 | R7, R8, RV-01 |

### 2.4 Commanding (API + vehicle owner)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-CMD-01 | States **QUEUED → SENT → RECEIVED → VERIFIED**, or **FAILED**; RECEIVED from `tcack` (accepted), VERIFIED when `last_cmd_id == cmd_id` (a telemetry-based verifier, like XTCE Complete / Yamcs algorithm verifier) | Spec names kept; maps cleanly to PUS service 1 acceptance/completion | Extra start/progress states (no source in our frame) | CMD-01 | R2, R1 |
| D-CMD-02 | FAILED carries `failure_reason` (SEND_ERROR · REJECTED · NOT_RECEIVED · NOT_VERIFIED); **`outcome`** (NOT_SENT · NOT_EXECUTED · **UNKNOWN**) is **computed, not stored**, by one helper used by the API and the UI (SEND_ERROR→NOT_SENT, REJECTED→NOT_EXECUTED, NOT_RECEIVED/NOT_VERIFIED→UNKNOWN); UI text says which | Answers review Q3 "did the vehicle do it?"; PUS: silence is not failure; never blind-resend a hazardous command after UNKNOWN; `outcome` is a pure function of `failure_reason`, so a column could only disagree with it (SR-07) | A bare FAILED; a stored `outcome` column | CMD-02, UI-06 | R2, SR-07 |
| D-CMD-03 | **Both windows from SENT**: not received in 5 s, not verified in 15 s; per-command overrides in `commands.yaml` | Simple to explain; worst case is 15 s, not 20 s | Yamcs chaining from previous stage | CMD-02 | R2, R1 (A-R1-1) |
| D-CMD-04 | **FAILED is terminal**; a later echo writes `LATE_VERIFICATION` + `late_verified_at` | History is append-only; investigators need to know the operator saw FAILED; ATP-03's 16 s pause must expect FAILED **then** LATE_VERIFICATION | Overwriting to VERIFIED (Yamcs) | CMD-02, 04 | R2 (A-R1-2) |
| D-CMD-05 | **Command tracking runs in the vehicle's owner, after the frame COMMIT, in its own transaction** (ALT-B), every loop pass (≤ 1 s resolution because `BLOCK 1000` returns even when the vehicle is silent). It decides from **stored evidence only**: the `commands` row, `tcack:<v>` entries (applied as guarded updates, then XACKed) and `telemetry.last_cmd_id` (one indexed query for open `cmd_id`s since `min(sent_at) − 1 s`; the first frame per id gives VERIFIED or LATE_VERIFICATION). The decision is a pure `decide(cmd, ack, first_verify_frame, now, caught_up)`; timeouts apply **only when the owner has caught up** on its backlog (last read was not a full batch and the takeover drain is done, D-ING-08/11), and evidence inside the window wins over a timeout in the same pass (A-E1-4). Timers keep running during LOS. A tracker error is logged, counted in `/healthz` as `cmd_tracker_errors`, and never touches frame processing | The owner already sees frames in order and inherits failover with no extra service; durable evidence means a crash between the frame and tracker commits loses nothing (B-F3) and a late or new owner reaches the same answer; caught-up timeouts stop a false FAILED after a takeover (B-F2); a separate transaction stops a tracker fault from sending every frame to the DLQ (B-F1); `decide()` makes every CMD-02 branch a table test | Tracking inside the frame transaction with in-memory command state; a tracker in the API; a separate `gs-cmdtrack` service (sixth service, no failover); the owner also sending commands | CMD-01, 02 | `lead` (A-R2-7), ALT-B |
| D-CMD-06 | **Hazardous two-step enforced by the API**: first POST rejected with `HAZARDOUS_CONFIRMATION_REQUIRED`, hazard text and `args_hash` = sha256 of canonical `{vehicle, name, args}`; re-POST must carry matching `confirm_args_hash` + `reason` (≥ 10 chars) | curl, scripts and UI all hit the same rule; you can't confirm A and send B | Client-only dialog (fails "cannot be sent without both"); server-stored tokens (more state, no extra protection) | CMD-03 | R2, R1 |
| D-CMD-07 | **Double-command interlock**: `SET cmdlock:{vehicle}:{key} {cmd_id}\|{user} NX PX 2000` as the last check; `key` = `name` for **hazardous** commands, `name:args_hash` otherwise; user excluded | Two operators can't each send `vent_cabin` with different durations within 2 s (RV-26); ordinary commands with different args still pass; a double-click is stopped | Args-hash key for every command | CMD-05 | R2, RV-26 (A-R2-4) |
| D-CMD-08 | **Append-only `audit_log`**: actions `LOGIN · CMD_REQUESTED · CMD_CONFIRM_REQUIRED · CMD_REJECTED · CMD_STATE · CMD_LATE_VERIFICATION · ALARM_ACK · LIMITS_RELOADED · SESSION_START · SESSION_STOP`; app role `INSERT, SELECT` only + triggers that raise on UPDATE/DELETE/TRUNCATE; **completeness** tested (N commands + M acks → exactly the expected rows) | Two independent layers; CMD-04 needs both immutability and "every command and ack" (RV-14, RV-20) | Hash chain / WORM (out of scope) | CMD-04, UI-06 | R2, R6, RV-14, RV-20 |
| D-CMD-09 | `commands.yaml` validated with pydantic at load; per command `description`, `hazardous`, `hazard` text, typed `args`; no file hash stored (SR-07: no requirement versions `commands.yaml`) | Typed args catch bad input at the API | XTCE (XML schema with ~100 element types); `commands_sha256` on every command row | CMD-03, ENG-06 | R2, SR-07 |
| D-CMD-10 | **No roles**: any logged-in user may command and run sessions; identity always from the session cookie, never the request body | A simple login is in scope, roles are not; second-person approval is the documented upgrade | Conductor/controller roles; second-user approval (COSMOS Enterprise) | CMD-03, 04 | R11 (A-R11-5 `default`), R2 |
| D-CMD-11 | **Command row concurrency**: every state change is `UPDATE … WHERE state IN (<earlier states>)` (monotonic: a late SENT can't overwrite RECEIVED); the API sets SENT + `sent_at` **before** `XADD tc:`, and on XADD error sets FAILED SEND_ERROR; the owner's single 5 s not-received timeout covers QUEUED too: `state IN ('QUEUED','SENT') AND coalesce(sent_at, queued_at) < now − 5 s` → FAILED NOT_RECEIVED (SR-07); `tcack:<v>` is read with the `ingest` group and XACKed after the tracker transaction commits (D-CMD-05). No in-memory command state, so nothing to load on takeover | API and worker both write command rows; without guards an executed command can end FAILED (RV-09); one timeout rule instead of a separate QUEUED sweep. If the API dies between QUEUED and SENT the command ends NOT_RECEIVED / UNKNOWN, which never falsely claims "not sent" | Unguarded updates; a separate "QUEUED > 5 s → SEND_ERROR" sweep | CMD-01, 02 | RV-09, SR-07, ALT-B |

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
| D-DB-08 | One `events` table for ground events (GAP, SEQ_*, POISON, LOS/AOS, FROZEN, LATE_VERIFICATION, LIMITS_*, ENGINE_STATE_RESET) with a deterministic `key`, `kind` and `details jsonb`; no `severity` / `source` columns (SR-09: PUS vocabulary with no reader and no requirement) | One place for every keyed event; a future CCSDS decoder adds its own severity mapping when it exists | Separate tables per event type; PUS service 5 `severity`/`source` columns "for later" | ING-04, CMD-04 | R2, SR-09 |
| D-DB-09 | **`events` and `alarm_events` are never pruned** (SEQ_RESET, LIMITS_APPLIED, GAP, alarm transitions and ACK rows …), nor is `limits_versions` | Replay shows these recorded rows as what happened live (D-REP-01) and the determinism test re-derives them (D-REP-05); they are small | Pruning with telemetry | DAT-06, ALM-08 | R7 (A-R7-5), RV-14, ALT-C |
| D-DB-10 | Report **frozen** when the vehicle watermark `seq_state.last_t ≥ ended_at` (or after 30 s, marked "incomplete: n of m frames"); until then `GET …/report` returns 409 `REPORT_PENDING` with frame count vs expected. Stored in `session_report(session_id, report, csv)`. No sign-off in the tool (SR-02, §4) | Freezing at stop could miss in-flight frames and show a false gap, Q5's exact worry (RV-15); hot rows are deleted after 25 h, so a report read at the test review a week later must come from the frozen copy | Freezing at stop; recomputing on every view; a sign-off endpoint with `REPORT_SIGNED` + sha256 (no pass condition, SR-02) | DAT-02 | R6 (A-R6-4), RV-15, SR-02 |
| D-DB-11 | **Parameter registry = the `parameters:` section of `limits.yaml`** (name, unit, optional checks). `gs-migrate` (owner role) adds missing columns; workers refuse to start if a registered parameter has no column; a hot reload that adds an unknown parameter is rejected with `LIMITS_REJECTED: run make migrate` | The app role can't `ALTER TABLE` (role design of D-CMD-08); one file is the one place to add a parameter | A separate `parameters.yaml`; `ALTER` from the app role | ENG-06, ALM-08 | R6, R8, `lead` |

### 2.6 Archive and replay

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ARC-01 | One **Parquet** object per vehicle-hour, pyarrow, zstd, one row group, schema from the hot table; no custom footer metadata (nothing reads it, SR-05) | ~0.91 MB per vehicle-hour, ~10× smaller than hot rows | snappy (1.13 MB); CSV; footer `limits_sha256`/seq range/schema version | DAT-04 | [R7](research/R7-archive-replay.md), SR-05 |
| D-ARC-02 | **Hash the uploaded bytes**: sha256 computed in memory, sent as `x-amz-checksum-sha256`, checked by the full re-read (D-ARC-03), and recorded in `archive_object` only after that re-read matches (D-ARC-04) | Parquet bytes are only reproducible with the same pyarrow version; recording the hash before upload adds nothing, because every step before verification is re-runnable from hot rows (SR-05) | Byte-reproducible files as the integrity basis; recording the hash in Postgres before upload | DAT-05 | R7, SR-05 |
| D-ARC-03 | **Single PUT** with `x-amz-checksum-sha256` (MinIO rejects a mismatch); then **full re-read** against the recorded hash; then sidecar `<HH>.parquet.sha256` in `sha256sum` format | Three independent checks before any delete; a killed upload leaves no visible object (shown in the R7 spike) | Multipart (objects are below the 5 MB part minimum; leaves parts behind); ETag as hash (not MD5 for multipart) | DAT-04, 05 | R7 (A-R7-1) |
| D-ARC-04 | **One `archive_object` row per (vehicle, hour), inserted only after verification** (`vehicle, hour, sha256, rows, verified_at, hot_deleted_at`, §3.2); an eligible hour with no row is (re)built from hot rows, which are deleted last. "Verified row = archived" | Kill mid-upload → no row exists → the next run rebuilds and re-uploads (about 1 MB), nothing lost, no manual cleanup (SR-05) | Stateless cron; a WRITTEN → UPLOADED → VERIFIED → HOT_DELETED state machine (states nothing needs) | DAT-05 | R7, SR-05 |
| D-ARC-05 | **Eligible = `hour_end + 10 min ≤ now`** (wall clock; 10 min > the 5 min producer buffer); **hot delete only when hour_end ≤ now − `HOT_RETENTION_H`** (default 25 h), per (vehicle, hour), in one transaction, commits only if deleted rows = `archive_object.rows` | The 24 h chart is always hot; one eligibility rule; the row-count check catches late frames | `add_retention_policy` / TTL (deletes unverified data); a vehicle-watermark rule plus a "+30 min if silent" fallback (SR-05) | DAT-05, UI-04 | R7, R6 (A-R7-4), SR-05 |
| D-ARC-06 | **v1: late frames** into an archived hour stay hot and raise an alert (the delete check refuses). **v1+:** write an immutable `<HH>.v<N>.parquet`; readers take the latest verified version | Nothing is lost in v1; versioning is extra code for a rare case | Overwriting `<HH>.parquet` (data/sidecar disagree for a window); separate `late/` objects every reader must merge | DAT-04, 05 | R7 (A-R7-2), `lead` |
| D-ARC-07 | Scheduler: plain **asyncio loop every 5 min** in `gs-archiver` | One loop does not justify a dependency | APScheduler (4.x still alpha) | DAT-04 | R7 |
| D-ARC-08 | S3 client: **minio-py** | Ships type hints (mypy strict) | boto3 (needs stubs) | ENG-09 | R7 |
| D-ARC-09 | MinIO image **`bitnamilegacy/minio:2025.5.24@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c`** (amd64 + arm64), data at `/bitnami/minio/data`, credentials `MINIO_ROOT_USER/PASSWORD`; healthcheck `curl -fsS http://localhost:9000/minio/health/live`; `minio-init` runs from the **same image** (`mc alias set … && mc mb --ignore-existing …`, `MC_CONFIG_DIR=/tmp/mc`), gated on `minio: service_healthy`; listed in README limitations | Upstream stopped publishing images; this one is built from official source. Spike S1: image contains `mc` and `curl`, rejects a wrong `x-amz-checksum-sha256` (`XAmzContentChecksumMismatch`), data survives container re-creation on the named volume. One image for server and init removes `pgsty/mc` and the retry loop | `pgsty/minio` (third party, unverified); building from source | DAT-04, ENG-01 | R7, R11, [S1](research/S1-gate2-spikes.md) S1 (A-X-3) |
| D-ARC-10 | Archiver `/healthz` reports `oldest_unarchived_hour` and Redis/DB reachability; older than 72 h (config) → `degraded`; no console banner (SR-02, §4); MinIO unreachable → retry with backoff, never delete | Hot data grows while MinIO is down and DAT-05 forbids deleting it, so someone has to find out | Silent growth until the disk fills; an ARCHIVE_BACKLOG console banner (no pass condition) | DAT-05, ENG-05 | R6 (A-R6-5), SR-02 |
| D-REP-01 | **Replay shows the recorded truth** (ALT-C): it merges the frames of the window (verified Parquet objects, hash checked, or the hot table for hours not yet archived) with the **`alarm_events` and `events` rows recorded live** for that window, ordered by `(t_onboard, seq)`. Alarm state at `t_from` = the latest recorded transition per alarm before `t_from` (one query); frames are annotated with each parameter's current recorded severity. **Nothing is recomputed** | "Same alarms as live" is then true by construction, also after a code change or a live defect; recomputing with today's code could show alarms that never happened (C-F2) and needs a seed loader, ACK overlay and limits switching (C-F3) | Recomputing with the same `step()` from LEVEL seeds (C-F2/C-F3); DuckDB over S3 (downloads an extension at runtime, skips our hash check); shadow tables | DAT-06 | R7, R8, R1, ALT-C |
| D-REP-02 | Pacing by `t_onboard` deltas at 1× or 10× | Replays vehicle time; a live backlog burst is not replayed as a burst | `t_received` pacing | DAT-06 | R7 (A-R7-8) |
| D-REP-03 | **Show the recorded ACK transitions**: ACK rows (who, when, note) appear at their recorded position like any other transition (read-only); replay never sounds and never accepts acks | ACK rows are recorded transitions, so ack-dependent behaviour (re-alarm on escalation) shows exactly as it happened live, with no overlay logic (ALT-C) | Overlaying live ACKs at `after_seq` into a recomputation; no ACKs at all (R9's first proposal) | DAT-06, ALM-06 | R7, R9 (A-X-4), ALT-C |
| D-REP-04 | Replay runs as an **async generator inside the API process** feeding `GET /api/replays/{id}/events` (same message shapes, `replay: true`) into a separate store in the UI; purple **REPLAY** banner that can't be dismissed; never writes live tables | Replay task and its SSE handler live in one process, so a Redis stream adds nothing (RV-23a) | `ui:replay:<id>` Redis stream; WebSocket; writing to tables | DAT-06 | R9, R7, RV-23 |
| D-REP-05 | **Two tests** (ALT-C). (a) `replay(window) == recorded rows` for the same window, from Parquet and from hot. (b) **Determinism**: run `step()` from the initial state over a recorded test run, with ACKs applied at `after_seq` and limits switched at `LIMITS_APPLIED`; it must reproduce the recorded transition keys. Recomputation is a test, not the replay mechanism | (a) proves the replay path; (b) still proves "the logic gives the same alarms" for a board that reads DAT-06 that way | One live-vs-recomputed-replay key comparison as the product path; visual comparison | DAT-06 | R7, ALT-C, A-E1-6 |
| D-REP-06 | STALE is display-only in replay; its keyed equivalent (GAP) is a recorded `events` row, so it is in both D-REP-05 tests | The STALE clock lives in the browser | Making STALE a stored event | DAT-06 | R7 (A-R7-7, `lead`) |

### 2.7 Operator console (React + TypeScript)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-UI-01 | **SSE** from FastAPI fed by Redis stream **`ui:events`** (worker `XADD` after commit, `MAXLEN ~ 3000`; API reads with plain `XREAD`, no group) | One-way is enough (commands go over REST); every (re)connect gets a snapshot (D-UI-02); a stream loses no alarm/command event across restarts; no consumer group, so `ACKED` trimming of `tm:*` is unaffected | WebSocket (hand-written reconnect/resume/heartbeat); Redis pub/sub (at-most-once); reading `tm:*` (UI would duplicate alarm logic) | UI-01, 07 | R9 (A-R9-1) |
| D-UI-02 | **Every (re)connect gets a snapshot, then deltas** (SR-03); no `Last-Event-ID` / `?since=` resume. One snapshot function serves connect, reconnect, D-UI-18 resync, the API's own Redis-reader reconnect and per-client queue overflow (bounded queue, overflow → close + reconnect); on SIGTERM an override of `uvicorn.Server.handle_exit` sets an `asyncio.Event` that every SSE loop checks, plus `timeout_graceful_shutdown=1`, so `restart api` doesn't stall | Spike S6 (headless Chromium, 9 runs, 0 gaps/duplicates): `restart api` → first new event 1.14–1.25 s with the shutdown hook, 2.3 s with the graceful timeout alone, **11.1 s** with plain uvicorn and a 10 s grace (fails UI-07); about 1.0 s of it is `retry: 1000`; one code path for five cases. Missed `tm` deltas need no replay: the snapshot carries last values, open alarms (an unacked RTN stays open as RTN_UNACK) and command rows with every state time | Resume from `Last-Event-ID` with snapshot fallback (two mechanisms); always full page reload | UI-07 | R9, RV-11, SR-03, S1-S6 |
| D-UI-03 | **Client watchdog = the only reconnect path**: on EventSource `error`, close it, show the banner, open a new EventSource after 1 s, receive a snapshot | A proxy 502 during restart closes EventSource for good; one path is easier to test than built-in retry plus resume | Trusting built-in reconnect; resume with `?since=<lastId>` | UI-07 | R9, SR-03 |
| D-UI-04 | **Heartbeat every 1 s**; banners: **NO LIVE CONNECTION** from the **first EventSource `error`** or 3 s without any message, until the next message; **LOSS OF SIGNAL** = no frame for 2 s while a healthy owner exists; UI-07 measured from the `restart` command to data flowing again, **with the compose `stop_grace_period` set explicitly** (S6: the engine default here is 1 s and would measure the wrong thing); the EventSource `error` fired in every S6 run, so the banner always appears | A 0.9 s restart must still show the banner ("banner while disconnected", RV-11) | 3 s silence only | UI-02, 07 | R9, RV-11 |
| D-UI-05 | **Freshness clock in the browser** (`performance.now()` seeded with server `age_ms`, 100 ms tick, STALE at 2.0 s), with **`age_ms = api_now − t_onboard`** (SR-08) | Frames that waited in a buffer or in Redis during an outage arrive with a fresh `t_received` but an old `t_onboard`; they must show STALE, not current (OPS-3, RV-08). With one clock, `t_received ≥ t_onboard`, so a `t_received` term can never win; a `bench_offset` knob only matters for out-of-scope hardware (README next steps, A-E1-8) | Receipt-time age only; `max(api_now − t_received, api_now − t_onboard − bench_offset)` | UI-02 | R9, RV-08, SR-08 |
| D-UI-06 | Each value has **two fields**: acquisition (`OK · STALE · FROZEN · NO_DATA`) × limit (`NOMINAL · YELLOW · RED` + LOW/HIGH); a STALE row keeps its last limit as an outline "was RED LOW" badge, value in italic + clock glyph | A value can be RED and STALE; COSMOS's STALE-as-colour loses the last limit during LOS | One merged enum | UI-02, 03 | R1, R9 (A-R1-3, A-R9-5) |
| D-UI-07 | **Grey when nominal, colour only for abnormal**, every state also as text + glyph; palette verified WCAG AA (e.g. red `#B3141B` with white 6.92:1; yellow with `#7A5C00` border 5.25:1); REPLAY = purple, used for nothing else; light theme only | ISA-101 high-performance HMI; no state by colour alone | Green-for-nominal (COSMOS) | UI-03 | R9, R1 (A-R9-9) |
| D-UI-08 | Audio armed by the login click (`AudioContext.resume()`); **one beep per `alert:true` transition key**; visible SOUND OFF badge if audio isn't running | Browser autoplay policy needs a gesture; "sounds once" | Beeping per frame | UI-05 | R9 (A-R9-7) |
| D-UI-09 | Alarm list sticky until acked, filters by vehicle/severity/state, ACK dialog with required note | Spec UI-05 + COSMOS Limits Monitor pattern | — | UI-05 | R1, R9 |
| D-UI-10 | Command timeline with all four state times in UTC, read from the **`commands` row** (`queued_at … verified_at`, `failed_at`, `late_verified_at`) | The times are already there; the audit log stays the history (RV-23b) | Reading the audit log | UI-06 | R2, RV-23 |
| D-UI-11 | Charts with **uPlot** (23.5 KB gz, canvas, native min/max bands), windows 2 min · 1 h · 24 h, server downsampling | Fast and small | ECharts (~198 KB), Recharts/visx (SVG, slow when streaming) | UI-04 | R9 |
| D-UI-12 | **No UI kit**; plain store with `useSyncExternalStore` (per-row subscriptions); native `<dialog>`; hooks shaped like Open MCT (`useTelemetry`, `useStaleness`) | 10 Hz updates without re-rendering the table; fewer dependencies | Redux; Open MCT itself (Vue, contradicts ENG-10) | UI-01, ENG-10 | R9, R1 |
| D-UI-13 | Times formatted with `toISOString()` (always `Z`) | UTC everywhere without a date library | moment/dayjs | UI-08 | R9 |
| D-UI-14 | Latency probe: each rendered value exposes `data-seq`/`data-t-onboard`; `performance.now()` stamped at SSE receipt and in the second `requestAnimationFrame` after commit (page half of UI-01, D-PERF-02); Playwright collects with `page.evaluate` | Every frame gets a sample, including ones superseded before paint | Playwright polling the DOM | UI-01 | R10, R9 |
| D-UI-15 | **GROUND PROCESSING DOWN** per vehicle when `hb:veh:<v>` is missing (D-ING-25); replaces LOS for that vehicle. Cut candidate #1 | A dead or blocked worker must not look like loss of signal (Q1, OPS-4, RV-16) | One global `ingest_ok`; a FRAMES REJECTED banner from per-vehicle DLQ counting (SR-02: no pass condition; frames rejected for `t_onboard_range` show as LOS and the DLQ says why, A-E1-9) | UI-02, 07 | `lead`, RV-16, SR-02 |
| D-UI-16 | **Replay page**: vehicle, from/to as `<input type="datetime-local">` labelled and interpreted as **UTC**, speed 1× / 10×, start/stop, progress (current replay `t_onboard`); purple frame + banner; separate store. Cut candidate #2 (replay then started via API) | OPS-6 needs a way to pick "14:00–14:20 last Tuesday" | Date-picker library | DAT-06, UI-08 | `lead` |
| D-UI-17 | **Session panel**: start (vehicle + name), stop, report view (or "pending"), CSV download | The conductor's whole job in OPS-1; sign-off happens outside the tool on the downloaded report (SR-02, A-E1-10) | A sign-off button with note | DAT-01, 02 | `lead`, SR-02 |
| D-UI-18 | **Console resync**: a worker publishes `resync{vehicle}` into `ui:events` after start and after every takeover; the API sends a fresh `snapshot` (open alarms with their latest transition key and `alert` flag, recent commands, last values with `t_onboard`) to all clients on `resync` and whenever its own Redis reader reconnects, using the same snapshot function as every client (re)connect (D-UI-02); the client deduplicates transition keys and beeps once for any RED `alert` key it hasn't beeped yet | A crash between COMMIT and `XADD ui:events` can't leave a RED alarm, or its beep, missing from an open console (RV-05) | Transactional outbox table + publisher (more code for the same guarantee) | UI-05, UI-07, ING-07 | RV-05 |

### 2.8 Delivery and engineering

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-ENG-01 | **One Python package `gs`** (`src/gs/{common,simulator,ingest,archiver,api}`), entry points `gs-sim · gs-ingest · gs-archiver · gs-api · gs-migrate` | All services share dependencies | uv workspace (N× config for nothing) | ENG-10 | R11 |
| D-ENG-02 | **One Dockerfile**, two stages (Node builds `ui/dist`, Python 3.14-slim + uv runs everything); FastAPI serves the built UI | One image, one origin, no CORS, no nginx | nginx container | ENG-01 | R11 |
| D-ENG-03 | **docker compose**: `restart: unless-stopped`, healthchecks, `depends_on` conditions, `stop_grace_period: 5s` on every app service; one-shots `migrate` and `minio-init` **each have a long-running dependent using `service_completed_successfully`** and are never named in `--wait`; `make up` creates `.env` (random secrets, Redis ACL file), then `docker compose up -d --build --wait` with no service list | Fresh clone → `make up` → demo works first try. Spike S2: full `up -d --wait` exits 0 in 6.1 s; a one-shot without a dependent makes `--wait` fail (`exited (0)`), and `--wait migrate` returns while it still runs. Explicit grace period because this Docker Desktop defaults to 1 s, which would hide shutdown stalls (S6) | Manual setup steps; relying on the engine's default stop timeout | ENG-01, 02 | R11, [S1](research/S1-gate2-spikes.md) S2/S6 |
| D-ENG-04 | Config from **env only** into a frozen dataclass (fails fast); `.env.example` committed; no hard-coded hosts | Spec ENG-09; ~10 lines | pydantic-settings | ENG-09 | R11 |
| D-ENG-05 | **Tests at 3 levels**: unit (pure functions: tracker, checks, state machines), integration against the **same compose** Redis + Postgres, E2E with **Playwright in TypeScript** | One set of image pins for local, CI and E2E | testcontainers / GitHub `services:` (duplicate pins) | ENG-03 | R11 |
| D-ENG-06 | **CI** (one workflow): `lint` (ruff check + format, `mypy --strict`, `tsc --noEmit`) ∥ `test` (`docker compose up -d --wait redis db && docker compose run --rm migrate`, then unit + integration) → `e2e` (`make up`, Playwright); same workflow green on the `v*` tag = release evidence | Spec ENG-04 + §6 "green run on the release tag"; the `up --wait … migrate` form fails or returns early (S2) | Separate workflows; `up -d --wait redis db migrate` | ENG-04 | R11, [S1](research/S1-gate2-spikes.md) S2 |
| D-ENG-07 | `/healthz` on every service (small asyncio HTTP server in workers); **stdlib logging with a JSON formatter**, every line with `vehicle` and `seq` where relevant | Spec ENG-05 with no new dependency | structlog | ENG-05 | R11 |
| D-ENG-08 | **Login**: fixed users in `users.yaml` with stdlib scrypt hashes; Starlette signed session cookie (`SameSite=strict`, 12 h); CSRF = required header `X-GS-Client: console` + JSON-only writes | "A simple login is enough" (spec §1), done safely | JWT; SSO (out of scope) | CMD-04 | R11 |
| D-ENG-09 | Versions pinned as in [R11's pin table](research/R11-stack-and-delivery.md) (Python 3.14, React 19, Vite 8, **TypeScript 6.0.3**, FastAPI 0.162, pydantic 2.14, redis-py 8.1, psycopg 3.3, Playwright 1.65) | Reproducible builds; TS 7 breaks typescript-eslint | Floating versions | ENG-01, 09 | R11 |
| D-ENG-10 | **Conventional commits**, small commits from the first day (research is the first commits) | Spec ENG-07 | One giant first commit | ENG-07 | — |
| D-ENG-11 | Kill-a-worker tests: `docker compose kill ingest-1` proves **takeover** by the other worker's waiting lock (the killed worker is not restarted); `kill -9 1` inside the container proves **self-restart** and the hand-back of its home vehicle (D-ING-10) | `docker kill` does not trigger the restart policy; a crash inside the container does. This is why a fixed vehicle per worker without failover was rejected (SR-01, §9) | Assuming both behave the same | ING-07, ENG-02 | R11 (A-R11-6) |
| D-ENG-12 | **Schema = `migrations/*.sql`** (roles, grants, tables, hypertable, audit triggers) mounted into the TimescaleDB image's `/docker-entrypoint-initdb.d`, which runs them once on a fresh volume (SR-04). `gs-migrate` (one-shot, as `gs_owner`) only adds registry columns (D-DB-11). A schema change during development = `make reset` (drop the volume) | A fresh clone always starts from an empty volume, which is exactly what initdb handles; no runner code to write or test | A migrations runner with `schema_migrations` + sha256 check (~30 lines plus tests, only needed for in-place upgrades, §4); Alembic; `CREATE IF NOT EXISTS` scattered across services | ENG-01, 09 | `lead`, SR-04, A-E1-7 |
| D-ENG-13 | **Demo users**: `make up` copies `users.example.yaml` → `users.yaml` if missing (`conductor`, `fc1`, `fc2`), generates one password into `.env` and prints it once | CMD-05 needs two operators at once; ENG-01 "demo works first try" | Manual user setup | ENG-01, CMD-05 | `lead` |
| D-ENG-14 | **Definition of done: nothing is merged without a test.** Every new function, endpoint, state machine, migration, simulator fault/command and UI behaviour ships with its test in the same commit, at the lowest level that can prove it: pure logic → unit; anything touching Redis/Postgres/MinIO → integration against the compose services; operator flows → Playwright. Bug fixes start with a failing test that reproduces the bug. Test infrastructure (simulator, perf scripts, `gs-migrate`) is tested too, because the ATPs trust it | The owner's rule for this project; untested code can't be accepted under §5 anyway, and tests written later get skipped when time runs out | "Tests at the end" (Gate 4) | ENG-03, all | owner rule |
| D-ENG-15 | **CI coverage gate**: `pytest --cov=gs --cov-fail-under=80` over unit + integration; Vitest coverage ≥ 80 % for `ui/src` logic (stores, formatters, palette, freshness clock); every E2E spec mapped to an ATP in [requirements.md](requirements.md). Coverage is a floor, not the goal: each requirement row still names the test that proves its pass condition | Makes "untested code" a red build instead of a review comment | Coverage as a report only | ENG-03, ENG-04 | owner rule |
| D-ENG-16 | **Test controls**: config `HOT_RETENTION_H` in the archiver; `gs-sim backfill --vehicle --from --to` writes past-hour frames through the normal stream path (within the 7-day validity window), and past hours are eligible at once (D-ARC-05); `CRASH_AT=<point>` (exit) and `STOP_AT=<point>` (SIGSTOP itself, e.g. `after_xack`) hooks in worker and archiver, active only when `GS_TEST_HOOKS=1`. No injectable clock, no `ARCHIVE_GRACE_S` (SR-05) | Archive/delete tests, ATP-07 and the "three days later" replay must run in CI and in a same-day acceptance; crash and hang points must be deterministic (RV-13, ALT-A) | Waiting real hours; random kills only; an injectable archiver clock | DAT-04, 05, 06, ING-07, 08 | RV-13, SR-05, ALT-A |
| D-ENG-17 | **Redis ACL users** from the ACL file in [S1](research/S1-gate2-spikes.md) S3, written by `make up` from `.env` (sha256 password hashes, file gitignored), default user off, per-key command selectors: `api` (write `tc:NYX-*`, `cmdlock:*`; read `ui:events`, `hb:*`), `sim` (read `tc:NYX-*`; write `tm:NYX-*`, `tcack:NYX-*`), `ingest` (read/ack/claim `tm:NYX-*`, `tcack:NYX-*`; write `tm:dlq`, `ui:events`, `hb:*`), `health` (`PING`, `+client\|setinfo`), `admin` (tests and ops only). Healthcheck `redis-cli --no-auth-warning --user health --pass "$$REDIS_HEALTH_PW" ping \| grep -q PONG`. Keys scoped to `tm:NYX-*` so the spec's `tm:dlq` name stays and `sim` can't write it. The simulator rejects `tc` entries without `cmd_id` and `issued_by`; `commands.yaml` bounds every arg (`vent_cabin.duration_s` ≤ 10). Residual risk (Redis admin can still write; Lua `EVAL` untested) in README | Command safety must not live only in the API (RV-18). S3 verified all four permission checks; `redis-cli` exits 0 even on a wrong password, hence `grep -q PONG` | No auth; `tm:*` patterns (would let `sim` write the DLQ); renaming the DLQ (spec names `tm:dlq`) | CMD-03, 04 | RV-18, [S1](research/S1-gate2-spikes.md) S3 |
| D-ENG-18 | **One time formatter** `gs.common.time.iso_z()` (`YYYY-MM-DDTHH:MM:SS.ffffffZ`) for API JSON, CSV, report and logs; Postgres timestamps are never serialized raw; tests assert `Z` in CSV and API output | Postgres text output has no `T`/`Z`; UI-08 covers every output, not just the UI (RV-20) | Per-module formatting | UI-08 | RV-20 |
| D-ENG-19 | **Evidence logs**: `docs/bug-log.md` (date · failing test · what it caught · fix commit), appended whenever a test catches a real bug; `docs/ai-usage.md` (what AI did: research agents with source links, independent design review, code drafts; how each was checked: sources, spikes with raw numbers, tests, CI, reading every diff) | Review-board Q16 and Q17 need facts collected during the build, not memory afterwards | Reconstructing them at Gate 5 | ENG-08 | review Q16/Q17 |

### 2.9 Measurement (method A requirements)

| ID | We build | Why | Instead of | Reqs | Src |
| --- | --- | --- | --- | --- | --- |
| D-PERF-01 | All stamps except UI-01 in **one clock** (Docker VM kernel): `t_onboard`, Redis entry ID ms, `t_received`, `raised_at` (`clock_timestamp()`) | Containers share the kernel clock → no offset error; the entry ID splits onboard→Redis from Redis→stored | Comparing across clock domains | ING-09, ALM-05, ING-10 | R10 |
| D-PERF-02 | **UI-01 in two single-clock halves** (SR-06): the API stamps `srv_ms = now − t_onboard` on each `tm` SSE message (VM clock, same as the simulator); the page adds `paint − receipt` from `performance.now()` (browser clock, D-UI-14); their sum per frame is frame-to-screen. Localhost socket transit (milliseconds against a 1000 ms budget) is left out and stated in the ATP report | Only metric crossing VM → macOS clock; each half is measured on one clock, so no offset correction is needed | A min-RTT offset ping with a before/after validity rule; ignoring the offset | UI-01 | R10, SR-06 |
| D-PERF-03 | **Raw samples to CSV** in `docs/perf/`, nearest-rank percentiles (stdlib), first 10 s excluded and reported | Spec §5 asks for raw numbers | HdrHistogram (not needed for ≤ 60 k samples) | all A-method reqs | R10 (A-R10-7) |
| D-PERF-04 | **ALM-05 on ≥ 60 alarms** (report 20 and 60): during run N the simulator `step`s `cabin_temp_c` to red for 1 s every 5 s (120 alarms in 10 min); latency from the N-th (triggering) frame; the alarm event stores `trigger_seq`, `trigger_t_onboard` | 20 samples can't bound a p95 (n ≥ 59); a leak cycle takes ~30 s, too slow for 60 alarms (RV-19) | 20 only; leak cycles | ALM-05 | R10, RV-19 |
| D-PERF-05 | **Run N** = 2 × 10 Hz, 10 min (pass/fail for ING-09, ALM-05, UI-01, DAT-03); **run P** = 2 × 100 Hz, 5 min (ING-10, others informative) | Pass/fail at the specified rate | Judging everything at 100 Hz; an extra run P′ with `appendfsync everysec` (SR-06) | ING-09, 10, ALM-05, UI-01 | R10 (A-R10-6), SR-06 |
| D-PERF-06 | ING-10 sampler at 1 Hz: `XINFO GROUPS` lag + pending and time lag (`clock_timestamp() − max(t_onboard)`); pass = max time lag < 1 s | Spec's "lag" read as time | Entry count only | ING-10 | R10 (A-R10-5) |
| D-PERF-07 | Takeover-window samples (state load + pending drain after the waiting lock returns; up to 30 s for a stolen hung owner) reported **separately**, never silently dropped | Honest p95 | Excluding them quietly | ING-09, ALM-05 | R4 (A-R4-3) |
| D-PERF-08 | Report: machine spec, image digests, load average before/after, raw CSV, `make perf` to reproduce | Spec §5 "on what machine, raw numbers" | Numbers without context | all A-method reqs | R10 |
| D-PERF-09 | **Scaling analysis** `docs/perf/scaling.md`: from run P totals (worker CPU % at 200 frames/s, Redis MB per vehicle-hour, DB insert time from the existing CSV; no per-stage instrumentation, SR-06), extrapolated to 10 vehicles × 100 Hz (1000 frames/s), naming the first bottleneck and its fix (engine per vehicle is single-threaded → split by parameter group; UI coalesces to 10 Hz renders; 1 min rollups at ~22 GB/day) | Review-board Q11 needs numbers, not opinions | "It scales" | ING-10 | R6, R10 |

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
| ★ `cmdlock:{v}:{key}` | api | `cmd_id\|user`, `PX 2000`; key = `name` (hazardous) or `name:args_hash` | CMD-05 interlock |

**SSE** `GET /api/events` (live), `GET /api/replays/{id}/events` (replay, in-process); events `hello · snapshot · tm · alarm · cmd · link · hb` (shapes in [R9](research/R9-operator-console.md)).

**Redis keys (other)**: `hb:veh:<vehicle>` (ingest → api, `1`, `EX 3`, D-ING-25).

**Postgres tables**: `telemetry` (hypertable) · `seq_state` · `engine_state` · `events` · `alarms` + `alarm_events` + `alarm_ack_request` · `limits_versions` · `commands` · `audit_log` · `test_session` · `session_report` · `archive_object`. DDL drafts: R6 (telemetry, audit), R11 (test_session), R5 (seq_state), §3.2 (the rest; `archive_object` there replaces R7's state-machine draft).

**Config files**: `limits.yaml` (schema in [R8](research/R8-alarm-logic.md)) · `commands.yaml` (schema in [R2](research/R2-standards-and-command-safety.md)) · `users.yaml` (scrypt hashes) · `.env`.

**Object store**: `archive/<vehicle>/<YYYY>/<MM>/<DD>/<HH>.parquet` + `<HH>.parquet.sha256`; v1+: `<HH>.v<N>.parquet`.

**Decoder plug point** (review Q13): one plain function `decode(raw: bytes) -> Frame` that raises `DecodeError(reason)` → DLQ; the signature is the pluggable layer, no registry (SR-09); a CCSDS decoder would map APID → vehicle, 14-bit count → `seq` (wrap at 2¹⁴), PUS secondary-header time → `t_onboard` ([R2](research/R2-standards-and-command-safety.md)).


### 3.1 HTTP API (`gs-api`, one origin with the UI)

All writes need the session cookie and header `X-GS-Client: console`, take JSON, and take the user from the session. Errors are `{"error": "<CODE>", "message": "<text for the operator>"}`.

| Method · path | Body / query | Success | Errors | Reqs |
| --- | --- | --- | --- | --- |
| `POST /api/login` · `POST /api/logout` · `GET /api/me` | `{user, password}` | 204 / `{user}` | 401 | CMD-04 |
| `GET /api/events` (SSE) | — (every connect starts with a `snapshot`, D-UI-02) | `hello, snapshot, tm, alarm, cmd, link, hb`; `tm` carries `srv_ms` (D-PERF-02) | — | UI-01, 02, 07 |
| `GET /api/vehicles` · `GET /api/limits` | — | vehicles + params · current limits + sha256 | — | ALM-08 |
| `GET /api/telemetry/{vehicle}/{param}` | `?from&to` (UTC `Z`) | `[{t, lo, hi, mean, n}]` | 400 unknown param, window > 24 h hot | UI-04, DAT-03 |
| `GET /api/commands/catalog` | — | `commands.yaml` view | — | CMD-03 |
| `POST /api/commands` | `{vehicle, name, args, confirm_args_hash?, reason?}` | 202 `{cmd_id}` | 409 `HAZARDOUS_CONFIRMATION_REQUIRED` `{hazard, args_hash}` · 409 `CONFIRMATION_MISMATCH` · 409 `DUPLICATE_COMMAND` `{by, seconds_ago, cmd_id}` · 422 `INVALID_ARGS` · 503 `BROKER_DOWN` | CMD-01..05 |
| `GET /api/commands` | `?vehicle&since` | commands + state timeline + computed `outcome` (D-CMD-02) | — | UI-06 |
| `GET /api/alarms` | `?vehicle&severity&state` | alarms | — | UI-05 |
| `POST /api/alarms/{alarm_id}/ack` | `{request_id, note}` | **202**, applied by the owner ≤ 1 s, ACKED pushed over SSE (idempotent on `request_id`) | 409 already cleared · 422 empty note | ALM-06 |
| `POST /api/sessions` · `POST /api/sessions/{id}/stop` | `{vehicle, name}` · — | 201 · 200 | 409 `SESSION_ACTIVE` · 409 already stopped | DAT-01 |
| `GET /api/sessions` · `GET /api/sessions/{id}/report` · `…/report.csv` | — | list · JSON · CSV | 409 session still running · 409 `REPORT_PENDING {frames, expected}` | DAT-02 |
| `POST /api/replays` · `DELETE /api/replays/{id}` · `GET /api/replays/{id}/events` (SSE) | `{vehicle, from, to, speed: 1·10}` | 201 `{replay_id}` | 404 no data in window · 409 archive object failed hash check | DAT-06 |
| `GET /healthz` (every service) | — | `{status: ok·degraded, checks}` | 503 when down | ENG-05 |
| Simulator `POST /sim/fault` · `DELETE /sim/fault` (port 8081, compose-internal, bound to 127.0.0.1 for tests) | `{vehicle, fault, params}` | 204 | 422 | ATPs |

### 3.2 Remaining table drafts (others: R6 `telemetry`, `audit_log`; R11 `test_session`)

```sql
CREATE TABLE seq_state (vehicle text PRIMARY KEY, epoch timestamptz NOT NULL, last_seq bigint NOT NULL,
  last_t timestamptz NOT NULL, period_s double precision NOT NULL);                  -- R5, same txn as frames

CREATE TABLE engine_state (                              -- D-ALM-11: upserted in every frame transaction
  vehicle text PRIMARY KEY, last_seq bigint NOT NULL, epoch timestamptz NOT NULL,
  limits_sha256 text NOT NULL REFERENCES limits_versions,
  schema_version int NOT NULL,                           -- mismatch → approximate rebuild + ENGINE_STATE_RESET
  state jsonb NOT NULL,                                  -- per-check frozen State, keyed by check kind (D-ALM-10)
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE events (            -- GAP SEQ_JUMP SEQ_RESET SEQ_ANOMALY POISON LOS AOS FROZEN LATE_VERIFICATION
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   --  LIMITS_APPLIED LIMITS_REJECTED ENGINE_STATE_RESET
  key text NOT NULL UNIQUE,                             -- deterministic, e.g. 'GAP/NYX-TA1/<epoch>/<from_seq>' (R5)
  vehicle text, kind text NOT NULL,                     -- no severity/source columns (D-DB-08, SR-09)
  t_onboard timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  details jsonb NOT NULL DEFAULT '{}');                 -- epoch, from_seq, lost, lost_by_time, sha256, first_seq …

CREATE TABLE limits_versions (sha256 text PRIMARY KEY, content text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE alarms (                                    -- current state; one open alarm per (vehicle, param)
  alarm_id uuid PRIMARY KEY, vehicle text NOT NULL, param text NOT NULL,
  state text NOT NULL CHECK (state IN ('UNACK','ACKED','RTN_UNACK','CLEARED')),
  severity text NOT NULL CHECK (severity IN ('YELLOW','RED')), acked_sev text,
  trigger_check text NOT NULL, limits_sha256 text NOT NULL,    -- of the latest transition (ALM-08)
  opened_at timestamptz NOT NULL, closed_at timestamptz);
CREATE UNIQUE INDEX alarms_one_open ON alarms (vehicle, param) WHERE state <> 'CLEARED';

CREATE TABLE alarm_events (                              -- history; never pruned (D-DB-09)
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key text NOT NULL UNIQUE,                              -- vehicle/param/epoch/trigger_seq/transition, or ACK request_id
  alarm_id uuid REFERENCES alarms, vehicle text NOT NULL, param text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('RAISE','ESCALATE','DEESCALATE','RTN','ACK','CLEAR')),   -- no LEVEL (D-ALM-11)
  check_kind text, level text, severity text,
  trigger_seq bigint, trigger_t_onboard timestamptz,     -- ALM-05 start stamp (R10)
  after_seq bigint,                                      -- on ACK: last seq the owner had processed when it applied the ack (R7, D-ALM-14)
  processed_at timestamptz NOT NULL DEFAULT clock_timestamp(),   -- ALM-05 end stamp
  by_user text NOT NULL DEFAULT 'system', note text, alert boolean NOT NULL DEFAULT false,
  limits_sha256 text NOT NULL REFERENCES limits_versions);

CREATE TABLE alarm_ack_request (                         -- D-ALM-14: API inserts, owner worker applies
  request_id uuid PRIMARY KEY, alarm_id uuid NOT NULL REFERENCES alarms, vehicle text NOT NULL,
  by_user text NOT NULL, note text NOT NULL CHECK (length(trim(note)) > 0),
  requested_at timestamptz NOT NULL DEFAULT clock_timestamp(), applied_at timestamptz);

CREATE TABLE commands (                                  -- current state; full history lives in audit_log
  cmd_id uuid PRIMARY KEY, vehicle text NOT NULL, name text NOT NULL, args jsonb NOT NULL,
  args_hash text NOT NULL, hazardous boolean NOT NULL, reason text, issued_by text NOT NULL,
  state text NOT NULL CHECK (state IN ('QUEUED','SENT','RECEIVED','VERIFIED','FAILED')),
  failure_reason text CHECK (failure_reason IN ('SEND_ERROR','REJECTED','NOT_RECEIVED','NOT_VERIFIED')),
  queued_at timestamptz NOT NULL, sent_at timestamptz,   -- outcome is computed from failure_reason (D-CMD-02)
  received_at timestamptz, verified_at timestamptz, failed_at timestamptz, late_verified_at timestamptz);

CREATE TABLE session_report (session_id bigint PRIMARY KEY REFERENCES test_session,
  report jsonb NOT NULL, csv text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE archive_object (                            -- D-ARC-04: inserted only after the re-read matches
  vehicle text NOT NULL, hour timestamptz NOT NULL, sha256 text NOT NULL, rows bigint NOT NULL,
  verified_at timestamptz NOT NULL, hot_deleted_at timestamptz,
  PRIMARY KEY (vehicle, hour));

-- Grant matrix (RV-14). gs_owner owns every table and runs the initdb SQL and drop_chunks; services connect as gs_app.
GRANT SELECT, INSERT          ON events, limits_versions, alarm_events, session_report, audit_log TO gs_app;  -- history
GRANT SELECT, INSERT, UPDATE  ON seq_state, engine_state, alarms, commands, alarm_ack_request, archive_object TO gs_app;  -- current state
GRANT SELECT, INSERT, DELETE  ON telemetry TO gs_app;   -- DELETE only in the verified retention transaction (D-ARC-05)
GRANT SELECT, INSERT, UPDATE (ended_at, ended_by) ON test_session TO gs_app;                                  -- no DELETE
```

### 3.3 Parameter values (simulator **and** `limits.yaml` — one source, D-SIM-11)

Defaults: `hysteresis_pct: 2` (of span), `persistence: 3`. Noise is Gaussian σ per frame.

| Parameter | Unit | Nominal | σ | red_low | yellow_low | yellow_high | red_high | Extra | Faults / commands |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `cabin_pressure_kpa` | kPa | 101.3 | 0.02 | 95 | 98 | 104 | 106 | `hysteresis_pct: 10` (d = 1.1 kPa, RV-12) · ROC 0.15 kPa/s, `direction: falling`, window 1 s, YELLOW | `leak` −0.4/s · `isolate_valve` +0.5/s to nominal · `vent_cabin` −2/s |
| `cabin_temp_c` | °C | 22.0 | 0.05 | 15 | 18 | 27 | 30 | — | `overheat` +0.5/s |
| `bus_voltage_v` | V | 28.0 | 0.05 | 24 | 26 | 30 | 32 | — | — |
| `bus_current_a` | A | 5.0 | 0.05 | — | — | 8 | 10 | one-sided, `hysteresis_abs: 0.2` (> p-p of ±1 % of the 8 A limit) | — |
| `tank_pressure_bar` | bar | 20.0 | 0.02 | 15 | 17 | 23 | 25 | — | — |
| `rate_x_dps` · `rate_y_dps` · `rate_z_dps` | deg/s | 0.0 | 0.01 | −5 | −2 | 2 | 5 | default 2 % (d = 0.2 > p-p of ±1 % of span noise) | — |

Checks on these numbers: σ 0.02 ≤ 0.027 kPa needed by the ROC limit (R8). ALM-02 on cabin pressure passes under **both** readings of "±1 %": noise of ±0.11 kPa (of span) or ±0.98 kPa (of the 98 kPa limit) stays inside the 1.1 kPa deadband, so the alarm is raised once and never clears on noise. ROC is falling-only, so repressurisation after `isolate_valve` doesn't re-alarm (closes A-R8-8 for this parameter).

### 3.4 ATP-02 expected timeline (10 Hz, `leak` injected at t = 0 from 101.3 kPa)

| t (s) | Pressure (kPa) | Expected |
| --- | --- | --- |
| ≈ 1.3 | ≈ 100.8 | ROC check: alarm **RAISE YELLOW LOW** (visual only, no sound) |
| 8.25 + 0.3 | 98.0 | absolute yellow joins the same alarm (no new alarm) |
| 15.75 + 0.3 | 95.0 | **ESCALATE RED**, one beep (`alert: true`) |
| operator | ~93 | ACK with note → 202, ACKED within ≤ 1 s (D-ALM-14); send `isolate_valve` → QUEUED → SENT → RECEIVED → VERIFIED within ~0.3 s |
| +≈ 6.5 after isolate | > 96.1 | DEESCALATE YELLOW (deadband 1.1) |
| +≈ 12.5 after isolate | > 99.1 | RTN → **CLEARED** (by system; the ACK was already applied by the owner) |

Playwright timeout for the whole flow: 60 s.


### 3.5 Bench-side interface requirements (what a real bench must also provide, RV-25)

The simulator keeps these promises and the ground side depends on them. Swapping in real hardware means the bench must keep them too; they go into the README as bench requirements.

| ID | Requirement on the bench | Ground decision that depends on it |
| --- | --- | --- |
| ICD-B-01 | Frames published in order; ≥ 5 min ordered buffering while the broker is unavailable | D-SIM-05, ING-01 "0 frames lost" |
| ICD-B-02 | `seq` +1 per frame; `t_onboard` UTC, µs precision, monotonic across reboots; bench clock synchronised with the ground clock | D-SIM-02/03, D-ING-14, D-UI-05 (age from `t_onboard` with no offset correction, SR-08) |
| ICD-B-03 | Command acceptance/rejection reported on `tcack:<vehicle>` with `cmd_id` | D-SIM-06, CMD-01 RECEIVED |
| ICD-B-04 | ≤ 1 command executed per frame; `last_cmd_id` in the next frame | D-SIM-06, CMD-01 VERIFIED |
| ICD-B-05 | Recommended, not required: a reset counter / `boot_id` field | A-R5-5 |

### 3.6 Where a frame or an alarm could be lost or doubled (review Q8)

| Point | What could go wrong | Why it can't | Proven by |
| --- | --- | --- | --- |
| Producer → Redis during an outage | frames dropped | ordered buffer ≥ 5 min; overflow counted and shown as GAP (D-SIM-05) | `test_redis_restart` |
| Redis restart | entries or pending list lost | AOF `always` + RDB (D-ING-07) | `test_redis_restart` |
| Stream trimming | un-acked entries deleted | `XADD … ACKED` (D-ING-06) | `test_trim_keeps_unacked` |
| Worker crash before COMMIT | frame lost | not acked → redelivered (D-ING-02/03) | `test_kill_owner_mid_stream[before_commit]` |
| Crash after COMMIT, before XACK | frame stored twice, alarm raised twice | storage key + DUP_OR_LATE + event keys (D-ING-13/17) | `[after_commit_before_ack]` |
| Crash after COMMIT, before the UI publish | console misses an alarm | resync on takeover and restart (D-UI-18) | `test_resync_after_crash` |
| Takeover or restart of the owner | alarm state differs from live → alarm duplicated or missing | `engine_state` committed in the same transaction as the frames it reflects, loaded on takeover (D-ALM-11, D-ING-11) | `test_snapshot_roundtrip_equals_uninterrupted`; alarm keys in `test_kill_owner_mid_stream` |
| Postgres error mid-batch | write on a connection that lost its lock | rollback, no XACK, worker exits; the restart re-acquires the lock (D-ING-24) | `test_db_error_exits_without_ack` |
| Two workers on one vehicle | reordering, double alarm | advisory lock on the writing session (D-ING-09); the standby waits on it (D-ING-10) | `test_two_workers_share_and_keep_order` + negative control, `test_hand_back_when_home_worker_waits` |
| Poison frame | crash loop across workers | delivery count > 3 → DLQ on every read path (D-ING-04) | `test_crash_every_time_frame_to_dlq` |
| Command tracker fault | every frame of the vehicle rolled back and sent to the DLQ | tracker runs after the frame COMMIT in its own transaction; errors are counted, never roll back frames (D-CMD-05, D-ING-22) | `test_tracker_error_never_touches_frames` |
| Hung owner | entries stuck, or new entries never delivered (empty PEL) | 30 s ghost claim (D-ING-26) + steal on consumer `idle` > 30 s (D-ING-12) | `test_reclaim_idle_30s`, `test_hung_owner_is_stolen[sigstop_in_read, sigstop_after_xack]` |
| Archive upload killed | hot data deleted without archive | `archive_object` row only after the checksum re-read matches; hot delete only after that (D-ARC-03..05) | `test_kill_mid_upload_then_resume` |
| Late frame after archive | never archived | delete refuses on row-count mismatch + alert (D-ARC-05/06) | `test_row_count_mismatch_rolls_back` |

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
| Report sign-off in the tool (`POST …/sign-off`, `REPORT_SIGNED`, report sha256) | No pass condition; the conductor signs the downloaded report in the review minutes (SR-02) | README limitations |
| ARCHIVE_BACKLOG and FRAMES REJECTED console banners | No pass condition; archiver `/healthz` reports `degraded`, the DLQ holds each rejected frame with its reason (SR-02) | README limitations |
| In-place schema migrations of a long-lived database | initdb runs the SQL once on a fresh volume; a schema change during development means `make reset` (SR-04) | README limitations, next steps |
| Bench clock offset (`bench_offset`) | The simulator shares the VM clock; real hardware with an offset would need it (SR-08) | README next steps, §3.5 |


### Not production-ready, in the order we would fix it (review Q14)

1. Identity: SSO, roles, second-person approval for hazardous commands (D-CMD-10).
2. Tamper evidence: hash-chained or WORM audit; separate Redis/Postgres admin from the app (D-CMD-08, D-ENG-17).
3. Availability: Redis and Postgres are single nodes; add replication and failover.
4. Object store: move off the frozen `bitnamilegacy/minio` image to a maintained S3 store (D-ARC-09).
5. Real decoder (CCSDS/PUS) and validation of the bench interface (§3.5).
6. The scaling fixes measured in `docs/perf/scaling.md` (D-PERF-09).
7. Alarm management: shelving, latching, suppression (D-ALM-13).

---

## 5. Effort, cut order and build order

### 5.1 Effort estimate (hours; fill **Actual** at each gate)

All estimates include writing the tests for that work (D-ENG-14). The spec's gates add up to ~27 h ("about 25"). After design review 1 this design estimated 31.75 h; enhancement round 1 (§9) removes 5.05 h, so it now estimates **≈ 26.7 h**, inside the ~27 h. The cut list below adds ~1.65 h of buffer without touching any pass condition (worst case ≈ 25.05 h). A schedule NCR (`docs/ncr/NCR-000-schedule.md`) is written only if actuals still overrun after the cuts; a must is never cut. Gate 1's research hours are recorded as actuals, not hidden.

Arithmetic: 31.75 − (SR-01 crash-only 0.1 + SR-02 1.0 + SR-03 0.5 + SR-04 0.5 + SR-05 0.75 + SR-06 0.5 + SR-07 0.3 + SR-08 0.5 + SR-09 0.15 + ALT-A 0.25 + ALT-B 0 + ALT-C 0.5 = 5.05) = **26.7**. Per gate: 2: 6.25 − 0.5 = 5.75 · day 2: 7.0 − 0.85 = 6.15 · day 3: 7.5 − 1.95 = 5.55 · 4: 8.0 − 1.75 = 6.25 · 5: 3.0.

| Gate | Work | Est. | Actual |
| --- | --- | --- | --- |
| 1 – Requirements review | research (12 notes), assumptions, design, design review 1, enhancement round 1 | — | record |
| 2 – Design review | repo, compose, Makefile, initdb SQL (D-ENG-12), Redis ACL users (1.25; SR-04 −0.5) · CI with coverage gate (0.5) · simulator v0 + buffer (1.0) · thin worker: validate, store, ack, `ui:events` (1.5) · API SSE + login + live table (1.0) · prove-early checks (0.5) | 5.75 | |
| 3 – day 2 | seq tracker, DLQ, poison isolation, 30 s reclaim, FROZEN detector (1.75; SR-08 flag −0.1, SR-09 events columns/decoder dict/limits stat −0.15) · ownership: blocking lock, hand-back, `idle` steal, crash-only DB errors (1.15; ALT-A −0.25, SR-01 −0.1) · alarm engine + `engine_state` snapshot (2.25; ALT-C −0.25) · limits reload + start fallback (0.5) · simulator faults incl. `pause`, `hold_exec`, `step` (0.5) | 6.15 | |
| 3 – day 3 | commanding: API, guarded transitions, tracker with `decide()` in its own transaction, interlock, audit (1.95; SR-07 −0.3, ALT-B ±0) · simulator command effects (0.5) · ACK path (0.25) · console: alarm list, ack, timeline, STALE/LOS, GROUND PROCESSING DOWN, audio, snapshot reconnect + resync (1.75; SR-03 −0.5, SR-02 FRAMES REJECTED −0.25, sign-off UI −0.25) · sessions + report freeze (0.5; SR-02 sign-off −0.25) · Playwright ATP-02/03/04 (0.6; SR-08 two e2e specs → unit tests, age knob −0.4) | 5.55 | |
| 4 – Test readiness | archiver + test controls (1.0; SR-05 −0.75, SR-02 archive banner −0.25) · replay generator + page (1.0; ALT-C −0.25) · charts (1.0) · perf runs + scaling note (0.75; SR-06 −0.5) · docs: README, operator guide, dev guide, CHANGELOG, `docs/atp/` (2.5) · start the 2 h ATP-01 soak unattended at the end | 6.25 | |
| 5 – Acceptance | fresh clone, all ATPs (soak already done), ATP report, NCRs, demo recording, review answers | 3.0 | |
| **Total** | | **26.7** | |

### 5.2 Cut order (cut from the top when a gate's actuals run over; each cut → one NCR)

Report sign-off, the ARCHIVE_BACKLOG banner and the FRAMES REJECTED banner are no longer cuts: they are not built (§4, SR-02). None of cuts 1–4 touches a pass condition. Cuts 5–6 use the whole should budget (≥ 5 of 7 must pass), so ALM-04, CMD-05, ENG-05, ENG-06 and ENG-07 must then pass.

| # | Cut | Saves | What remains | Prio hit |
| --- | --- | --- | --- | --- |
| 1 | GROUND PROCESSING DOWN banner (D-UI-15) | 0.25 | LOS shown instead; README limitation (Q1 risk) | none |
| 2 | Replay page (D-UI-16) | 0.5 | replay started via API, console shows the stream with banner | none |
| 3 | Versioned late objects (D-ARC-06, already v1+, not in the estimate) | — | late rows stay hot + alert | none |
| 4 | FROZEN detector (D-ING-21) | 0.15 | `freeze` documented as a known limitation (OPS-3 risk) | none |
| 5 | Limits hot reload → reload on restart; hash still stored with every alarm (ALM-08) | 0.5 | Q6 still answerable | **S** 1 of 2 |
| 6 | ING-10 100 Hz run → run once, publish numbers, NCR if lag ≥ 1 s | 0.25 | ING-09 at 10 Hz unaffected | **S** 2 of 2 |

Buffer: 0.25 + 0.5 + 0.15 + 0.5 + 0.25 = 1.65 h.

**Never cut**: anything on a must-requirement's pass condition, including D-ING-09/10/11 (ING-06/07 ownership and takeover), D-ING-12/26 (ING-08), D-ALM-11 (`engine_state`, ING-07) and D-REP-01/03/05 (DAT-06); D-ING-02/13 (frame loss); D-ING-24 (no write without the lock); D-CMD-05 tracker isolation (CMD-02); D-CMD-06 (hazardous commands); D-CMD-08 (audit); D-ALM-14 (ack path); D-UI-18 (resync).

### 5.3 Build order (gates from spec §8)

| Gate | Build | Proves early (open checks from research) |
| --- | --- | --- |
| **2 – Design review** (thin slice) | repo, compose (Redis with ACL users, TimescaleDB with initdb SQL, MinIO), Makefile, CI with coverage gate, demo users; sim (2 vehicles, D-SIM-01..05) → one worker (D-ING-01..03, 13, 22) → `telemetry` → `ui:events` → SSE → live table | MinIO image contents and bucket init · `compose up --wait` with one-shot services · SSE reconnect through compose · Docker VM clock sync · cost of `appendfsync always` · Redis ACL + healthcheck |
| **3 – Implementation** day 2 | ingest depth (D-ING-04..26: blocking lock, hand-back, `idle` steal, crash-only DB errors), alarm engine with `engine_state` snapshot (D-ALM-*), limits reload, simulator faults (D-SIM-07/08/11) | the blocking lock returns at once when the owner's session ends and `pg_locks` shows the home worker waiting; `XINFO CONSUMERS` `idle` on Redis 8.10 behaves as A-E1-1 says; ATP-04, ATP-05 pass |
| **3 – Implementation** day 3 | commanding with tracker `decide()` (D-CMD-*, D-SIM-06/10), ACK path (D-ALM-14), console (D-UI-*), sessions + report (D-DB-06/07/10) | ATP-01 (short), 02, 03, 06 pass |
| **4 – Test readiness** | archiver + test controls (D-ARC-*, D-ENG-16), replay of recorded transitions + determinism test (D-REP-*), perf runs + scaling (D-PERF-*), all §6 docs, ATP-01 2 h soak | ATP-07, ATP-08; every requirement has a test or inspection note ([requirements.md](requirements.md)) |
| **5 – Acceptance** | fresh clone, all ATPs, `atp-report.md`, NCRs (incl. cuts taken and the schedule NCR), demo recording, review answers | acceptance rule met or gaps documented |

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
| ING-07 | D-ING-02, 03, 06, 07, 09, 10, 11, 13, 17, 22 · D-ENG-11 · D-ING-24 · D-UI-18, D-ENG-16 · D-ALM-10, 11 |
| ING-08 | D-ING-04, 05, 12 · D-ING-26 · D-ENG-16 |
| ING-09 | D-ING-22, 23 · D-PERF-01, 05, 07 · D-SIM-01, 02 |
| ING-10 | D-PERF-05, 06 · D-PERF-09 |
| ALM-01 | D-ALM-01 · D-SIM-11 |
| ALM-02 | D-ALM-02 · D-SIM-11 |
| ALM-03 | D-ALM-03, 04 · D-SIM-11 |
| ALM-04 | D-ALM-05 · D-SIM-08 · D-SIM-11 |
| ALM-05 | D-PERF-01, 04, 05 · D-SIM-07 |
| ALM-06 | D-ALM-06, 07, 08 · D-ING-17 · D-SIM-10 · D-ALM-14 · D-REP-03 |
| ALM-07 | D-ALM-10 |
| ALM-08 | D-ALM-12 · D-DB-09 · D-DB-11 |
| CMD-01 | D-CMD-01, 05 · D-SIM-06 · D-SIM-10 · D-CMD-11 |
| CMD-02 | D-CMD-02, 03, 04, 05 · D-SIM-06, 07 · D-SIM-10 · D-CMD-11 |
| CMD-03 | D-CMD-06, 09, 10 · D-ENG-17 |
| CMD-04 | D-CMD-08 · D-ENG-08 · D-ENG-17 |
| CMD-05 | D-CMD-07 · D-ENG-13 |
| UI-01 | D-UI-01, 12, 14 · D-PERF-02 |
| UI-02 | D-UI-04, 05, 06 · D-ING-21 · D-ALM-04 · D-SIM-07 · D-ING-25, D-UI-15 |
| UI-03 | D-UI-06, 07 |
| UI-04 | D-UI-11 · D-DB-02, 04 · D-ARC-05 |
| UI-05 | D-UI-08, 09 · D-ALM-06, 08, 09 · D-ALM-14, D-UI-18 |
| UI-06 | D-UI-10 · D-CMD-02, 08 |
| UI-07 | D-UI-01, 02, 03, 04 · D-UI-15 · D-UI-18 |
| UI-08 | D-UI-13 · D-SIM-02 · D-UI-16 · D-ENG-18 |
| DAT-01 | D-DB-07 · D-UI-17 |
| DAT-02 | D-DB-06 · D-ING-16 · D-UI-17, D-DB-10 |
| DAT-03 | D-DB-01, 02, 05 |
| DAT-04 | D-ARC-01, 03, 06, 07, 09 · D-ENG-16 |
| DAT-05 | D-ARC-02, 03, 04, 05 · D-ARC-10 · D-ENG-16 |
| DAT-06 | D-REP-01..06 · D-ALM-10 · D-DB-09 · D-UI-16 · D-ALM-14, D-ENG-16 |
| ENG-01 | D-ENG-02, 03, 09 · D-ENG-12, 13 |
| ENG-02 | D-ING-07, 08 · D-SIM-05 · D-ENG-03, 11 · D-ING-24 |
| ENG-03 | D-ENG-05 · D-SIM-09 · D-ENG-14, 15 |
| ENG-04 | D-ENG-06 · D-ENG-15 |
| ENG-05 | D-ENG-07 · D-ARC-10, D-ING-25 |
| ENG-06 | D-DB-03 · D-ING-18, 19, 20 · D-CMD-09 · D-DB-11 |
| ENG-07 | D-ENG-10 |
| ENG-08 | §6 deliverables: README, this file, requirements trace, ATP report, guides (no research needed) · D-ENG-19 |
| ENG-09 | D-ENG-04, 09 · D-ARC-08 · D-ENG-12 |
| ENG-10 | D-ENG-01 · D-UI-12 |

---

## 7. Design review 1 disposition

Review: [reviews/design-review-1.md](reviews/design-review-1.md) (PASS WITH CONDITIONS, 5 major · 16 minor · 6 obs). All 27 findings accepted.

| Finding | Sev | Closed by |
| --- | --- | --- |
| RV-01 no ACK path into the engine | MAJOR | D-ALM-14, §3.1, §3.2 `alarm_ack_request`, `after_seq` aligned with R7 |
| RV-02 ATP-03 can't reach NOT_VERIFIED | MAJOR | D-SIM-07 `pause` / `hold_exec`, A-RV-02 |
| RV-03 no v1 actor for 30 s reclaim; cut list hit musts | MAJOR | D-ING-26, D-ING-12 → v1, §5.2 rebuilt |
| RV-04 budget doesn't close | MAJOR | §5.1 re-estimate (docs 2.5 h, soak unattended), §5.2 cuts with no pass-condition impact, schedule NCR |
| RV-05 UI events lost on crash | MAJOR | D-ING-22 order, D-UI-18 resync |
| RV-06 poison crash cascade | MINOR | D-ING-04 (one rule, > 3, every read path) |
| RV-07 engine STALE not deterministic | MINOR | D-ALM-04 data-STALE |
| RV-08 old values shown current after backlog | MINOR | D-UI-05 `age_ms` uses `t_onboard` |
| RV-09 command row races | MINOR | D-CMD-11 |
| RV-10 producer buffer margin | MINOR | D-SIM-05 (6000 frames, 1 s backoff cap) |
| RV-11 banner hidden on fast restart | MINOR | D-UI-02, D-UI-04 |
| RV-12 ALM-02 reading unconfirmed | MINOR | D-ALM-02 + §3.3 (passes both readings) |
| RV-13 no time control or crash hooks | MINOR | D-ENG-16 |
| RV-14 DDL vs decisions | MINOR | D-DB-09, D-CMD-08, §3.2 (`limits_sha256` on alarms, grant matrix) |
| RV-15 report frozen too early | MINOR | D-DB-10 watermark freeze |
| RV-16 LOS shown for ground faults | MINOR | D-ING-25, D-UI-15 |
| RV-17 invalid limits at start stops ingest | MINOR | D-ALM-12 fallback |
| RV-18 command safety only in the API | MINOR | D-ENG-17 Redis ACL, arg bounds |
| RV-19 no workload for 60 alarms | MINOR | D-SIM-07 `step`, D-PERF-04 |
| RV-20 planned tests weaker than pass conditions | MINOR | requirements.md (CMD-04, ALM-08, ING-01, UI-08), D-ENG-18 |
| RV-21 fair share without release | MINOR | D-ING-10 static preference + release (since replaced by the blocking lock + hand-back, §9 ALT-A) |
| RV-22 assumptions register stale | OBS | assumptions.md updated |
| RV-23 simpler options | OBS | D-REP-04, D-UI-10 simplified; (c)–(f) in §5.2 |
| RV-24 FROZEN counts frames | OBS | D-ING-21 time-based |
| RV-25 bench interface rationale | OBS | §3.5 |
| RV-26 hazardous interlock by args | OBS | D-CMD-07 name-only for hazardous |
| RV-27 retry inside one transaction | OBS | D-ING-04 isolate by re-running per frame |

## 8. Review-board questions (spec §7) → where the answer lives

| Q | Answer from |
| --- | --- |
| 1 current, not frozen? | D-UI-05, D-ING-21, D-UI-15 |
| 2 acked, then worse? | D-ALM-08 |
| 3 FAILED: did it happen, what do I do? | D-CMD-02/04; operator guide: on `UNKNOWN` check `mode`, `last_cmd_id` and the affected parameters, never blind-resend a hazardous command, tell the test conductor |
| 4 trust a replay? | D-REP-01, 03, 05: replay shows the frames with the alarm transitions and ACKs recorded live, nothing recomputed; the determinism test proves today's logic reproduces them (D-ALM-14 `after_seq`, D-ALM-12 `LIMITS_APPLIED`); D-DB-09 keeps those rows forever |
| 5 report correct after two dropouts? | D-ING-16, D-DB-06, D-DB-10 |
| 6 which limits applied? | D-ALM-12 |
| 7 why Redis, when Kafka? | D-ING-01, R3 switch triggers |
| 8 where could a frame be lost or doubled? | §3.6 |
| 9 per-vehicle order with two workers | D-ING-09/10/11/12: each vehicle stream has one reader at a time, holding a Postgres lock on the connection it writes with; the other worker waits on that lock and continues the moment the owner's session ends; a stuck owner (no read for 30 s) has its session killed; README "how order is kept" |
| 10 frame crashes the worker every time | D-ING-04 |
| 11 10 vehicles at 100 Hz | D-PERF-09 |
| 12 Parquet, hourly, delete after checksum | D-ARC-01..05 |
| 13 swap JSON for CCSDS | §3 decoder plug point, §3.5 |
| 14 not production-ready, in what order | §4 ordered list |
| 15 what was cut and why | §5.2 + NCRs |
| 16 which test caught a real bug | D-ENG-19 `docs/bug-log.md` |
| 17 how AI was used and checked | D-ENG-19 `docs/ai-usage.md` |
| 18 a junior adds a parameter | D-DB-11, `docs/dev-guide.md`, ENG-06 |

## 9. Enhancement round 1 disposition

Reviews: [reviews/simplification-review.md](reviews/simplification-review.md) (SR-01..09) and [reviews/alternatives-review.md](reviews/alternatives-review.md) (hotspots ALT-A/B/C). Hours are savings against the 31.75 h of design review 1 (§5.1); for partly accepted findings only the accepted part is counted.

| Finding | Disposition | Decisions changed | Hours |
| --- | --- | --- | --- |
| SR-01 static partition + crash-only | **Partly accepted**: only "worker exits on any Postgres error and Docker restarts it". Fixed vehicle-to-worker assignment without failover is **rejected**: `docker kill` does not trigger Docker's restart policy (R11 probe, A-R11-6), so a killed worker's vehicle would stop until someone runs `up`, which breaks OPS-4 "nobody has to run a manual fix" and ATP-05. Failover stays (ALT-A) | D-ING-24 (with ALT-A) | −0.1 |
| SR-02 delete sign-off and two banners | Accepted | D-DB-10, D-UI-17, D-CMD-08, D-ARC-10, D-UI-15, D-ING-25, §3.1, §3.2, §4, §5.2 | −1.0 |
| SR-03 snapshot on every reconnect | Accepted | D-UI-01, D-UI-02, D-UI-03, D-UI-18, §3.1 | −0.5 |
| SR-04 initdb instead of a migrations runner | Accepted | D-ENG-12, D-ENG-14, §4, §5.3 | −0.5 |
| SR-05 smaller archiver | Accepted | D-ARC-01, D-ARC-02, D-ARC-04, D-ARC-05, D-ENG-16, §3.2 `archive_object` | −0.75 |
| SR-06 smaller measurement | Accepted | D-PERF-02, D-PERF-05, D-PERF-09, D-UI-14, §3.1 | −0.5 |
| SR-07 four command deletions | **Partly accepted**: `outcome` computed from `failure_reason`, QUEUED sweep merged into the 5 s not-received timeout, `commands_sha256` dropped. **Rejected**: name-only interlock for every command, which would block a legitimate `isolate_valve A` then `isolate_valve B` within 2 s; D-CMD-07 unchanged | D-CMD-02, D-CMD-09, D-CMD-11, §3.1, §3.2 | −0.3 |
| SR-08 smaller freshness and FROZEN | Accepted | D-UI-05, D-ING-21, §4 | −0.5 |
| SR-09 delete things nothing reads | **Accepted except** deleting `ui/src/palette.test.ts`: kept, because everything built is tested (D-ENG-14) and it is cheap; D-UI-07 unchanged | D-DB-08, D-ALM-12, §3 decoder plug point, §3.2 `events` | −0.15 |
| ALT-A ingest ownership | Accepted (hybrid H-A): advisory lock on the writing connection kept; one blocking lock call per (worker, vehicle); hand-back when Postgres shows the home worker waiting; steal on owner consumer `idle` > 30 s | D-ING-09, D-ING-10, D-ING-11, D-ING-12, D-ING-24, D-ING-26, D-ENG-11, D-ENG-16, D-PERF-07 | −0.25 |
| ALT-B command tracking | Accepted (hybrid H-B): tracking stays in the owner, after the frame COMMIT, own transaction, pure `decide()` over stored evidence, timeouts only once caught up, errors never touch frames | D-CMD-05, D-CMD-11, D-ING-22 | ±0 |
| ALT-C alarm rebuild and replay | Accepted (replace, H-C): `engine_state` snapshot in every frame transaction replaces LEVEL events and the K-frame warm-up; replay shows recorded transitions; recomputation becomes the determinism test | D-ALM-10, D-ALM-11, D-ALM-12, D-ALM-14, D-REP-01, D-REP-03, D-REP-05, D-REP-06, D-DB-09, D-ING-11, D-ING-22, §3.2, §3.6 | −0.5 |
| **Total** | | | **−5.05 (31.75 → 26.7)** |

No decision row was deleted, so every D-ID reference still resolves. New assumptions from this round are A-E1-1..11 in [assumptions.md](assumptions.md).

### Gate 2 spikes ([S1](research/S1-gate2-spikes.md))

All six PASS (S2, S6 after a fix). Design changes applied: D-ARC-09 (digest, volume path, healthcheck, same-image init), D-ENG-03 (`service_completed_successfully` rule, explicit `stop_grace_period`), D-ENG-06 (CI command), D-ENG-17 (tested ACL file, `tm:NYX-*` scoping, `grep -q PONG`), D-UI-02/04 (shutdown hook; measured 1.14–1.25 s vs 11.1 s without), D-ING-07 (fsync cost measured; host power-loss caveat). D-PERF-01 confirmed (container clocks agree within 0.2 ms; host–VM −0.5 ms median, no drift over 3 min; behaviour after host sleep untested).
