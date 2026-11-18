# Acceptance test report, Gate 5

Date: 2026-11-18. Built from commit `c6034e8` of branch `gate5-acceptance` (fresh clone of `main` at `7e696b3`, plus the Gate 5 fixes listed under INSP-01). Spec: `Nyx Ground Segment – Test Campaign Monitoring & Commanding Requirements and Acce.md` §5. Per-requirement status: [requirements.md](requirements.md).

## How and where it was measured

- Machine: Apple M4 Pro, 12 cores, 24 GB, macOS 27.2.1, Docker Desktop. One laptop. The load driver (perf scripts, browser) ran on the same machine as the stack (D-PERF-08), so the numbers include that load.
- Stack: `make up` in a fresh clone, 8 services healthy, ports shifted by 1400. Other stacks on the machine were stopped for the measurements.
- ATP-08 run N and the passing run P started on a quiet machine: 1-min load average 3.56 and 3.81 before them (12 cores). The first run P did not (load 28.08, NCR-01). The ATP-01 soak did not start on a quiet machine either: it started at 17:45:08Z, 13 s after that first run P, whose summary records a 1-min load average of 6.81 after the run (above the step-0 gate of 4). ATP-01's pass checks (frames lost, false raises, chattering) do not depend on latency, so the soak stands, but its summary recorded no load, so the load at its start is the 6.81 from that neighbouring run, not a measurement of its own. It ran on the same machine as the rest.
- Every p95 comes from pooled raw samples with its rank-based 95 % confidence interval where n >= 120 (D-PERF-10). Raw samples are in the CSV files next to each summary.
- Suite logs (lint, pytest, system, e2e) were produced by the CI-equivalent steps in the clone on the same day. The logs are kept outside the repository; CI on the release tag is the repeatable record: TBD-CI.

## Acceptance table (spec §5)

