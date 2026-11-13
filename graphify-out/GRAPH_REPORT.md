# Graph Report - .  (2026-11-13)

## Corpus Check
- 21 files · ~82,201 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 842 nodes · 3123 edges · 25 communities
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 243 edges (avg confidence: 0.84)
- Token cost: 605,617 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Ingest broker & ownership assumptions|Ingest broker & ownership assumptions]]
- [[_COMMUNITY_Alarm logic assumptions|Alarm logic assumptions]]
- [[_COMMUNITY_Console & replay assumptions|Console & replay assumptions]]
- [[_COMMUNITY_Latency measurement|Latency measurement]]
- [[_COMMUNITY_Value freshness & limit display|Value freshness & limit display]]
- [[_COMMUNITY_Command lifecycle & standards|Command lifecycle & standards]]
- [[_COMMUNITY_UTC time & context|UTC time & context]]
- [[_COMMUNITY_Stack, login & CI|Stack, login & CI]]
- [[_COMMUNITY_Sequence tracking & resets|Sequence tracking & resets]]
- [[_COMMUNITY_Time-series store|Time-series store]]
- [[_COMMUNITY_Archive & replay notes|Archive & replay notes]]
- [[_COMMUNITY_Scope, cuts & schedule|Scope, cuts & schedule]]
- [[_COMMUNITY_Sessions & report scope|Sessions & report scope]]
- [[_COMMUNITY_Delivery engineering|Delivery engineering]]
- [[_COMMUNITY_Archive pipeline|Archive pipeline]]
- [[_COMMUNITY_Command safety & audit|Command safety & audit]]
- [[_COMMUNITY_Command tracker|Command tracker]]
- [[_COMMUNITY_Simulator & bench ICD|Simulator & bench ICD]]
- [[_COMMUNITY_Frame validation|Frame validation]]
- [[_COMMUNITY_Command timeouts & FAILED|Command timeouts & FAILED]]
- [[_COMMUNITY_Parameter registry & schema|Parameter registry & schema]]
- [[_COMMUNITY_Compose, MinIO & CI spikes|Compose, MinIO & CI spikes]]
- [[_COMMUNITY_Test sessions|Test sessions]]
- [[_COMMUNITY_Double-command interlock|Double-command interlock]]
- [[_COMMUNITY_Archive verify-then-delete|Archive verify-then-delete]]

## God Nodes (most connected - your core abstractions)
1. `Assumptions and open questions (Gate 1)` - 103 edges
2. `R11 – Stack, delivery tooling, frame validation and sessions (incl. R13)` - 85 edges
3. `R8 – Alarm logic and alarm management` - 79 edges
4. `R2 – Space standards vocabulary + command safety` - 69 edges
5. `R9 – Operator console` - 65 edges
6. `R7 – Archive and replay` - 64 edges
7. `Review board questions` - 63 edges
8. `R5 – Idempotency, dedup, gap counting and sequence resets` - 62 edges
9. `R1 – Prior art: open-source mission control systems` - 59 edges
10. `R6 – Hot time-series store` - 58 edges

## Surprising Connections (you probably didn't know these)
- `Apache Kafka` --semantically_similar_to--> `Redis Streams`  [INFERRED] [semantically similar]
  docs/research/00-research-plan.md → Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md
- `NATS JetStream` --semantically_similar_to--> `Redis Streams`  [INFERRED] [semantically similar]
  docs/research/00-research-plan.md → Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md
- `ICD-B-02 seq +1, µs UTC monotonic, synced clock` --conceptually_related_to--> `Telemetry frame (10 Hz, tm:<vehicle>)`  [INFERRED]
  docs/design.md → Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md
- `R3 – Redis Streams delivery semantics` --references--> `Telemetry frame (10 Hz, tm:<vehicle>)`  [INFERRED]
  docs/research/R3-redis-streams.md → Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md
- `D-DB-11 Parameter registry in limits.yaml` --implements--> `limits.yaml (versioned by file hash)`  [INFERRED]
  docs/design.md → Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **ALT-A ingest ownership protocol** — docs_design_d_ing_09, docs_design_d_ing_10, docs_design_d_ing_11, docs_design_d_ing_12, docs_design_d_ing_24, docs_design_d_ing_26, reviews_alternatives_review_alt_a [EXTRACTED 1.00]
