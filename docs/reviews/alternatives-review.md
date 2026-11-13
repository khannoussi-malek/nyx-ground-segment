# Alternatives review: ingest ownership, command tracking, alarm rebuild

Independent second opinion on three high-risk parts of `docs/design.md` (draft for Gate 2, 2026-11-13). Checked against the spec ("spec": `Nyx Ground Segment – … Acceptance.md`), research notes R2–R8, and `reviews/design-review-1.md` (RV-xx). The reviewer did not write the design. For each hotspot, the current design and 2–3 structurally different alternatives are scored the same way. The current design is kept only where it wins on the scores.

## 0. Result in one table

| Hotspot | Recommendation | D-IDs touched | Hours vs §5.1 |
| --- | --- | --- | --- |
| A. Ingest ownership | **Hybrid.** Keep the advisory lock on the writing session. Replace the polled try-lock + "free > 2 s" timer + Redis-heartbeat release with a **blocking lock per (worker, vehicle)** and a Postgres-only hand-back. Trigger the steal on the **owner consumer's `idle` > 30 s** instead of "an entry pending > 30 s" (that trigger has a hole). | D-ING-10, 12, 24 (D-ING-09, 11, 26 kept) | **−0.25 h** |
| B. Command tracking | **Hybrid.** Keep tracking in the vehicle's owner, but move it **out of the frame transaction**. Make it a pure `decide()` over **durable evidence** (the `commands` row plus `telemetry.last_cmd_id`), and time out only once the owner has caught up. | D-CMD-05, 11, D-ING-22 | **±0 h** |
| C. Alarm rebuild and replay | **Replace.** Rebuild after takeover from a **full engine-state snapshot row written in every batch transaction**, not LEVEL events plus a K-frame warm-up. Replay **plays back the recorded transitions** next to the archived frames and does not recompute them. Recomputation stays as a test. | D-ALM-10, 11, D-REP-01, 03, 05, §3.2 DDL | **−0.5 h** |

Net: **≈ −0.75 h** on the 31.75 h estimate (design §5.1). Every must pass condition stays covered, and three latent defects in the current design are closed (A-F1, B-F1, C-F1 below).

**Scoring scale** (1–5, 5 is best in every column): **Cov** = pass-condition coverage for the listed requirements · **Safe** = failure-mode safety under crash, restart, hang, Redis outage and Postgres outage · **Hrs** = build + test hours for one engineer (5 = cheapest) · **Test** = deterministic testability · **Expl** = can be explained to a review board in under a minute. An alternative that fails a must pass condition is marked **DQ** (disqualified) whatever its total.

---

## A. Ingest ownership and ordering

Requirements: ING-06 "2 workers share load; per-vehicle order kept (document how)", ING-07 "kill a worker mid-stream: 0 frames lost", ING-08 "entry idle > 30 s is reclaimed; frame failing 3× goes to DLQ", ENG-02 "stop Redis 10 s → all services recover, no manual step", ALM-02/03 (stateful per-vehicle logic needs ordered frames, R4 l.7). OPS-4: "nobody has to run a manual fix".

### A.1 Current design

Each worker holds a dedicated Postgres connection per owned vehicle and takes `pg_try_advisory_lock(hashtext('tm:'‖vehicle))` on the connection that writes that vehicle's data (D-ING-09). `ingest-1` prefers TA1 and `ingest-2` prefers TA2. A worker "takes over any vehicle whose lock has been free > 2 s" and releases a non-preferred vehicle "once that vehicle's preferred worker heartbeats again" (D-ING-10; the heartbeat is `hb:ingest:<consumer>` in Redis, D-ING-25). Takeover runs: lock → load state → warm up → `XAUTOCLAIM … 0` → pending before new (D-ING-11). A hung owner is stolen when a peer "sees an entry on a vehicle it does not own pending > 30 s" → `pg_terminate_backend` (D-ING-12). The owner runs a 30 s `XAUTOCLAIM` every 5 s (D-ING-26). On any DB error the worker drops all ownership and re-locks (D-ING-24).

**Failure behaviour.** On a crash the session ends, the lock frees, and the peer takes over 1–3 s later (R4 diagram). When the crashed worker comes back, a second handover (failback) follows, so a crash costs two warm-ups. A hang is stolen after 30 s. Redis outage: ownership lives in Postgres and is unaffected (R4 table). Postgres outage: every worker drops every lock and they race again afterwards.