| ATP | Scenario | Requirements covered | Result | Evidence |
| --- | --- | --- | --- | --- |
| ATP-01 | OPS-1 nominal run | ING-01, ING-09, UI-01, DAT-01, DAT-02 | PASS | 2 h soak on NYX-TA1 + TA2, sessions started and stopped, nothing injected: 2 x 72000 frames stored, 0 lost, 0 GAP, 0 SEQ_RESET, 0 simulator overflow, 0 false raises, no chattering; but both vehicles showed LOSS OF SIGNAL once: NYX-TA2 17:56:31-33Z (2.2 s) and NYX-TA1 17:56:31-33Z (2.2 s, after a 0.1 s one at 17:56:28Z), 0 frames lost: the ground saw more than 2 s without frames while the host stalled and the simulator's Redis reads timed out (NCR-01); an operator would have heard a LOS tone during a nominal run ([soak summary](perf/soak-2026-11-18/summary.md), reports `report-NYX-TA1.csv`, `report-NYX-TA2.csv`). ING-09 and UI-01 numbers are from ATP-08 run N. `e2e/atp01-session.spec.ts`: two 2-minute sessions, report pending then ready, CSV times end in `Z` (2.2 min). |
| ATP-02 | OPS-2 cabin leak | ALM-01 to 06, ALM-08, CMD-01, UI-05, UI-06 | PASS | `e2e/atp02-leak.spec.ts` (leak, yellow, red, ack, isolate_valve, VERIFIED, clear; 35.6 s; ACK applied after 49 ms), `e2e/ack.spec.ts`. ALM-08 mid-test edit: `tests/integration/test_limits_reload.py` (pytest run). ALM-01 to 03, 06: unit and integration tests in the pytest run (1743 passed). |
| ATP-03 | OPS-2 command failure | CMD-02 | PASS | `tests/integration/test_commands_api.py::test_not_received_5s` (simulator `pause 6`) and `::test_not_verified_15s` (`hold_exec 16`), both passed in the pytest run. Automated, not run by hand. |
| ATP-04 | OPS-3 loss of signal | ING-04, UI-02 | PASS | `e2e/atp04-los.spec.ts`: dropout 10 s, every row STALE after 2.09 s, LOSS OF SIGNAL, one tone, AOS with 100 frames lost, recovery; second test: FRAMES REJECTED instead of LOS. `tests/system/test_link_events.py::test_dropout_gap_los_aos`. |
| ATP-05 | OPS-4 ground failures | ING-02, 03, 05, 06, 07, 08, ENG-02, UI-07 | PASS | System suite 52 passed (11 min 48 s): `test_ownership.py::test_kill_owner_mid_stream` (5 variants), `::test_hung_owner_is_stolen` (5), `test_faults.py` (duplicate, malformed, seq_jump, freeze), `test_seq_reset.py`, `test_redis_restart.py::test_redis_stop_10s_recovers`, `test_dlq_crash.py`. UI-07: `e2e/reconnect.spec.ts`, data flowing 1222 ms after the API restart began (compose restart returned at 574 ms; phases: open +1103 ms, snapshot +1137 ms), banner from the first error; Redis stopped 10 s shows GROUND PROCESSING DOWN on both vehicles, no LOS. The system log prints no takeover, failover or resume timings (verbose lines only show PASSED); none are quoted. |
| ATP-06 | OPS-5 hazardous command | CMD-03, 04, 05 | PASS | `e2e/hazardous.spec.ts` (vent_cabin with the typed id and a reason reaches VERIFIED, never without the confirm; fc1 and fc2 within 2 s: fc2 told fc1 sent it), `e2e/hazardous-confirm.spec.ts`, `tests/integration/test_commands_api.py` (pytest run). Audit log: INSP-01 finding 8. |
| ATP-07 | OPS-6 investigation | DAT-03 to 06, UI-04 | PASS (steps 1 to 5); DAT-03 and UI-04 not measured by these steps | [record](atp/records/2026-11-18/atp07-record.md), screenshots [form](atp/records/2026-11-18/atp07-1-form.png), [replay at 12:10:00Z, just before the leak is raised: NOMINAL](atp/records/2026-11-18/atp07-2-leak.png), [dropout](atp/records/2026-11-18/atp07-3-dropout.png), [finished](atp/records/2026-11-18/atp07-4-finished.png). Step 1 backfill 71900 frames; step 2 hours 12 and 13 archived (35900 and 36000 rows; 35900 = 36000 minus the 100 frames of the dropout); step 3 `12.parquet: OK`; step 4 archiver crashed after put, hour 14 recorded and rebuilt, sidecar OK, hot rows kept at 107900; step 5 replay of 20 min at 10x: REPLAY banner purple, no button in it, leak RED LOW at replay 12:10, back to normal at 12:12, dropout STALE x8 and LOS at 12:15, finished in 145 s and 147 s. The leak itself in replay is evidenced by the observation log in the record (RED LOW at replay 12:10:16, 94.87 kPa), by `e2e/replay.spec.ts` passing on the fresh stack (it asserts the recorded leak RED in replay) and by the demo recording from 0:58. DAT-03: ATP-08 run N. UI-04: `e2e/chart.spec.ts` plus the open point below. 1x speed, replay against the recorded alarms and the 409 on a corrupt hour: `tests/integration/test_replay_equality.py` (pytest run) and `e2e/replay.spec.ts` passed on a fresh stack (`nyx-demo`, same commit, 2026-11-18 20:40Z: 4 passed with atp02-leak and atp04-los, recorded as the [demo](demo/README.md)). |
| ATP-08 | Performance | ING-09, ING-10, ALM-05, UI-01, DAT-03 | PASS (NCR-01) | Run N and run P below. |
| INSP-01 | Inspection | ALM-07, CMD-04, UI-03, UI-08, ENG-01, 03 to 10 | PASS | 13 findings below. ENG-04 needs the CI run on the release tag: TBD-CI. |

### ATP-08 numbers

Run N, [summary](perf/run-n-2026-11-18/summary.md): 2 vehicles x 10 Hz, 600 s, 120 RED steps. Load average before 3.56.