- **ALT-C engine_state + recorded replay** — docs_design_d_alm_10, docs_design_d_alm_11, docs_design_d_alm_12, docs_design_d_alm_14, docs_design_d_rep_01, docs_design_d_rep_03, docs_design_d_rep_05, docs_design_d_rep_06, docs_design_d_db_09, reviews_alternatives_review_alt_c [EXTRACTED 1.00]
- **§5.2 cut order (NCR per cut)** — docs_design_cut_1, docs_design_cut_2, docs_design_cut_3, docs_design_cut_4, docs_design_cut_5, docs_design_cut_6, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_ncr [EXTRACTED 1.00]
- **R1 custom build borrows from three prior-art systems** — research_r1_prior_art_mission_control_decision, research_00_research_plan_yamcs, research_00_research_plan_openc3_cosmos, research_00_research_plan_nasa_open_mct, research_r1_prior_art_mission_control_acquisition_vs_monitoring, research_r1_prior_art_mission_control_cosmos_limits_fields, research_r1_prior_art_mission_control_open_mct_provider_api [EXTRACTED 1.00]
- **Server-side command safety chain (two-step, interlock, audit, outcome)** — research_r2_standards_and_command_safety_hazardous_two_step, research_r2_standards_and_command_safety_interlock_set_nx, research_r2_standards_and_command_safety_audit_actions, research_r2_standards_and_command_safety_failed_outcome, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_cmd_03, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_cmd_04, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_cmd_05 [EXTRACTED 1.00]
- **Alarm pipeline: boundary → deadband → persistence → ROC → life cycle** — research_r8_alarm_logic_boundary_rule, research_r8_alarm_logic_hysteresis_pct_of_span, research_r8_alarm_logic_persistence_ring, research_r8_alarm_logic_ols_roc, research_r8_alarm_logic_alarm_lifecycle_table, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_alm [INFERRED 0.85]
- **Effectively-once ingest: idempotent key + seq_state + event keys in one txn, XACK after commit** — research_r5_idempotency_gaps_resets_storage_key, research_r5_idempotency_gaps_resets_seq_state, research_r5_idempotency_gaps_resets_event_keys, research_r3_redis_streams_decision, research_r6_timeseries_store_psycopg3 [EXTRACTED 1.00]
- **Verify-then-delete chain: checksummed single PUT, full re-read, state machine, count-checked hot delete** — research_r7_archive_replay_single_put_checksum, research_r7_archive_replay_full_reread_verify, research_r7_archive_replay_archive_object, research_r6_timeseries_store_controlled_delete, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_verify_then_delete [EXTRACTED 1.00]
- **Latency stamps across one VM clock domain plus browser probe** — research_r10_latency_measurement_clock_timestamp, research_r10_latency_measurement_redis_entry_id_stamp, research_r10_latency_measurement_ui_probe, research_r10_latency_measurement_offset_correction, research_r9_operator_console_latency_hooks [INFERRED 0.85]
- **Competing redesigns of ingest ownership (D-ING-10/12/24)** — reviews_design_review_1_rv_03, reviews_design_review_1_rv_21, reviews_simplification_review_sr_01, reviews_alternatives_review_alt_a_static_per_vehicle, reviews_alternatives_review_alt_a_hybrid, reviews_alternatives_review_a_f1, docs_design_d_ing_10, docs_design_d_ing_12, docs_design_d_ing_24 [INFERRED 0.85]
- **Command tracking hardening (CMD-02 both cases, monotonic transitions, durable evidence)** — reviews_design_review_1_rv_02, reviews_design_review_1_rv_09, reviews_simplification_review_sr_07, reviews_alternatives_review_alt_b_hybrid, reviews_alternatives_review_b_f2, docs_design_d_cmd_11, docs_design_d_sim_07, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_cmd_02 [INFERRED 0.85]
- **Alarm ACK path and replay equality (DAT-06 / Q4)** — reviews_design_review_1_rv_01, reviews_design_review_1_rv_07, reviews_alternatives_review_c_f1, reviews_alternatives_review_c_f2, reviews_alternatives_review_alt_c_hybrid, reviews_alternatives_review_c6_experiment, docs_design_d_alm_14, docs_design_d_rep_03, nyx_ground_segment___test_campaign_monitoring___commanding_requirements_and_acce_gs_dat_06 [INFERRED 0.85]

