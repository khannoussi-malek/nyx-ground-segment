# Requirements trace

Spec §4, one row per requirement. **Decisions** point to [design.md](design.md); **Planned evidence** is the test (or inspection) that will prove it, named now so it gets written next to the feature, not at Gate 4. **Status** is filled at Gate 5 (PASS / FAIL / PARTIAL + link) and mirrored in `docs/atp-report.md`.

Method: T test · D demonstration · I inspection · A analysis. Prio: M must · S should.
**Rule (D-ENG-14):** anything we build ships with its test in the same commit, including code that serves no single requirement (simulator, migrations, health checks); CI fails under 80 % coverage (D-ENG-15).
Test locations: `tests/unit/` (pure functions), `tests/integration/` (real Redis + Postgres + MinIO from the compose file), `e2e/` (Playwright, TypeScript), `perf/` (measurement scripts, raw CSV to `docs/perf/`).

## 4.1 Ingest and processing

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ING-01 | Two test articles stream in parallel for 2 h with zero frames lost. | T, D | M | D-ING-01, D-SIM-05 | `integration/test_ingest_store.py::test_two_vehicles_no_loss`; `perf/soak_count.py` (sent vs stored per `(epoch, seq)`) over the 2 h ATP-01 soak | 01 | |
| ING-02 | A malformed frame goes to `tm:dlq` with its reason and ingest carries on. | T | M | D-ING-05, 18–20 | `unit/test_frame_model.py` (NaN, `"5"`, naive time, bad UUID, broken JSON); `integration/test_dlq.py::test_malformed_to_dlq_and_continue` | 05 | |
| ING-03 | A duplicated frame is stored once. | T | M | D-ING-13, 14 | `unit/test_seq_tracker.py::T3`; `integration/test_ingest_store.py::test_duplicate_stored_once` | 05 | |
| ING-04 | A 10 s dropout produces one GAP event with 100 ± 1 lost frames. | T | M | D-ING-14–16 | `unit/test_seq_tracker.py::T2,T4,T8`; `e2e/atp04-los.spec.ts` (GAP count shown) | 04 | |
| ING-05 | A simulator restart resumes ingest within 2 s and logs SEQ_RESET, losing nothing. | T | M | D-ING-13–15, D-SIM-03 | `unit/test_seq_tracker.py::T6,T7`; `integration/test_seq_reset.py` (restart sim container, time to first new frame < 2 s) | 05 | |
| ING-06 | Two workers in one group share the load and keep per-vehicle order. | T, A | M | D-ING-01, 09–11 | `integration/test_ownership.py::test_two_workers_share_and_keep_order` (+ naive negative control must fail), `::test_hand_back_when_home_worker_waits` (load shared again after the home worker returns); README "how order is kept" (one reader per vehicle, blocking lock, standby continues when the owner's session ends) | 05 | |
| ING-07 | A frame is acked only after it is stored, so killing a worker loses nothing. | T | M | D-ING-02, 03, 06, 07, 09–11, 13, 17, 22, 24, D-ALM-10, 11, D-ENG-11 | `integration/test_ownership.py::test_kill_owner_mid_stream[before_commit, after_commit_before_ack, docker_kill, kill_9_self_restart]` (0 lost by seq count; alarm keys equal an uninterrupted run, none duplicated or missing), `::test_db_error_exits_without_ack` (owner's backend terminated → no XACK, exit, restart, 0 lost); `unit/test_engine_state.py::test_snapshot_roundtrip_equals_uninterrupted`; `integration/test_stream_trim.py::test_trim_keeps_unacked` (§3.6) | 05 | |
| ING-08 | Entries idle > 30 s are reclaimed and a frame failing 3× goes to the DLQ. | T | M | D-ING-04, 05, 12, 26, D-ENG-16 | `integration/test_dlq.py::test_poison_after_3`, `::test_crash_every_time_frame_to_dlq` (other vehicle keeps flowing); `integration/test_ownership.py::test_reclaim_idle_30s` (ghost consumer), `::test_hung_owner_is_stolen[sigstop_in_read, sigstop_after_xack]` (second case: `STOP_AT=after_xack`, empty PEL, stolen on consumer `idle` > 30 s) | 05 | |
| ING-09 | p95 from `t_onboard` to stored is under 200 ms at 2 × 10 Hz. | A | M | D-ING-22, 23, D-PERF-01, 05, 07 | `perf/run_n.py` → `docs/perf/run-n-*.csv`, table in atp-report; `tests/unit/test_perf_stats.py` proves the percentile code | 08 | |
| ING-10 | At 2 × 100 Hz for 5 min the lag stays under 1 s. | A | S | D-PERF-05, 06 | `perf/run_p.py` → `docs/perf/run-p-*.csv` | 08 | |

## 4.2 Limits and alarms

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ALM-01 | Each value is classed nominal / yellow / red, with on-limit values going to the worse band. | T | M | D-ALM-01, D-SIM-11 | `unit/test_checks_limit.py::test_boundaries` (the 16-row table in R8) | 02 | |
| ALM-02 | Hysteresis turns ±1 % noise around a limit into one alarm (one RAISE). | T | M | D-ALM-02, D-SIM-11 | `unit/test_checks_limit.py::test_noise_one_alarm[pct_of_span, pct_of_limit]` (passes under both readings) | 02 | |
| ALM-03 | A single-frame spike does not alarm when persistence N = 3. | T | M | D-ALM-03, 04 | `unit/test_engine.py::test_spike_no_alarm`, `::test_gap_resets_counters_not_alarm` | 02 | |
| ALM-04 | A 0.4 kPa/s leak alarms on rate of change before the absolute limit. | T | S | D-ALM-05, D-SIM-08 | `unit/test_checks_roc.py::test_leak_alarms_before_yellow` | 02 | |
| ALM-05 | An alarm is raised within 300 ms (p95) of its triggering frame. | A | M | D-PERF-01, 04, 05, D-SIM-07 | `perf/alarm_latency.py` (sim `step` → 120 alarms in run N; report 20 and 60+) | 08 | |
| ALM-06 | Every ACTIVE → ACKED → CLEARED transition stores who, when and the note. | T | M | D-ALM-06–08, 14, D-ING-17, D-SIM-10, D-REP-03 | `unit/test_alarm_lifecycle.py` (state table incl. RTN_UNACK, re-alarm on escalation); `integration/test_alarm_ack_api.py::test_ack_then_rtn_is_cleared`, `::test_ack_after_seq_is_owner_seq`; `integration/test_replay_equality.py::test_replay_equals_recorded` (recorded ACK rows with who and note shown in replay) | 02 | |
| ALM-07 | A new check type is one new class plus registration, with no worker edit. | I | M | D-ALM-10 | Inspection: README section + `unit/test_check_registry.py::test_new_check_file_is_registered` | INSP | |
| ALM-08 | An edited limits file is live within 5 s and alarms carry its hash. | T | S | D-ALM-12, D-DB-09, 11 | `integration/test_limits_reload.py` (incl. invalid file at start → fallback); limits edit step inside `e2e/atp02-leak.spec.ts` | 02 | |

## 4.3 Commanding

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CMD-01 | Every command is tracked QUEUED → SENT → RECEIVED → VERIFIED. | T | M | D-CMD-01, 05, 11, D-SIM-06, 10 | `unit/test_command_state.py`, `unit/test_cmd_decide.py` (VERIFIED from `telemetry.last_cmd_id`, ack before or after the echo); `integration/test_commands_api.py::test_isolate_valve_verified` | 02 | |
| CMD-02 | No receipt in 5 s or no verification in 15 s marks the command FAILED with a reason. | T | M | D-CMD-02–05, 11, D-SIM-06, 07, 10 | `integration/test_commands_api.py::test_not_received_5s` (sim `pause 6`), `::test_not_verified_15s` (sim `hold_exec 16`), both expecting FAILED then LATE_VERIFICATION; `::test_monotonic_transitions`, `::test_queued_never_sent_fails_not_received`; `unit/test_cmd_decide.py` (table test of every branch incl. evidence in the window processed late, no timeout while not caught up); `integration/test_cmd_tracker.py::test_tracker_error_never_touches_frames`, `::test_no_false_failed_after_takeover` | 03 | |
| CMD-03 | A hazardous command can't be sent without a second confirmation and a reason. | T, D | M | D-CMD-06, 09, 10 | `integration/test_commands_api.py::test_hazardous_requires_confirm_and_reason`, `::test_confirm_hash_mismatch`; `e2e/hazardous.spec.ts` | 06 | |
| CMD-04 | Every command and ack is in an append-only audit log with user, time and reason. | I, T | M | D-CMD-08, D-ENG-08, 17 | `integration/test_audit_append_only.py` (UPDATE/DELETE/TRUNCATE fail); `::test_audit_complete` (N commands + M acks → exact rows); `integration/test_redis_acl.py::test_direct_tc_write_denied` | 06 | |
| CMD-05 | The same command from two operators within 2 s rejects the second with a message. | T | S | D-CMD-07, D-ENG-13 | `integration/test_commands_api.py::test_duplicate_within_2s` (users `fc1`, `fc2`), `::test_hazardous_lock_ignores_args` | 06 | |

## 4.4 Operator console

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| UI-01 | Live values appear within 1 s (p95) of the frame. | A | M | D-UI-01, 12, 14, D-PERF-02 | `perf/ui_latency.spec.ts`: per frame, server half `srv_ms` (VM clock) + page half `paint − receipt` (`performance.now()`); raw samples and p95; localhost transit stated as excluded | 08 | |
| UI-02 | Values go STALE at 2 s ± 0.5 s, the vehicle shows LOS, and both recover on their own. | T | M | D-UI-04–06, 15, D-ING-21, 25, D-ALM-04, D-SIM-07 | `e2e/atp04-los.spec.ts` (STALE timing on every row, LOS, recovery); `ui/src/freshness.test.ts` (age function: old `t_onboard` + fresh `t_received` → STALE); `unit/test_frozen_detector.py` (all analog values identical ≥ 2 s → FROZEN); `integration/test_resync.py` checks snapshot and `tm` messages carry `t_onboard` | 04 | |
| UI-03 | No state is shown by colour alone, nominal is grey, contrast passes WCAG AA. | I | M | D-UI-06, 07 | Inspection + `ui/src/palette.test.ts` (contrast ratios computed) | INSP | |
| UI-04 | Any parameter charts from 2 min to 24 h; 24 h loads in < 2 s with a min/max band. | T, A | M | D-UI-11, D-DB-02, 04, D-ARC-05 | `integration/test_downsample.py::test_24h_under_2s`; `e2e/chart.spec.ts` | 07 | |
| UI-05 | The alarm list supports ack and filters, and a new red alerts once. | T | M | D-UI-08, 09, 18, D-ALM-06, 08, 09, 14 | `e2e/atp02-leak.spec.ts` (leak → yellow → red → ack → clear, `alerts === 1`); `integration/test_resync.py::test_resync_after_crash` (crash after COMMIT before publish → alarm and beep still delivered) | 02 | |
| UI-06 | Each command shows its state timeline with all four times. | D | M | D-UI-10, D-CMD-02, 08 | `e2e/atp02-leak.spec.ts` (timeline step) + demo | 02 | |
| UI-07 | After an API restart the UI recovers in < 5 s and shows a banner while disconnected. | T | M | D-UI-01–04, 15, 18 | `e2e/reconnect.spec.ts` (from `docker compose restart api` to data flowing < 5 s; banner seen from the first `error`; the new connection starts with a snapshot) | 05 | |
| UI-08 | All times are UTC with `Z`. | I | M | D-UI-13, 16, D-SIM-02, D-ENG-18 | `unit/test_time_format.py` (API JSON, CSV, report, logs all end in `Z`); `ui/src/time.test.ts`; CI grep for `toLocale` | INSP | |

## 4.5 Test sessions, data and archive

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DAT-01 | Named sessions start and stop per vehicle and store conductor, start and end. | T | M | D-DB-07, D-UI-17 | `integration/test_sessions.py::test_start_stop`, `::test_one_active_per_vehicle` | 01 | |
| DAT-02 | A session report gives duration, min/max/avg per parameter, alarms, commands, gaps and a CSV. | T | M | D-DB-06, 10, D-ING-16, D-UI-17 | `integration/test_sessions.py::test_report_contents`, `::test_report_pending_until_watermark`, `::test_report_frozen` | 01 | |
| DAT-03 | 24 h of hot data; 1 h of one parameter returns in < 300 ms. | A | M | D-DB-01, 02, 05 | `perf/query_time.py` (API round trip) | 08 | |
| DAT-04 | Each vehicle-hour is archived to MinIO as Parquet + `.sha256`. | T | M | D-ARC-01, 03, 06, 07, 09, D-ENG-16 | `integration/test_archiver.py::test_hour_archived_with_sidecar` (backfilled hour, eligible at once; no injected clock) | 07 | |
| DAT-05 | Hot data is deleted only after the archive re-read matches; a killed upload loses nothing. | T | M | D-ARC-02–05, 10, D-ENG-16 | `integration/test_archiver.py::test_kill_mid_upload_then_resume` (`CRASH_AT=upload`: no `archive_object` row, next run rebuilds and re-uploads), `::test_delete_only_after_verify` (`HOT_RETENTION_H=0`), `::test_row_count_mismatch_rolls_back` | 07 | |
| DAT-06 | Any past window replays at 1× or 10× with the same alarms as live and a REPLAY banner. | T, D | M | D-REP-01–06, D-ALM-04, 10, 14, D-DB-09, D-UI-16, D-ENG-16 | `integration/test_replay_equality.py::test_replay_equals_recorded[parquet, hot]` (replay of a window == the `alarm_events`/`events` rows recorded live, incl. an ack-then-escalate window and a data-STALE window); `::test_recompute_reproduces_recorded_keys` (determinism: `step()` from the initial state over a recorded run, ACKs at `after_seq`, limits at `LIMITS_APPLIED` → same transition keys); `e2e/replay.spec.ts` (banner, speed) | 07 | |

## 4.6 Engineering, delivery and maintainability

| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ENG-01 | A fresh clone runs the demo with `make up`, first try. | D | M | D-ENG-02, 03, 09, 12, 13 | INSP-01 on a clean machine/VM; CI e2e job does the same | INSP | |
| ENG-02 | All services recover from a 10 s Redis stop with no manual step. | T | M | D-ING-07, 08, 24, D-SIM-05, D-ENG-03, 11 | `integration/test_redis_restart.py` (stop 10 s, 0 lost, all `/healthz` ok after) | 05 | |
| ENG-03 | Automated tests exist at unit, integration and end-to-end level. | T | M | D-ENG-05, D-SIM-09 | The three test folders + CI | INSP | |
| ENG-04 | CI runs lint, mypy, tsc and all tests on every push. | I | M | D-ENG-06 | `.github/workflows/ci.yml` + green run on the release tag | INSP | |
| ENG-05 | Every service exposes `/healthz` and writes JSON logs with vehicle and seq. | I | S | D-ENG-07, D-ARC-10, D-ING-25 | Inspection + `integration/test_health.py` (incl. archiver `degraded` and ingest `cmd_tracker_errors`) | INSP | |
| ENG-06 | A new team member adds a parameter in under 15 min using the guide. | D | S | D-DB-03, 11, D-ING-18–20, D-CMD-09 | `docs/dev-guide.md` timed run, recorded; automated: `tests/meta/test_add_parameter.py` (D-ENG-20) | INSP | |
| ENG-07 | History is small commits with clear messages. | I | S | D-ENG-10 | `git log` inspection; automated: `scripts/check_commits.py` in CI | INSP | |
| ENG-08 | README covers run, architecture, decisions + why, limitations, next steps. | I | M | §6 deliverables | README review against this list; automated: `tests/meta/test_readme_sections.py` | INSP | |
| ENG-09 | Python is typed and mypy-clean, with no hard-coded hosts. | I | M | D-ENG-04, 09, 12, D-ARC-08 | `mypy --strict` in CI; grep for host literals in CI; `tests/meta/test_no_host_literals.py` | INSP | |
| ENG-10 | Backend in Python, UI in React + TypeScript. | I | M | D-ENG-01, D-UI-12 | Inspection; `tests/meta/test_stack.py` | INSP | |