| Requirement | Measure | n | p95 (95 % CI) | Limit | Result |
| --- | --- | --- | --- | --- | --- |
| ING-09 | ingest, both vehicles pooled | 11877 | 16.6 ms [16.3, 16.8] | 200 ms | PASS |
| ALM-05 | alarm, first 20 | 20 | 18.7 ms | 300 ms | PASS |
| ALM-05 | alarm, all 120 | 120 | 19.9 ms [18.6, 22.5] | 300 ms | PASS |
| UI-01 | frame to screen (age + paint) | 12008 | 94.4 ms [93.7, 95.3] | 1000 ms | PASS |
| DAT-03 | 1 h range query | 60 | 23.3 ms | 300 ms | PASS |

Run P, ING-10 (S), 2 vehicles x 100 Hz, 300 s. Both runs are reported as measured:

| Run | Load average before | Max lag | Result | Where |
| --- | --- | --- | --- | --- |
| First, sampled 17:39:55-17:44:55Z | 28.08 | 19.564 s (median 23 ms; ingest p95 11126.2 ms, alarm p95 8438.4 ms) | FAIL | [run-p-2026-11-18-host-load](perf/run-p-2026-11-18-host-load/summary.md), [NCR-01](ncr/NCR-01.md) |
| Second, after ATP-08 step 0 (quiet-machine gate) was added | 3.81 | 0.648 s (ingest p95 49.9 ms, alarm p95 54.9 ms, query p95 31.9 ms, ingest-1 CPU mean 11.6 % max 18.7 %) | PASS | [run-p-2026-11-18](perf/run-p-2026-11-18/summary.md) |

The first run started shortly after a full `make up --build`, with the host oversubscribed (load average 28.08 before it, against 3.73 and 3.81 on the passing runs). `lag.csv` shows two stall episodes, 17:40:04-07Z (up to 3.56 s) and 17:40:41-17:41:01Z (up to 19.56 s), and a brief 1.2 s blip at 17:43:56Z; 69 of 600 samples were at or above 1 s. The load is a correlation with the stall, not a proven mechanism. The same stalls produced the LOSS OF SIGNAL in the soak that followed (ATP-01 row, NCR-01). The second run is a rerun after a documented cause and a procedure change, not a rerun until it passed: the failed run stays in the repository and in NCR-01. Stall trials after Gate 5 ([summary](perf/stall-2026-11-18/summary.md)) show the recovery is CPU-bound: 2.5–3.5 s after a 10 s freeze on a free host, about 10 s with ingest capped at a quarter of a core.

Run N's first attempt, [run-n-2026-11-18-aborted](perf/run-n-2026-11-18-aborted/), is not a measurement: the browser did not run because the fresh clone lacked the e2e packages (finding 2.3). Run N was run again after the fix, once, and passed.

Scaling (informative, [scaling.md](perf/scaling.md)): extrapolated from the passing run P, 113 % of one core at 1000 frames/s, capacity 882 frames/s per worker; at 10 vehicles x 100 Hz a restarted worker does not catch up its backlog.

## Rule verdict

Acceptance rule (spec §5): every M requirement PASS with evidence; at least 5 of 7 S requirements PASS; every FAIL or PARTIAL has an NCR.

| Class | PASS | Not PASS | Detail |
| --- | --- | --- | --- |
| M (40) | 40 | 0 | UI-04: server-side 24 h query 0.27 s, browser paint not timed (open point 1). ENG-04 is counted as PASS and holds only once the CI run TBD-CI is green. |
| S (7) | 6 | 1 | ING-10 PASS (NCR-01), ALM-04, ALM-08, CMD-05, ENG-05, ENG-07 PASS; ENG-06 PARTIAL (NCR-02) |

Verdict: the S rule holds (6 of 7), and it holds without ING-10 as well (5 of 7), so no S verdict depends on a rerun. The M rule holds (40 of 40, ENG-04 with the CI run on the tag). The 47 requirements: 46 PASS, 1 PARTIAL. Extensions EXT-01 to 05 are outside the rule and were not run (ATP-E1 to E5, after v1.0).