## Communities (25 total, 0 thin omitted)

### Community 0 - "Ingest broker & ownership assumptions"
Cohesion: 0.06
Nodes (123): A-E1-1 XINFO CONSUMERS idle > 30 s means owner is hung (steal trigger), A-E1-2 application_name = consumer name; pg_locks shows home worker waiting (hand-back), A-E1-3 Crash-only on Postgres errors is acceptable (peer takes vehicles, Docker restart), A-R11-6 ATP-05 kill a worker = docker compose kill ingest-1 (takeover); kill -9 1 for self-restart, A-R3-1 Producer buffers frames while Redis is down, flushes on reconnect, A-R3-2 Frame failing 3x = 3 failed deliveries; DLQ on 4th claim, A-R3-3 Redis >= 8.2 required (ACKED trimming); image pinned 8.10.3, A-R3-4 One tm:dlq stream for malformed and poison frames, with reason field (+115 more)

### Community 1 - "Alarm logic assumptions"
Cohesion: 0.05
Nodes (108): A-E1-5 engine_state schema_version mismatch → approximate rebuild + ENGINE_STATE_RESET, A-E1-6 DAT-06 met by archived frames + recorded alarm transitions; determinism test covers recompute, A-R1-5 RTN before ack: stays ACTIVE-unacked (Yamcs) or separate RTN_UNACKED state?, A-R8-1 On-limit = more severe band (EPICS, COSMOS), A-R8-2 "±1 %" in ALM-02 = % of span, not % of limit value, A-R8-3 Persistence N gates escalation, de-escalation and clearing alike, A-R8-4 GAP, SEQ_RESET and STALE reset the counters and the ROC window, A-R8-5 Acked yellow → red becomes unacknowledged again and alerts once (+100 more)

### Community 2 - "Console & replay assumptions"
Cohesion: 0.06
Nodes (82): Assumptions and open questions (Gate 1), A-E1-11 Missed tm deltas during SSE reconnect not replayed; snapshot covers it, A-E1-8 Bench clock equals ground clock; age_ms needs no offset, A-R10-2 ALM-05 latency starts at t_onboard of the frame completing the alarm condition, A-R11-2 DLQ fields follow the R13 record above (supersedes R3 src/src_id/payload), A-R3-6 Live UI does not read the stream via a second consumer group, A-R7-5 alarm_events, events and limits_versions are never pruned, A-R7-6 ACK rows carry after_seq, so replay reproduces ack-dependent transitions (+74 more)

### Community 3 - "Latency measurement"
Cohesion: 0.10
Nodes (54): A-R10-1 "stored" = row inserted, stamped clock_timestamp() at INSERT, A-R10-3 run 60 alarms instead of the spec's 20 (n ≥ 59 needed for a 95 % bound), A-R10-4 UI-01 = server half (now − t_onboard) + page half (paint − receipt), A-R10-5 ING-10 "lag < 1 s" = max over 1 s samples of time lag (now − newest stored t_onboard), A-R10-6 ING-09, ALM-05 and UI-01 pass/fail at the specified 10 Hz in run N, A-R10-7 Percentile = nearest-rank; first 10 s of each run excluded, A-R10-8 t_onboard serialized with µs precision (ISO 8601 …ffffffZ), D-ING-23 t_received = clock_timestamp() (+46 more)

### Community 4 - "Value freshness & limit display"
Cohesion: 0.11
Nodes (46): A-R1-3 Two orthogonal fields per value: acquisition status and limit state (RED and STALE possible), A-R1-4 Value exactly on a limit → the more severe state (COSMOS rule), D-ALM-01 On-limit value goes to worse band, D-UI-06 Acquisition × limit state fields, Not built: Yamcs / COSMOS deployment, e2e/atp04-los.spec.ts, ui/src/freshness.test.ts, tests/unit/test_frozen_detector.py (+38 more)

