# Hazard list for the test-campaign ground segment (candidate)

Scope and status: a short list of what Nyx can cause or fail to prevent during a test campaign, with the controls
already built and the gaps. It supports [criticality.md](criticality.md) and NPR 7150.2D SWE-052 (software
requirements to system hazards). The list is a proposal from research notes
[R24](../research/R24-command-verification-pus.md) and [R25](../research/R25-software-assurance-gap.md). **To be
confirmed by the owner / system hazard analysis.**

Why a table here and not a column in `requirements.md`: the requirements table has eight columns and a meta test
that parses it (`tests/meta/test_requirements_trace.py`), so a column would touch every row and the test. This
section keeps the trace in one place. `tests/meta/test_design_trace.py` checks that every requirement and decision
named here exists and that every test file cited (and test name, when given) exists.

Each hazard has a `Tests:` line: real test files (path, and `::name` where one test is the evidence). They show a
control is exercised, not that the hazard is closed.

Severity is the effect on the test article or the test, not on crew. It is a candidate rating only.

## Hazards

### H-1 Unintended hazardous command (vent_cabin)

- Cause: operator error, a repeated click, a replayed or stolen confirm token, a script bypassing the console, a
  wrong `tc` entry.
- Effect: irreversible loss of the test article's cabin atmosphere.
- Controls built: single-use user-bound confirm token and reason, both checked by the server (D-CMD-06, CMD-03); the
  vehicle, command and arguments are bound into the token's hash. The typed vehicle ID is a browser-only step
  against habituation (D-UI-20); double-command
  interlock (D-CMD-07, CMD-05); argument validation against `commands.yaml` (D-CMD-09); authenticated session
  identity, never from the body (D-CMD-10); append-only audit (D-CMD-08, CMD-04).
- Tests: `tests/integration/test_commands_api.py::test_confirm_token_single_use_and_user_bound`,
  `tests/integration/test_commands_api.py::test_hazardous_requires_confirm_and_reason`,
  `tests/integration/test_commands_api.py::test_duplicate_within_2s`, `e2e/hazardous-confirm.spec.ts`.
- Gaps: the server does not check the typed vehicle ID, so a script that holds a valid token bypasses it (the token
  and the args hash still bind it to one user and one vehicle; R24 F6); no prerequisite check (R24 F2; D-EXT-08 designed, not built); one telecommand fires the hazard, with no
  arm/enable step (R24 F3, ECSS-E-ST-70-11C §5.5.2b); one person can confirm alone (R24 F5, owner decision).

### H-2 Missed or late RED alarm

- Cause: persistence or hysteresis hides a real excursion; the engine falls behind; the alert is lost on takeover.
- Effect: the test conductor does not react to a limit violation.
- Controls built: red needs no persistence count of its own (D-ALM-03); rate-of-change check (D-ALM-05, ALM-04);
  only RED alerts audibly and once (D-ALM-09, D-ALM-08, UI-05); engine state survives a worker kill (D-ALM-11,
  ING-07); latency measured, p95 under 300 ms (ALM-05); every classification is a pure function (D-ALM-10).
  Planned, not built here: EXT-03 property tests P3/P4 (branch ext03-property-chaos, not merged).
- Tests: `tests/unit/test_alarm_lifecycle.py`, `tests/unit/test_checks_roc.py::test_leak_alarms_before_yellow`,
  `tests/integration/test_limits_reload.py`.
- Gaps: none known to the author, but not analysed. The 300 ms figure is a requirement measured on the reference
  machine, not a guarantee under overload (ING-10 covers 100 Hz for 5 min only); persistence and hysteresis
  settings were tuned on the simulator's faults, not on a real vehicle; a lost alert on takeover is covered by
  the ING-07 tests, not by a hazard analysis.

### H-3 False or hidden loss of signal, stale value shown as fresh

- Cause: frozen sensor values, a stalled ingest, a heartbeat that outlives its worker, a UI clock error.
- Effect: the operator trusts old data, or ignores a healthy vehicle.
- Controls built: STALE at 2 s and LOS shown on every value (UI-02, D-ALM-04); FROZEN detector (D-ING-21); per-vehicle
  heartbeat written from inside the read loop, so a stuck loop stops it (D-ING-25); gap counted and shown (ING-04,
  D-ING-16); stale and LOS recover on their own (UI-02).