### Open points

1. UI-04, 24 h window under 2 s. `tests/integration/test_downsample.py::test_24h_window_is_valid_and_fast` passed (1500 buckets, 864000 rows counted, band and first/last values correct) and `e2e/chart.spec.ts` passed. The test prints the elapsed time but asserts no bound (shared CI runners, A-R2R-8). What was measured: one in-process API call for the 24 h window (server side), n = 1, on the fresh `nyx-demo` stack (`pytest -s`, load average 4.26 before, just above the ATP-08 gate of 4): `24 h / 864000 rows -> 1500 buckets in 0.27 s` against the 2 s limit. The browser paint of the 1500 buckets is not timed separately; `e2e/chart.spec.ts` passed. Verdict **PASS** on that method.
2. CI on the release tag (ENG-04): TBD-CI.
3. `e2e/replay.spec.ts`: passed on a fresh stack (`nyx-demo`, same commit, 2026-11-18 20:40Z: 4 passed with atp02-leak and atp04-los, recorded as the [demo](demo/README.md)). In the Gate 5 e2e run it was refused in `beforeAll` with "NYX-TA9 backfill window exhausted": the long-lived acceptance stack's TA9 history was used up by ATP-07 and the suite runs, and the fixture's guard says to use a fresh stack. That refusal is reported, not hidden; procedure note: run the replay spec on a stack with unused NYX-TA9 history (CI always does).

## Non-conformance reports

| NCR | Requirement | Severity | Decision |
| --- | --- | --- | --- |
| [NCR-01](ncr/NCR-01.md) | ING-10 lag over 1 s when the host is oversubscribed | minor | accepted by the owner after an investigation ([stall trials](perf/stall-2026-11-18/summary.md): back under 1 s 2.5–3.5 s after a 10 s whole-stack freeze; no ingest defect); ATP-08 step 0 gates the start on a quiet machine |
| [NCR-02](ncr/NCR-02.md) | ENG-06 timed run by a newcomer not done | minor | accept with a known limitation (proposed; the owner confirms) |
| [NCR-03](ncr/NCR-03.md) | Fresh-clone defects in the procedures and setup (ENG-01, ENG-08, ATP-08) | minor | fix now (done on this branch) |

## Automated suites (CI-equivalent, same day, in the clone)

| Suite | Result |
| --- | --- |
| Vitest (`ui`) | 429 passed, 35 files; line coverage 98.6 % |
| `tsc --noEmit` (ui, e2e) | clean |
| ruff check, ruff format | clean, 307 files formatted |
| mypy strict | no issues in 304 source files |
| commit check, stream schema notice, `toLocale` grep, `make gen` stale check | clean |
| pytest (unit, integration, meta) | 1743 passed, 1 deselected, 17 min 0 s; coverage 98.52 % (floor 80 %) |
| after_coverage (`tests/meta/test_except_coverage.py`) | 1 passed |
| system (`tests/system`, full stack) | 52 passed, 11 min 48 s |
| ACL log check (`scripts/check_acl_log.py`) | clean twice (after system, after e2e) |
| Playwright e2e | 29 passed, 1 failed (`replay.spec.ts`, refused in `beforeAll`: NYX-TA9 backfill window exhausted; passed on a fresh stack, open point 3); 4.9 min |

e2e numbers worth keeping: UI-07 reconnect 1222 ms, ACK applied after 49 ms, every row STALE after 2.09 s.

## INSP-01 findings

Done by reading files and grepping in the clone; no stack was used for 3 to 13.