### Community 5 - "Command lifecycle & standards"
Cohesion: 0.10
Nodes (43): A-R2-1 RECEIVED signalled by simulator ack stream tcack:<vehicle> (= PUS TM[1,1]/[1,2]), A-R2-2 last_cmd_id echo seen before any ack → VERIFIED (receipt implied, flagged), A-R2-5 Hazardous two-step confirmed by same user (confirm_args_hash + reason ≥ 10 chars), A-R2-6 Simulator executes ≤ 1 command per frame tick, so last_cmd_id never skips a command, A-R2-7 Command timers keep running during LOS (no pause), Command lifecycle QUEUED→SENT→RECEIVED→VERIFIED / FAILED, Pluggable CCSDS decoder layer (JSON stand-in), CCSDS Space Packet + XTCE (+35 more)

### Community 6 - "UTC time & context"
Cohesion: 0.08
Nodes (38): A-R11-4 t_onboard validity window [now − 7 d, now + 5 min]; output always Z, A-R12-1 Target bench user is both AIT test conductor and flight controller, A-R12-2 Keep 'thermal' in spec §1 as a generic test type (no public TEC thermal report), A-R12-3 No README line about sitting next to a Rails backend unless asked, D-ENG-18 One UTC time formatter iso_z(), D-UI-13 Times via toISOString(), tests/unit/test_time_format.py, ui/src/time.test.ts (+30 more)

### Community 7 - "Stack, login & CI"
Cohesion: 0.11
Nodes (37): A-R11-5 No roles: any logged-in user may run sessions and hazardous commands; identity recorded, D-ENG-08 Login with scrypt users.yaml, R11 – Stack, delivery tooling, frame validation and sessions (incl. R13), GitHub Actions ci.yml (lint ∥ test → e2e), actions checkout@v7, setup-uv@v10, setup-node@v7, upload-artifact@v7, R11 decision: one Python package gs with entry points, one 2-stage Dockerfile, compose reused for tests/CI, Playwright TS, stdlib JSON logs + asyncio /healthz, scrypt + signed session cookie, env-only config, Two-stage Dockerfile (node builds ui/dist → python:3.14-slim + uv, FastAPI StaticFiles), Env-only config into frozen dataclass; make up generates SESSION_SECRET, FastAPI / Starlette / uvicorn (+29 more)

### Community 8 - "Sequence tracking & resets"
Cohesion: 0.11
Nodes (36): A-R5-1 duplicate fault resends byte-identical frame (same seq and t_onboard), A-R5-2 dropout N keeps on-board seq counter running, drops frames in transit, A-R5-3 Simulator restart resets seq to 0, t_onboard stays monotonic, A-R5-4 seq_jump produces SEQ_JUMP event, not GAP, A-R5-5 Frame format stays as spec §3; no boot_id (future ICD change), A-R5-7 Replay output never writes to live tables, D-ING-15 Data-derived epoch in seq_state, D-ING-17 Deterministic event keys (+28 more)

### Community 9 - "Time-series store"
Cohesion: 0.14
Nodes (32): A-R6-2 GS-DAT-03 < 300 ms measured as API round trip, A-R6-6 Image timescaledb:2.32.0-pg17, Apache-2 features only, A-X-1 Conflict R5 vs R6 dedup key resolved: unique key (vehicle, t_onboard, seq); epoch only on event rows, D-DB-01 One TimescaleDB, D-DB-02 Wide hypertable, 1 h chunks, D-DB-04 time_bucket min/max downsampling, D-DB-05 Apache-2 Timescale features only, Not built: compression, continuous aggregates (+24 more)