**Findings on the current design**

- **A-F1 (defect): the steal trigger misses an owner that hangs with nothing pending.** D-ING-12 looks for "an entry … pending > 30 s". The owner's loop is read → COMMIT → XADD ui → **XACK** → read (D-ING-22). If the owner stops (SIGSTOP, deadlock, a blocking call in the limits poll or the ack-request poll) after XACK and before its next `XREADGROUP`, its PEL is empty. New entries stay *undelivered*, and undelivered entries are never "pending" (R4 l.16: XAUTOCLAIM and XPENDING see only the PEL). No entry ever goes idle, the steal never fires, and the vehicle stops until someone intervenes. `test_hung_owner_is_stolen` will usually still pass, because a worker blocked inside `XREADGROUP BLOCK 1000` gets entries pushed into its PEL. That is exactly why the hole would survive testing. Redis has a precise signal for this: `XINFO CONSUMERS` `idle` = "milliseconds … since the consumer's last **attempted** interaction (XREADGROUP, XCLAIM, XAUTOCLAIM)", with these semantics since 7.2.0 ([redis.io XINFO CONSUMERS](https://redis.io/docs/latest/commands/xinfo-consumers/)). We pin 8.10 (D-ING-07). A healthy owner calls `XREADGROUP` at least once per second (D-ING-08 `BLOCK 1000`), so an owner whose `idle` exceeds 30 s is hung, whether or not its PEL is empty.
- **A-F2 (complexity): ownership needs four timers and two stores.** The 1 s lock poll, the 2 s "free" timer (which needs a `pg_locks` read of a 64-bit advisory key to observe "free" without taking the lock), the Redis heartbeat with `EX 3` for release, and the 30 s steal. Release depends on Redis being up while ownership depends on Postgres, which goes against D-ING-09's own reason for choosing Postgres ("survives Redis restart").
- **A-F3 (observation): on one host, peer takeover mainly protects against *how* a worker dies.** Everything runs in one compose stack (D-ENG-03), so host loss takes Redis and Postgres down too. A restart policy covers a crash *inside* the container, but per D-ENG-11 "`docker kill` does not trigger the restart policy". The value of a peer is that recovery does not depend on how the process died. ING-07's planned evidence includes a `docker_kill` case (requirements.md ING-07).

### A.2 Alternatives

**A1. Single process plus supervisor.** One `gs-ingest` process with one asyncio task per vehicle (each task is its own consumer), `restart: unless-stopped`, and a `faulthandler.dump_traceback_later(20, exit=True)` watchdog re-armed every loop pass, which turns a hung loop into an exit (stdlib). Recovery always runs the startup protocol (own PEL from `0`, then `>`).
- ING-06: **fails** on the natural reading. "Run as ≥ 2 workers … kill a worker" (ING-06/07, ATP-05) reads as processes; one process killed takes both vehicles down. ING-07: passes (own PEL after restart). ING-08: passes (D-ING-26 ghost reclaim). ENG-02: passes.