- Tests: `tests/unit/test_frozen_detector.py`, `tests/unit/test_los_clock.py`, `ui/src/state/freshness.test.ts`,
  `ui/src/state/banners.test.ts`, `e2e/atp04-los.spec.ts`.
- Gaps: none known to the author, but not analysed. The freshness clock runs in the browser (D-UI-05, seeded with
  the server's `age_ms`): a browser clock that is paused or throttled (a background tab) is not tested as a cause.
  A bad vehicle clock is H-10.

### H-4 Wrong command outcome shown

- Cause: VERIFIED means "started", not "done"; a failed execution has no field in `tcack`; a late echo after a
  timeout.
- Effect: the operator believes a hazardous or corrective command took effect, or that it did not.
- Controls built: monotonic states and terminal FAILED with late evidence kept beside it (D-CMD-04, D-CMD-11,
  CMD-02); outcome text for each case (D-CMD-02); timeline shows all four times (UI-06).
- Tests: `tests/integration/test_cmd_tracker.py::test_late_ack_after_not_received`,
  `tests/integration/test_commands_api.py::test_not_verified_15s`, `ui/src/components/commands.test.tsx`.
- Gaps: no completion or effect check (R24 F1); `tcack` cannot carry an execution failure (R24 F4, cheap now,
  costly after a bench ICD is frozen).

### H-5 Lost or duplicated telemetry

- Cause: producer or consumer crash, Redis outage, redelivery, a malformed frame.
- Effect: a hole or a repeat in the record; a wrong alarm.
- Controls built: ack only after commit (D-ING-02, ING-07); store key makes a duplicate a no-op (D-ING-13, ING-03);
  producer buffer during a Redis outage (D-SIM-05, ENG-02); poison frames to the DLQ with the reason (D-ING-05,
  D-ING-27, ING-02); gaps counted, not hidden (ING-04, ING-01).
- Tests: `tests/integration/test_ingest_store.py::test_duplicate_stored_once`,
  `tests/integration/test_dlq.py::test_malformed_to_dlq_and_continue`, `tests/unit/test_seq_tracker.py`.
- Gaps: none known to the author; no fault-injection of a disk-full or a partial Postgres write is cited here.

### H-6 Wrong limit set or command definition applied

- Cause: a bad or half-edited `mission/` file goes live.
- Effect: alarms judge against the wrong band, or a command is allowed with a wrong range.
- Controls built: the three files validated as one set, the reload refused on any error (D-EXT-01, EXT-01); band
  order checked at load (D-ALM-01); the sha256 of the limit set stored on every alarm and a LIMITS_APPLIED event
  (D-ALM-12, ALM-08); limits events are never pruned (D-DB-09).
- Tests: `tests/integration/test_limits_reload.py::test_invalid_reload_keeps_old_set`,
  `tests/integration/test_limits_reload.py::test_edit_live_within_5s_and_alarm_carries_hash`.
- Gaps: no second-person review of a mission change; the change is traceable, not approved.

### H-7 Corrupt archive shown as truth in replay or export

- Cause: bit rot or a partial upload in object storage.
- Effect: a post-test analysis built on altered data.
- Controls built: hash on upload, full re-read, content check, row written only after verification (D-ARC-02,
  D-ARC-03, D-ARC-04, DAT-04); hot data deleted only after verification (D-ARC-05, DAT-05); rolling scrub raises
  ARCHIVE_CORRUPT (D-ARC-11); replay equals recorded rows (D-REP-01, D-REP-05, DAT-06).
- Tests: `tests/integration/test_archiver.py::test_delete_only_after_verify`,
  `tests/integration/test_replay_api.py::test_corrupt_hour_409`, `tests/integration/test_replay_equality.py`.
- Gaps: the scrub detects only; there is no repair (D-ARC-11).

### H-8 Replay mistaken for live data

- Cause: the operator looks at a replay page (or an export) and acts as if the vehicle were doing it now.
- Effect: a command or a decision based on a past state.
- Controls built: REPLAY is purple and used for nothing else (D-UI-07); the replay page has a purple frame and a
  REPLAY banner that cannot be dismissed (D-UI-16); replay accepts no ACK and never sounds (D-REP-03); the live
  alarm list, beep and banners keep running beside it (D-REP-04); STALE and LOS run on replay time (D-REP-06).
- Tests: `ui/src/components/replayPage.test.tsx` ("banner shows the live counter and cannot be dismissed"),
  `ui/src/components/replayPage.test.tsx` ("replay alarms show without an ACK button"), `e2e/replay.spec.ts`.
- Gaps: nothing stops a command from the console while the replay page is open (no inhibit); the export file
  (D-EXT-10) carries no banner of its own beyond its file name; the colour and banner were judged by inspection
  (INSP-01), not by a user test.

### H-9 Wrong vehicle commanded

- Cause: the operator picks the wrong vehicle; two consoles command one vehicle.
- Effect: a command (up to vent_cabin) acts on the wrong test article.
- Controls built: no default vehicle: the panel is blank until one is chosen, and a new vehicle never becomes the
  target (P-pf26); the vehicle is part of the token's args hash (D-CMD-06); the lock is per vehicle and
  command (D-CMD-07); for a hazardous command the dialog repeats the vehicle and the operator types its ID (D-UI-20,
  browser only).
- Tests: `ui/src/components/commands.test.tsx` ("the vehicle is an explicit choice"), `e2e/hazardous-confirm.spec.ts`,
  `tests/integration/test_commands_api.py::test_hazardous_lock_ignores_args` (token args binding only; the per-vehicle lock itself has no two-vehicle test).
- Gaps: a valid token on the wrong vehicle, picked by the operator, still goes through: the server cannot know what
  was meant (the typed ID is not checked server-side). No command authority: two operators can command one vehicle
  seconds apart (R24 F6, owner decision).

### H-10 Time and clock errors

- Cause: the bench clock is not UTC (unset, fast, GPS or TAI, week rollover); a leap second `:60`; the host or the
  vehicle clock steps backward.
- Effect: frames rejected, so telemetry stops; or replay and charts zigzag over the repeated second; or latency
  figures hide a real offset.
- Controls built: frames outside `[now - 7 d, now + 5 min]` go to the DLQ with the reason, and the console shows
  LOS plus FRAMES REJECTED (D-ING-18, D-UI-25); live classification goes by `seq`, so a backward step is not
  mis-ordered live (R26 F2); the simulator stamps UTC on the host that runs ingest (D-SIM-03).
- Tests: `tests/unit/test_frame_model.py::test_t_onboard_window`, `tests/integration/test_los.py`.
- Gaps (mostly open, analysed in [R26](../research/R26-time-handling.md)): a bench clock that is not UTC loses every
  frame, no correlation function and no TIME_OFFSET alarm (F1, high for any real bench, dormant with the simulator);
  a `:60` is rejected as a schema error, and a backward step interleaves archive and replay order (F2); ground host
  NTP is a documented need, not a check (F3). All three need work before a bench ICD is frozen.

### H-11 Session report frozen with missing data

- Cause: a report is frozen before late frames arrive, or while frames were lost.
- Effect: a post-test conclusion from a report that looks complete.
- Controls built: the report freezes at the vehicle watermark, or after 30 s marked "incomplete: n of m frames"
  (D-DB-10), until then 409 `REPORT_PENDING`; GAP rows and the frame count against the expected one are in the
  report (D-DB-06, D-ING-16, DAT-02); an incomplete report can be re-frozen while hot rows exist.
- Tests: `tests/integration/test_sessions.py::test_report_incomplete_after_30s`,
  `tests/integration/test_sessions.py::test_report_pending_until_watermark`,
  `tests/integration/test_sessions.py::test_report_never_truncated`.
- Gaps: once the hot rows are archived and deleted an incomplete report cannot be rebuilt (410); a reader who
  ignores the "incomplete" mark gets min/max/avg over fewer frames.

## Requirement to hazard trace

| Hazard | Requirements that control it |
| --- | --- |
| H-1 | CMD-03, CMD-05, CMD-04, CMD-01 |
| H-2 | ALM-01, ALM-03, ALM-04, ALM-05, ALM-06, UI-05, EXT-03 (planned, no property test on this branch) |
| H-3 | UI-02, ING-04, ING-05, UI-07 |
| H-4 | CMD-01, CMD-02, CMD-04, UI-06 |
| H-5 | ING-01, ING-02, ING-03, ING-04, ING-07, ING-08, ENG-02 |
| H-6 | EXT-01, ALM-08, ALM-01 |
| H-7 | DAT-04, DAT-05, DAT-06 |
| H-8 | UI-05, DAT-06 |
| H-9 | CMD-03, CMD-05 |
| H-10 | ING-02, ING-09, UI-02 |
| H-11 | DAT-02 |

Every requirement not listed controls no identified hazard.