### Community 10 - "Archive & replay notes"
Cohesion: 0.13
Nodes (30): A-R7-1 Sidecar name <HH>.parquet.sha256, content in sha256sum format, A-R7-2 Late frames produce <HH>.v<N>.parquet (+ sidecar), A-R7-3 MinIO image choice: bitnamilegacy/minio:2025.5.24 vs pgsty/minio vs source build, A-R7-8 Replay pacing uses t_onboard (vehicle time), not t_received, D-REP-02 Replay pacing 1× / 10×, R7 – Archive and replay, APScheduler, archiver service: asyncio loop run_once() every 5 min + at start-up; pg_try_advisory_lock single runner (+22 more)

### Community 11 - "Scope, cuts & schedule"
Cohesion: 0.10
Nodes (27): A-RV-04 Schedule ≈ 26.7 h vs 27 h; cuts first, schedule NCR only if actuals overrun, Nyx Ground Segment – What We Build (design), Cut 2: Replay page, Cut 3: Versioned late objects, Cut 4: FROZEN detector, Cut 6: ING-10 100 Hz run, D-ALM-13 Out of scope: shelving, latching etc., D-UI-16 Replay page (UTC inputs) (+19 more)

### Community 12 - "Sessions & report scope"
Cohesion: 0.16
Nodes (24): A-E1-10 Conductor report sign-off happens outside the tool, A-E1-9 No FRAMES REJECTED banner; t_onboard_range rejects show as LOS, DLQ says why, A-R5-6 Session report lists SEQ_RESET as gap of unknown size, A-R6-4 Sessions <= 24 h; report frozen into a row at session stop, A-R6-5 If MinIO is down hot data grows; health alert at > 72 h unarchived, A-X-2 Conflict R7 vs R9 replay transport: async generator in API (GET /api/replays/{id}/events), Cut 1: GROUND PROCESSING DOWN banner, D-ARC-10 Archiver healthz degraded at 72 h (+16 more)

### Community 13 - "Delivery engineering"
Cohesion: 0.15
Nodes (24): D-ARC-08 S3 client minio-py, D-ENG-01 One Python package gs, D-ENG-02 One two-stage Dockerfile, D-ENG-04 Config from env only, D-ENG-05 Tests at 3 levels, D-ENG-07 healthz + JSON logs everywhere, D-ENG-09 Pinned versions, D-ENG-10 Conventional small commits (+16 more)

### Community 14 - "Archive pipeline"
Cohesion: 0.26
Nodes (19): A-R7-4 Archive eligibility: hour_end + 10 min ≤ now; late frame keeps the hour hot, D-ARC-01 Parquet per vehicle-hour, D-ARC-02 Hash the uploaded bytes, D-ARC-03 Single PUT + full re-read verify, D-ARC-04 archive_object row after verification, D-ARC-05 Eligibility and verified hot delete, D-ARC-06 Late frames stay hot + alert, D-ARC-07 Archiver asyncio loop every 5 min (+11 more)

### Community 15 - "Command safety & audit"
Cohesion: 0.21
Nodes (18): D-CMD-06 Hazardous two-step in the API, D-CMD-08 Append-only audit_log, D-CMD-10 No roles, D-DB-08 One events table for ground events, D-ENG-17 Redis ACL users, Not built: tamper-evident audit, Prod gap 2: Tamper evidence, e2e/hazardous.spec.ts (+10 more)

### Community 16 - "Command tracker"
Cohesion: 0.25
Nodes (17): A-E1-4 Command tracker 'caught up' when last XREADGROUP returned < full batch, D-CMD-01 Command states QUEUED→VERIFIED, D-CMD-02 FAILED reasons and outcome, D-CMD-05 Command tracking in owner, own txn, D-CMD-11 Monotonic command row updates, D-UI-10 Command timeline from commands row, GS-UI-06 Each command shows its state timeline with all four times, B. Command tracking (+9 more)

### Community 17 - "Simulator & bench ICD"
Cohesion: 0.22
Nodes (14): D-SIM-06 Sim executes ≤1 command per tick, tcack, D-SIM-10 Sim command effects, ICD-B-01 Ordered frames, ≥5 min buffering, ICD-B-03 Command ack on tcack, ICD-B-04 ≤1 command per frame, last_cmd_id, tests/unit/test_cmd_decide.py, tests/unit/test_command_state.py, tests/integration/test_commands_api.py (+6 more)