**A2. One worker per vehicle by configuration** (R4's "a0" fallback, l.67). `ingest-1` has `VEHICLES=NYX-TA1`, `ingest-2` has `VEHICLES=NYX-TA2`. There is no takeover. The advisory lock stays only as a **start guard**: if `pg_try_advisory_lock` fails, exit non-zero, so a misconfiguration can never put two writers on one vehicle. Crash → restart policy → own PEL. Hang → faulthandler watchdog → exit → restart. Postgres error → exit (crash-only), so the restart rebuilds state.
- ING-06: passes literally. Both workers are in group `ingest` (A-R4-1) and each owns one vehicle, which is the same steady state as the current design. ING-07: passes for a crash inside the container (`kill -9 1`, D-ENG-11). With `docker kill` the vehicle stays down until a manual `up`, which **breaks OPS-4 "no manual fix"** if acceptance kills that way. ING-08: ghost reclaim passes. A SIGSTOP'd owner just stalls until SIGCONT, which is not a split-brain because nobody else writes. ENG-02: passes.

**A3. Blocking standby lock, no failback.** Each worker runs one task per vehicle, each on its own connection, calling the **blocking** `pg_advisory_lock`. The home vehicle is requested immediately, others after a 2 s start delay, so the home worker wins at startup. The standby already waits inside Postgres, and its call returns the moment the owner's session ends (an advisory lock is released "at session end, even if the client disconnects ungracefully", R4 l.28). There are no poll or free timers and no Redis heartbeat in ownership. The steal is kept, with the A-F1 trigger. There is no failback: after a crash the survivor keeps both vehicles until it is restarted itself.
- ING-06: passes at start. After an ATP-05 kill, load is no longer shared (ATP-05 lists ING-06), so the evidence must be captured before the kill. ING-07, ING-08, ENG-02: pass.

**H-A (recommended hybrid) = A3 plus a Postgres-only hand-back.** A task holding a **non-home** vehicle releases it (after acking its current batch) when `pg_locks` shows a session waiting (`granted = false`) on that key whose `application_name` is the vehicle's home worker. That check piggybacks on the existing 5 s reclaim tick (D-ING-26). When the home worker restarts, its blocking request becomes visible as that waiter and the vehicle goes back. Liveness, ownership and failback then all live in Postgres.

### A.3 Pass conditions per alternative

| Pass condition | Current | A1 single proc | A2 static | A3 blocking | H-A |
| --- | --- | --- | --- | --- | --- |
| ING-06 2 workers share load, order kept | ✓ | ✗ one process | ✓ (config) | ✓ at start; ~ after a kill | ✓ |
| ING-07 kill mid-stream, 0 lost | ✓ peer | ✓ restart | ~ ✓ only if killed inside the container | ✓ peer (ms) | ✓ peer (ms) |
| ING-08 idle > 30 s reclaimed | ✓ ghost; hang only if PEL non-empty (A-F1) | ✓ ghost; watchdog | ✓ ghost; watchdog | ✓ ghost; `idle` steal | ✓ ghost; `idle` steal |
| ENG-02 Redis stop 10 s | ✓ | ✓ | ✓ | ✓ | ✓ |
| OPS-4 no manual fix | ✓ | ✓ | ~ (`docker kill`) | ✓ | ✓ |

### A.4 Scores

| | Cov | Safe | Hrs | Test | Expl | Total |
| --- | --- | --- | --- | --- | --- | --- |
| Current | 5 | 4 | 2 | 3 | 3 | 17 |
| A1 single process + supervisor | 2 | 2 | 5 | 4 | 5 | 18 **DQ** (ING-06) |
| A2 static per-vehicle config | 4 | 3 | 4 | 4 | 5 | 20 |
| A3 blocking standby, no failback | 4 | 4 | 3 | 4 | 4 | 19 |
| **H-A** | 5 | 5 | 3 | 4 | 4 | **21** |

Why: Current Safe = 4 because of A-F1 and two handovers per crash. Its Hrs = 2 because the polled timers, the heartbeat-based release and the SIGSTOP test (≥ 30 s wall clock) take the most code and test time. Its Test = 3 because every ownership test waits on 1 s / 2 s / 3 s timers. H-A Safe = 5: takeover happens when Postgres frees the lock rather than on the next poll, no ownership decision depends on Redis, and the hang trigger also covers an empty PEL. A2 is the honest runner-up. It is simpler to explain and build, but its coverage depends on *how* the worker is killed, which the board, not us, will choose.

### A.5 Recommendation: hybrid H-A

- **D-ING-09**: keep.
- **D-ING-10** → "One task per (worker, vehicle), each on its own connection, calls the blocking `pg_advisory_lock`. The home vehicle is requested at once, others after a 2 s start delay. A task holding a non-home vehicle releases it, after acking its batch, when `pg_locks` shows its home worker waiting for it (checked on the 5 s reclaim tick)." Delete the "free > 2 s" timer and the heartbeat-based release. `hb:ingest:*` stays for the console only (D-ING-25, D-UI-15).
- **D-ING-11**: keep. It now runs when the blocking call returns. Its state load changes per C below.
- **D-ING-12** → trigger = "the lock holder's consumer has `idle` > 30 s in `XINFO CONSUMERS tm:<v> ingest` (Redis ≥ 7.2 semantics)". The rest (look up the pid in `pg_locks`, `pg_terminate_backend`) is unchanged. Add a test that SIGSTOPs the owner **between XACK and the next read** through a `CRASH_AT`-style hook (D-ENG-16), with `STOP` instead of exit.
- **D-ING-24** → scoped per task: on a DB error, close that connection (which releases its lock), back off, and return to the blocking acquire.
- **D-ING-26**: keep.
- README "how order is kept" (ING-06): "Each vehicle stream has one reader at a time. The reader holds a Postgres lock on the connection it writes with. The other worker waits on that lock and continues the moment the owner's session ends. A stuck owner (no read for 30 s) has its session killed."
- Fallback if Gate 3 day 2 overruns: A2, as R4 already says. It costs a test change (kill inside the container) and a written note on OPS-4.

---

## B. Command tracking

Requirements: CMD-01 "`isolate_valve` reaches VERIFIED when seen in `last_cmd_id`", CMD-02 "not received in 5 s or not verified in 15 s → FAILED with reason; both cases tested", CMD-03 (two-step + reason), CMD-04 (append-only audit, every command), CMD-05 (double command rejected within 2 s), UI-06 "all 4 states with times visible", ATP-03 "simulator paused 6 s and 16 s".

### B.1 Current design

The API validates the command (two-step D-CMD-06, interlock D-CMD-07), inserts it QUEUED, sets SENT before `XADD tc:` and marks FAILED SEND_ERROR on an XADD error (D-CMD-11). The simulator writes `{cmd_id, accepted, …}` to `tcack:<v>` and reports `last_cmd_id` in the next frame (D-SIM-06). The vehicle's **owner worker** reads `tcack:<v>` alongside `tm:<v>` with the `ingest` group, matches `last_cmd_id`, and "checks timeouts every loop pass (≤ 1 s resolution)" (D-CMD-05). Transitions are guarded `UPDATE … WHERE state IN (…)`, and "takeover loads open commands" (D-CMD-11). FAILED is terminal; a later echo writes LATE_VERIFICATION (D-CMD-04).

**Failure behaviour.** Owner crash: tracking pauses until takeover, then the new owner reloads open commands. Hang: tracking pauses up to the 30 s steal. Redis down: the API returns `BROKER_DOWN`. Postgres down: nothing is written.

**Findings on the current design**

- **B-F1 (defect, blast radius): a tracker fault can send innocent frames to the DLQ, on both workers in turn.** D-ING-22 puts the whole batch in "one DB transaction per `XREADGROUP` batch", and D-ALM-14 applies owner-side work "in its batch transaction". If tracker writes share that transaction (the design does not separate them), a tracker exception that depends on *state* (a bad open command row, a bug in the timeout arithmetic) aborts every batch. D-ING-04 then "re-run[s] one frame per transaction … retried 3× … then DLQ'd", so every frame of that vehicle ends in `tm:dlq` as POISON. "Takeover loads open commands" (D-CMD-11) carries the same bad state to the peer, which then fails the same way. D-ING-04's protection keys on *entries*, and this poison lives in *state*.
- **B-F2 (latent false FAILED): wall-clock timeouts can run before the evidence has been read.** After a takeover the new owner loads open commands (D-CMD-11) and must then drain the pending and backlog entries (D-ING-11), which can include the `tcack` and the frame with `last_cmd_id`. If a timeout check runs in a loop pass before the drain finishes, an executed command is marked FAILED NOT_RECEIVED and then LATE_VERIFICATION, which is a wrong answer to review Q3. The design does not state the order.
- **B-F3 (correctness depends on the transaction):** VERIFIED evidence reaches the tracker only through frames classed *new* (D-ING-17). If the tracker were moved into its own transaction without other changes, a crash between the frame COMMIT and the tracker COMMIT would redeliver the frame as DUP_OR_LATE, the tracker would never see `last_cmd_id`, and the command would end in a false NOT_VERIFIED with no LATE_VERIFICATION. Fixing B-F1 therefore requires durable evidence: `last_cmd_id` is already a `telemetry` column (R6 l.113).

### B.2 Alternatives

**B1. Tracker in the API.** The API already writes QUEUED and SENT, so it becomes the only writer of `commands`. It reads `tcack:<v>` with its own consumer group `cmd` (durable, XACK after update), takes `last_cmd_id` from the `tm` messages it already reads on `ui:events`, back-fills from `telemetry` after a restart (because `ui:events` is `MAXLEN ~ 3000`, D-UI-01), and runs an asyncio timer task every 250 ms. It pushes `cmd` events to SSE clients in-process.
- CMD-01..05, UI-06, ATP-03: pass. The trade-offs: ingest is fully isolated, but a tracker bug now takes the console and commanding down. UI-07's own ATP step "restart API" pauses tracking, which is safe only with the back-fill. The Redis ACLs change (D-ENG-17: `api` must read `tcack:*`).

**B2. A separate small `gs-cmdtrack` service over DB evidence.** The owner stores each `tcack` as a row (`command_ack`) in its batch, as plain vehicle → ground data. A singleton service runs one transaction every 250 ms: a pure `decide(cmd, ack, first_verify_frame, now)` over `commands`, `command_ack` and `telemetry.last_cmd_id`, followed by guarded UPDATEs, audit rows and an `XADD ui:events`.
- All pass conditions pass. Decisions come only from stored rows and `now`, so a late or restarted tracker reaches the same answer. The costs: a sixth service (compose, `/healthz`, image entry point, D-ENG-07), and as a singleton it has no failover beyond the restart policy (A-F3).

**B3. The owner is the single writer, including the send.** This mirrors D-ALM-14. The API validates and inserts a `command_request`; the owner XADDs `tc:` and tracks everything. That needs a QUEUED expiry (never send a command late after a takeover) and LISTEN/NOTIFY or ≤ 1 s polling for send latency.
- All pass conditions pass, but an ingest outage now also blocks *sending*, and ordinary sends gain up to 1 s of latency. It also makes the owner's blast radius larger (B-F1), not smaller.

**H-B (recommended hybrid) = B2's mechanism hosted in the owner.** Tracking stays where D-CMD-05 put it and inherits H-A's failover with no new service. It runs **after the frame COMMIT, in its own transaction**, and decides from **durable evidence** only:
- `tcack` entries → guarded `UPDATE commands SET RECEIVED/FAILED REJECTED` in the command transaction, then XACK (the row *is* the evidence store; poison rule D-ING-04 unchanged on that stream).
- VERIFIED and LATE_VERIFICATION → one indexed query: `telemetry WHERE vehicle = $v AND t_onboard >= min(open sent_at) − 1 s AND last_cmd_id = ANY($open)`, which takes the first frame per id.
- Timeouts are applied **only when caught up** (the last read was not a full batch, and the takeover drain is done; the worker already tracks `backlog`, D-ING-08). Evidence found within the window wins over the timeout in the same pass.
- `decide()` is pure (row values plus `now` in, transition out). Exceptions are caught and logged, counted in `/healthz` as `cmd_tracker_errors`, and never roll back frames or count as frame deliveries.

### B.3 Pass conditions per alternative

| Pass condition | Current | B1 API | B2 service | B3 owner sends | H-B |
| --- | --- | --- | --- | --- | --- |
| CMD-01 VERIFIED via `last_cmd_id` | ✓ | ✓ (back-fill needed) | ✓ | ✓ | ✓ |
| CMD-02 5 s / 15 s, both tested | ✓ (B-F2 risk after takeover) | ✓ | ✓ | ✓ | ✓ |
| CMD-03 two-step + reason | ✓ API | ✓ | ✓ | ✓ | ✓ |
| CMD-04 every command audited | ✓ | ✓ | ✓ | ✓ | ✓ |
| CMD-05 2 s interlock | ✓ API | ✓ | ✓ | ✓ | ✓ |
| UI-06 four times | ✓ | ✓ | ✓ | ✓ | ✓ |
| ATP-03 pause 6 / hold_exec 16 | ✓ | ✓ | ✓ | ✓ | ✓ |

### B.4 Scores

| | Cov | Safe | Hrs | Test | Expl | Total |
| --- | --- | --- | --- | --- | --- | --- |
| Current | 5 | 3 | 3 | 3 | 4 | 18 |
| B1 tracker in API | 5 | 3 | 3 | 4 | 4 | 19 |
| B2 separate service | 5 | 4 | 2 | 5 | 3 | 19 |
| B3 owner single writer incl. send | 5 | 3 | 2 | 3 | 4 | 17 |
| **H-B** | 5 | 4 | 3 | 5 | 4 | **21** |

Why: Current Safe = 3 because of B-F1 (state poison spreads through takeover) and B-F2. Its Test = 3 because the tracker is driven by the order of stream entries inside the worker loop, so ATP-03's branches need the simulator and a worker running. H-B Test = 5: every CMD-02 branch, including "evidence arrived but was processed late", becomes a table test of `decide()` with no Redis, no simulator and no sleeps, plus one integration test per ATP-03 case. All alternatives tie on Cov, because every pass condition lives in the API (CMD-03/05) or in rules any tracker location can follow. The choice is about failure isolation and tests, not coverage.

### B.5 Recommendation: hybrid H-B

- **D-CMD-05** → "Tracking runs in the vehicle's owner, **after the frame COMMIT, in its own transaction**. It applies `tcack` entries (then XACKs them), finds VERIFIED and LATE_VERIFICATION evidence in `telemetry.last_cmd_id`, and applies timeouts only when the owner is caught up. The decision is a pure `decide()`. A tracker error is logged and counted and never affects frame processing."
- **D-CMD-11**: drop "takeover loads open commands" (there is no in-memory command state any more). Keep the guarded updates, SENT-before-XADD and the QUEUED > 5 s rule.
- **D-ING-22**: state explicitly what the frame transaction contains: frames, `seq_state`, sequence events, alarm transitions, applied ACKs, and the engine snapshot (C). Nothing else.
- No change to D-CMD-01..04, 06..10 or D-SIM-06.

---

## C. Alarm state rebuild and replay equality

Requirements: ALM-06 "who, when and note stored for every transition", DAT-06 "replay from archive shows same alarms as live; REPLAY banner", ING-07 (no duplicated or missing alarm across a kill), UI-05 "leak → yellow → red → ack → clear". Spec OPS-6: "with the same alarms appearing as they did live". Review Q4: "Can I trust a replay to look exactly like what happened live?"

### C.1 Current design

The engine is a pure `step()` over pluggable checks (D-ALM-10). Every per-check debounced level change is logged as a `LEVEL` event. Rebuild "after takeover or for replay = seed from LEVEL events, then warm up the last K frames" with K = max(N, ROC window samples) (D-ALM-11, R8 l.105). ACKs are applied by the owner with `after_seq` (D-ALM-14). Replay reads archive or hot frames, "seeds state from `alarm_events`, runs the **same pure `step()`** as live, switches limits at the recorded `LIMITS_APPLIED` frame" (D-REP-01) and "overlay[s] live ACKs at their `after_seq`" (D-REP-03). Equality is tested by comparing transition keys (D-REP-05). The console resyncs on takeover (D-UI-18).

**Failure behaviour.** After a takeover, the new owner's state is correct only if the warm-up reproduces the persistence ring, the ROC window and every reset in it (GAP, SEQ_RESET, data-STALE per D-ALM-04, and the ring reset on `LIMITS_APPLIED`, R8 §7–8). Replay is "same alarms as live" only if every input is reproduced, including the seed, the ACK overlay, the limits timeline, and the *same code version*.

**Findings on the current design**

- **C-F1 (defect, measured): K = max(N, ROC samples) is too small.** The ring's last N raw levels each need a *full* ROC window ending at their own frame, so the warm-up needs **window_s plus N frames** of history (with one frame of margin for stamping jitter). That is a sum, not a max. Experiment (§C.6, engine following R8: deadband on the debounced level, all-N persistence, OLS ROC over 1 s with the ≥ 80 % rule, ms stamping jitter): over 3,340 takeover points, **16 continuations (0.48 %) differ from live with K = 10**. Both K = N + W + 1 = 14 and a time-based window of `window_s + (N+1)/rate` give **0 mismatches**. A design fixed by reasoning about one boundary has other boundaries to get right (resets at the window edge, LIMITS_APPLIED inside it). Each is a place where a takeover silently diverges from live.
- **C-F2 (truthfulness): recomputed replay can differ from what operators actually saw.** Replay recomputes alarms with the current code. After any code change, or after a live defect (C-F1 is one), the replay shows alarms that did *not* happen. For anomaly investigation (OPS-6, three days later), the truthful answer to Q4 is the recorded transitions. The never-pruned `alarm_events` (D-DB-09) already holds them, including ACK rows with who and note (ALM-06).
- **C-F3 (cost):** the replay seed loader needs LEVEL rows at or before the start, open alarms as of the start, the epoch, and the limits timeline with content by hash (R7 l.119). Together with the ACK overlay and the LIMITS switch inside replay, this is the largest piece of replay code. Every part of it exists only to *re-derive* rows the database already holds.

### C.2 Alternatives

**C1. Engine-state snapshot per batch, recomputed replay.** In the same transaction as the frames, the owner upserts `engine_state(vehicle PK, last_seq, epoch, limits_sha256, schema_version, state jsonb)`. R8 already makes every check state "frozen, JSON-serialisable" (R8 l.96). Takeover and restart load that row, so the state is exact by construction (0 mismatches in §C.6, as expected). Replay keeps recomputing and seeds from a periodic `engine_snapshot` history row (for example every 60 s, never pruned, about 6 MB/day) followed by a silent fast-forward to `t_from`.
- ALM-06, ING-07, UI-05: pass. DAT-06: passes, but replay still needs the ACK overlay and the limits timeline, and C-F2 remains.

**C2. Event-sourced replay, LEVEL warm-up kept for takeover.** Replay merges the archived frames (hash-checked, D-REP-01's reader) with the recorded `alarm_events` and `events` rows of the window, ordered by `(t_onboard, seq)` and paced by `t_onboard` (D-REP-02). The alarm state at `t_from` is the latest recorded transition per alarm before `t_from` (one query). Nothing is recomputed. ACKs appear as recorded rows.
- DAT-06: passes by construction ("same alarms as live" is literally the live rows). The "from archive" wording is met by the frames. If a board reads DAT-06 as "re-run the logic", the determinism test (see H-C) answers that. ALM-06: passes, and the who and note show up in replay too. Takeover is unchanged, so C-F1 needs its K fix.

**C3. Re-feed from NOMINAL, no LEVEL log and no snapshot** (the simplest possible rebuild).
- **Fails.** §C.6 measures **28–29 % of takeover continuations differing from live**, because hysteresis remembers inside the deadband (R8 l.105). ING-07 ("no alarm duplicated") and DAT-06 fail.

**H-C (recommended) = C1 for takeover plus C2 for replay.** The snapshot row makes takeover exact with no warm-up reasoning. Replay shows the recorded truth. Recomputation is kept as a **test**, not as the product path.

### C.3 Pass conditions per alternative

| Pass condition | Current | C1 snapshot | C2 event-sourced | C3 NOMINAL | H-C |
| --- | --- | --- | --- | --- | --- |
| ALM-06 who/when/note per transition | ✓ | ✓ | ✓ (also in replay) | ✓ | ✓ |
| ING-07 no dup or missing alarm after a kill | ~ (C-F1 until K fixed) | ✓ exact | ~ (C-F1 until K fixed) | ✗ 28–29 % | ✓ exact |
| DAT-06 replay = live alarms, banner | ✓ if every input is reproduced and code unchanged | ✓ same caveat | ✓ by construction | ✗ | ✓ by construction + determinism test |
| UI-05 leak → … → clear, one alert | ✓ | ✓ | ✓ | ~ | ✓ |

### C.4 Scores

| | Cov | Safe | Hrs | Test | Expl | Total |
| --- | --- | --- | --- | --- | --- | --- |
| Current (LEVEL + warm-up, recompute) | 4 | 3 | 2 | 4 | 3 | 16 |
| C1 snapshot + recompute | 5 | 4 | 3 | 5 | 4 | 21 |
| C2 event-sourced replay + LEVEL takeover | 5 | 4 | 4 | 4 | 4 | 21 |
| C3 NOMINAL re-feed | 2 | 2 | 5 | 4 | 4 | 17 **DQ** |
| **H-C** | 5 | 4 | 4 | 5 | 5 | **23** |

Why: Current Cov = 4 because of the measured C-F1. Its Safe = 3 because of C-F1 and C-F2. Its Hrs = 2 because LEVEL logging, the warm-up, the seed loader, the ACK overlay and the limits switching are all required. H-C Safe = 4, not 5, because a new risk appears: a snapshot written by older code whose state shape changed must be detected (`schema_version`) and falls back to an approximate rebuild. H-C Expl = 5: "a new owner loads the engine's saved state, written in the same transaction as the frames; a replay shows the stored frames and the stored alarm rows, exactly what was on screen."

### C.5 Recommendation: replace with H-C

- **D-ALM-10**: each `Check` declares a frozen `State` dataclass. A generic JSON (de)serializer is keyed by `kind`. Add a property test: serialize at every batch boundary, reload, continue, and the result equals the uninterrupted run (this is §C.6's `snapshot` arm).
- **D-ALM-11** → "The owner upserts `engine_state` in every batch transaction. Takeover and restart load it. If `schema_version` differs: open alarms from `alarms`, checks from NOMINAL, plus an `ENGINE_STATE_RESET` event (a known approximation, documented with §C.6's number)." Delete `LEVEL` events and the K-frame warm-up. *If the owner keeps D-ALM-11 anyway, the minimum fix is the warm-up window = `window_s + (N+1)/rate` of `t_onboard`, not K = max(N, W).*
- **D-REP-01** → "Replay merges archived frames (hash-checked, or hot) with the recorded `alarm_events` and `events` of the window, by `(t_onboard, seq)`. Alarm state at `t_from` = the latest recorded transition per alarm before `t_from`. Frames are annotated with the current severity per parameter. Nothing is recomputed."
- **D-REP-03**: satisfied by construction (ACK rows are recorded transitions). Keep `after_seq` in D-ALM-14 for the determinism test.
- **D-REP-05** → two tests. (a) `replay(window) == recorded rows` for the same window, from Parquet and from hot. (b) **Determinism**: run `step()` from the initial state over a recorded test run, with ACKs at `after_seq` and limits at `LIMITS_APPLIED`; it must reproduce the recorded transition keys. Test (b) is what still proves "the logic would give the same alarms".
- **D-DB-09**: unchanged (never prune `alarm_events`, `events`, `limits_versions`). v1+: export each vehicle-hour's `alarm_events` next to its Parquet file, so the archive is self-contained.
- **§3.2 DDL**: remove `'LEVEL'` from `alarm_events.kind`, add `engine_state`, and grant `SELECT, INSERT, UPDATE` on it to `gs_app`.
- Unchanged: D-ALM-14 (ACK path), D-UI-18 (resync), D-REP-02/04/06.
- requirements.md DAT-06 evidence should be updated by the owner to name tests (a) and (b). This review does not edit it.

### C.6 Experiment (throwaway)

`/private/tmp/claude-502/-Users-malek-try-nyx/6335b422-9524-46f4-8e9d-feb69a42ebfe/scratchpad/alt/rebuild_exp.py` (stdlib only, `python3 -I`). Signal: 120 s per seed at 10 Hz, σ = 0.02 kPa, leak −0.4 kPa/s, partial repressurisation into the deadband (98.6 kPa, inside the 1.1 kPa deadband), then full repressurisation, with ±2 ms stamping jitter. Limits are those in design §3.3. For each seed the live engine runs uninterrupted. Then at every 7th frame a takeover is simulated, the engine is rebuilt with each method, and the continuation is compared with the live transition list.

```
takeover points tested: 3340 (20 seeds x every 7th frame), K=10
  snapshot  mismatching continuations: 0  (0.00 %)
  level_k   mismatching continuations: 16  (0.48 %)   # D-ALM-11 as written
  level_t   mismatching continuations: 0  (0.00 %)   # warm-up = window_s + (N+1) frames by time
  nominal   mismatching continuations: 979  (29.31 %)
```
`rebuild_exp_k14.py` (same code with K = N + W + 1 = 14): level_k 0, nominal 939 (28.11 %).

Limits of the experiment: one parameter, two checks (level, ROC). No GAP, SEQ_RESET, LIMITS_APPLIED or ACK inside the warm-up window, so those are extra boundaries the real LEVEL warm-up must also handle, and the snapshot does not have to.

---

## 4. What this review does not change

The frame path stays: XACK after COMMIT, the idempotent key, poison handling (D-ING-02/04/13). So do the hazardous two-step and the interlock (D-CMD-06/07), the ACK path (D-ALM-14) and console resync (D-UI-18). These were outside the three hotspots, or they were checked here and hold under every alternative.

## 5. Hours delta detail

| Hotspot | Removed | Added | Net |
| --- | --- | --- | --- |
| A | lock poll + "free > 2 s" via `pg_locks` + heartbeat release + its test (−0.5) | blocking task per vehicle + waiter hand-back + `idle` trigger + STOP-after-XACK test (+0.25) | **−0.25 h** |
| B | in-memory command state, takeover load, ordering of tcack before frames (−0.25) | separate transaction, evidence query, `decide()` table tests (+0.25) | **±0 h** |
| C | LEVEL logging + warm-up (−0.5), replay seed loader + ACK overlay + limits switch (−0.5) | snapshot row + (de)serializer + roundtrip test (+0.25), determinism test harness (+0.25) | **−0.5 h** |
| **Total** | | | **≈ −0.75 h** (31.75 → ≈ 31.0) |