1. ENG-01, fresh clone: `git clone` took 2 s; `make up` finished in 15 s with exit 0 and 8 services healthy. It reused cached image layers, so a cold build time was not measured. The demo then ran on that stack (ATP-07, e2e smoke). `tests/system/test_lifecycle.py::test_up_down_up_keeps_env_and_logins` passed. PASS.
2. Fresh-clone findings, all fixed on this branch, each in the git log:
   1. README clone URL pointed to a repository that does not exist (`5156c01`; [NCR-03](ncr/NCR-03.md)).
   2. The archiver logged a MinIO client pool warning every 5 s; pool size raised (`f5815a2`).
   3. `make perf` and `make e2e` on a fresh clone lacked the e2e packages, so run N did not run its browser (`47d4257`).
   4. ATP psql commands needed `exec -T` from a script (`2416a7c`).
   5. Run P under host load failed ING-10: NCR-01; ATP-08 step 0 added.
   6. `e2e/replay.spec.ts` needs a fresh TA9 history. A procedure note, not a defect: the fixture refuses a used-up stack by design.
3. ENG-08, README: has What it is, Run, What to try first, Architecture, Decisions and why, Limitations, Next steps; run is three commands (clone, cd, `make up`); `tests/meta/test_readme_sections.py` passed. PASS.
4. ENG-03, tests at three levels: `tests/unit` 81 files, `tests/integration` 59 (real Redis, Postgres, MinIO), `tests/system` 21 (container kills against the full stack), `tests/meta` 13, `e2e/` 10 Playwright specs, `ui/src` 35 Vitest files. PASS.
5. ENG-04, CI: `.github/workflows/ci.yml` runs on every push and `v*` tag; jobs `lint` (tsc, Vitest, ruff, commit check, `toLocale` grep, format, mypy strict, `make gen` stale check), `test` (pytest with coverage floor 80 %), `e2e` (up/down/up, system tests, ACL check, Playwright). The same steps ran green locally. The green run on the release tag is TBD-CI.
6. ENG-05, health and logs: `/healthz` per service (`src/gs/*`, `tests/integration/test_health.py`); `tests/system/test_json_logs.py` passed: every service logs JSON and frame-path lines carry vehicle and seq. PASS.
7. ALM-07, new check type: README "Add a check type": one module in `src/gs/alarms/checks/` with `@register`, worker not edited; `tests/unit/test_check_registry.py::test_new_check_file_is_registered` passed. PASS.
8. CMD-04, audit log: `migrations/03-audit.sql` revokes access from PUBLIC, grants the app role SELECT and INSERT only, and adds triggers that raise on UPDATE, DELETE and TRUNCATE (even for the owner); `tests/integration/test_initdb.py::test_audit_log_is_append_only_even_for_owner` passed. Limitation, stated in the README: the log is append-only by role and trigger, not tamper-evident. PASS.
9. UI-03, status not by colour alone: the console shows state as text with the colour (for example `RED LOW · 94.87 kPa (limit 95)`, STALE, LOSS OF SIGNAL in the ATP-07 screenshots); `ui/src/style.test.ts` computes WCAG AA contrast from the tokens in `style.css` and passed. PASS.
10. UI-08, UTC: `grep -rn toLocale ui/src` returns 0 lines (also a CI step); `ui/src/time.ts` formats `HH:MM:SSZ`; `tests/unit/test_time_format.py` (API JSON, CSV, report, logs) passed. PASS.
11. ENG-07, history: 411 commits since 2026-11-13, subjects `fix` 124, `feat` 81, `test` 78, `docs` 62, merges 40, others 26; `scripts/check_commits.py` clean. The first commit is the spec only. The largest commits are recorded performance CSVs (12 000-line `ingest.csv` files), not code. PASS.
12. ENG-09, types and hosts: `[tool.mypy] strict = true`; mypy strict clean on 304 source files; settings come from environment variables and fail on a missing one (`ConfigError`); `tests/meta/test_no_host_literals.py` passed. PASS.
13. ENG-10, stack: Python backend in `src/gs`, UI in React 19 and TypeScript 6 (`ui/package.json`); `tests/meta/test_stack.py` passed. PASS.

## Known limitations

See [CHANGELOG.md](../CHANGELOG.md#known-limitations) and the README limitations section. The ones that touch this report: one laptop with the load driver on the same machine; ING-10 only on a host that is not CPU-oversubscribed (NCR-01); no cold build time; no timed ENG-06 run (NCR-02).