### Community 18 - "Frame validation"
Cohesion: 0.23
Nodes (13): A-R11-1 Stream entry has one field data = JSON frame, A-R11-3 Registered parameter missing from frame → NULL + counter + warning (not DLQ), A-R6-3 Frame field not in parameter registry: ignored, counted, warned (not DLQ), D-ING-18 Strict pydantic frame model, D-ING-19 Unknown frame field ignored, counted, D-ING-20 Missing parameter stored as NULL, D-SIM-04 One data field per stream entry, tests/unit/test_frame_model.py (+5 more)

### Community 19 - "Command timeouts & FAILED"
Cohesion: 0.27
Nodes (13): A-R1-1 CMD-02 "not verified in 15 s" is measured from SENT (not from RECEIVED), A-R1-2 FAILED is terminal; later last_cmd_id echo logged as LATE_VERIFICATION, A-R2-3 FAILED outcome NOT_SENT / NOT_EXECUTED / UNKNOWN; no blind hazardous resend, A-RV-02 ATP-03 6 s = no ack (NOT_RECEIVED), 16 s = execution held (NOT_VERIFIED), D-CMD-03 5 s / 15 s windows from SENT, D-CMD-04 FAILED terminal, LATE_VERIFICATION, Loss point: Command tracker fault, tests/integration/test_cmd_tracker.py (+5 more)

### Community 20 - "Parameter registry & schema"
Cohesion: 0.36
Nodes (10): A-E1-7 Schema migrations out of scope; schema runs once via initdb (make reset), D-DB-03 Parameter columns from registry, D-DB-11 Parameter registry in limits.yaml, D-ENG-12 Schema via initdb migrations SQL, D-ENG-14 Definition of done: test per change, Not built: in-place schema migrations, GS-ENG-06 Add a parameter without changing core code, Q18 draft answer: junior adds a parameter (dev-guide, boundary tests, < 15 min) (+2 more)

### Community 21 - "Compose, MinIO & CI spikes"
Cohesion: 0.42
Nodes (9): A-R11-7 MinIO runs from the community fork pgsty/minio pinned, A-X-3 Conflict R7 vs R11 MinIO image: default bitnamilegacy/minio:2025.5.24 pinned by digest, D-ARC-09 Pinned bitnamilegacy/minio image, D-ENG-03 docker compose restart + healthchecks, D-ENG-06 One CI workflow, S1 - Gate 2 spikes (throwaway, measured 2026-11-13), S1 MinIO image bitnamilegacy/minio:2025.5.24, S2 docker compose up --wait with one-shot services (+1 more)

### Community 22 - "Test sessions"
Cohesion: 0.25
Nodes (9): D-DB-07 test_session, one active per vehicle, tests/integration/test_sessions.py, GS-DAT-01 Named sessions start and stop per vehicle and store…, btree_gist exclusion constraint on tstzrange for sessions, pydantic extra='forbid', Flat Redis stream fields per param, Lax pydantic validation, R13 decision: strict pydantic FrameBase + registry create_model, single data field, t_onboard window, DLQ record, test_session with partial unique index (+1 more)

### Community 23 - "Double-command interlock"
Cohesion: 0.50
Nodes (8): A-R2-4 CMD-05 same command = vehicle + name (+ args hash for non-hazardous); SET NX PX 2000, D-CMD-07 Double-command interlock (cmdlock), D-CMD-09 commands.yaml validated by pydantic, commands.yaml catalogue (hazardous flag), GS-CMD-05 The same command from two operators within 2 s rejects the…, commands.yaml draft schema (hazardous, hazard text, args, timeouts; XTCE names), RV-26 Args-hash interlock lets two hazardous commands through, SR-07 Four small command deletions (name-only interlock, no outcome column, merged QUEUED sweep, no commands_sha256)

### Community 24 - "Archive verify-then-delete"
Cohesion: 0.36
Nodes (8): A-R6-1 Hypertable partitioned and archived on t_onboard, not t_received, Hourly Parquet archive + .sha256 in MinIO, Verify-then-delete archive pattern, pyarrow / DuckDB Parquet reader, R7 Archive and replay, Controlled per-(vehicle, hour) DELETE with row-count check, then drop_chunks, TimescaleDB drop_chunks (whole chunks only), archive_object table / job state machine (WRITTEN → UPLOADED → VERIFIED → HOT_DELETED, PENDING computed)

## Ambiguous Edges - Review These
- `MinIO distribution change: upstream archived/source-only; bitnamilegacy/minio:2025.5.24 pinned` → `pgsty/minio + pgsty/mc community fork images`  [AMBIGUOUS]
  docs/research/R11-stack-and-delivery.md · relation: conceptually_related_to
- `SR-01 Static vehicle ownership with crash-only restart` → `H-A blocking lock per (worker, vehicle) + Postgres-only hand-back + XINFO idle > 30 s steal`  [AMBIGUOUS]
  docs/reviews/simplification-review.md · relation: conceptually_related_to
- `SR-03 Merge two reconnect mechanisms into snapshot-then-deltas` → `S6 SSE reconnect through docker compose restart api`  [AMBIGUOUS]
  docs/research/S1-gate2-spikes.md · relation: conceptually_related_to

## Knowledge Gaps
- **33 isolated node(s):** `Ground software engineer`, `perf/soak_count.py`, `tests/unit/test_frame_model.py`, `tests/integration/test_seq_reset.py`, `perf/run_n.py` (+28 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `MinIO distribution change: upstream archived/source-only; bitnamilegacy/minio:2025.5.24 pinned` and `pgsty/minio + pgsty/mc community fork images`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `SR-01 Static vehicle ownership with crash-only restart` and `H-A blocking lock per (worker, vehicle) + Postgres-only hand-back + XINFO idle > 30 s steal`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `SR-03 Merge two reconnect mechanisms into snapshot-then-deltas` and `S6 SSE reconnect through docker compose restart api`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Assumptions and open questions (Gate 1)` connect `Console & replay assumptions` to `Ingest broker & ownership assumptions`, `Alarm logic assumptions`, `Latency measurement`, `Value freshness & limit display`, `Command lifecycle & standards`, `UTC time & context`, `Stack, login & CI`, `Sequence tracking & resets`, `Time-series store`, `Archive & replay notes`, `Scope, cuts & schedule`, `Sessions & report scope`, `Archive pipeline`, `Command tracker`, `Frame validation`, `Command timeouts & FAILED`, `Parameter registry & schema`, `Compose, MinIO & CI spikes`, `Double-command interlock`, `Archive verify-then-delete`?**
  _High betweenness centrality (0.117) - this node is a cross-community bridge._
- **Why does `R11 – Stack, delivery tooling, frame validation and sessions (incl. R13)` connect `Stack, login & CI` to `Ingest broker & ownership assumptions`, `Console & replay assumptions`, `UTC time & context`, `Sequence tracking & resets`, `Archive & replay notes`, `Delivery engineering`, `Command safety & audit`, `Frame validation`, `Compose, MinIO & CI spikes`, `Test sessions`?**
  _High betweenness centrality (0.104) - this node is a cross-community bridge._
- **Why does `Review board questions` connect `Sessions & report scope` to `Ingest broker & ownership assumptions`, `Alarm logic assumptions`, `Console & replay assumptions`, `Latency measurement`, `Value freshness & limit display`, `Command lifecycle & standards`, `UTC time & context`, `Sequence tracking & resets`, `Time-series store`, `Archive & replay notes`, `Scope, cuts & schedule`, `Delivery engineering`, `Archive pipeline`, `Command tracker`, `Command timeouts & FAILED`, `Parameter registry & schema`, `Compose, MinIO & CI spikes`, `Archive verify-then-delete`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `R11 – Stack, delivery tooling, frame validation and sessions (incl. R13)` (e.g. with `Research note template (decision candidate + rejected alternative)` and `R11 Stack and delivery tooling`) actually correct?**
  _`R11 – Stack, delivery tooling, frame validation and sessions (incl. R13)` has 2 INFERRED edges - model-reasoned connections that need verification._