# Nyx Ground Segment – Test Campaign Monitoring & Commanding: Requirements and Acceptance Plan

Nov 13, 2026 · @malek khannoussi

## 1. Mission need and scope

The Nyx test campaign needs one ground tool that test engineers and flight controllers trust to show live vehicle state, raise alarms, send verified commands, and keep every frame for later investigation. This document specifies that tool as if it were a real deliverable, with acceptance criteria an operations team would sign off.

**Background.** Nyx is a reusable cargo capsule heading for an ISS demo mission; before flight it goes through ground tests (pressure, vibration, drop, thermal). During each test, people at the bench must see the vehicle's state in real time and act on it. The same software later grows into mission operations. The job posting describes exactly this: software for integrated testing and mission operations, used by flight controllers and engineering teams ([job posting](https://en.devjobs.de/job/93aee774cf6842a8a36ae6d656d88739)).

**Problem today (assumed).** Test data is seen on vendor tools per bench, alarms are checked by eye, commands are confirmed verbally, and data from different tests is hard to compare later.

**In scope**

- Live telemetry ingest from 1–N test articles (start with 2: `NYX-TA1`, `NYX-TA2`)
- Limit monitoring, alarms with acknowledgement
- Commanding with end-to-end verification
- Test session records and reports
- Archive to object storage and replay for anomaly investigation

**Out of scope**

- Real CCSDS packet decoding (JSON frames stand in; the decoder is a pluggable layer)
- Flight software, RF link, authentication against company SSO (a simple login is enough)

**Practice rules for you.** Build it in a fresh repo in about 25 hours. Treat each requirement as a promise to a real user. Sign-off is the acceptance plan in section 5, not points.

## 2. Users and operational scenarios

Four roles use the tool; six scenarios describe the real situations it must survive. Every requirement in section 4 traces back to one of these scenarios.

| Role | What they need from the tool |
| --- | --- |
| Test conductor (AIT) | Start/stop a test, see pass/fail of limits, get a report signed off after the test |
| Flight controller (ops) | Trust the screen: live, fresh, abnormal values obvious at a glance, safe commanding |
| Systems / anomaly engineer | Replay any past window, compare tests, export data |
| Ground software engineer | Add a parameter or limit without code changes in the core, deploy with one command |

**OPS-1 – Nominal test run.** The test conductor starts session "Pressure test #4" on `NYX-TA1`. Telemetry flows at 10 Hz for 2 hours. All values stay green. At the end the conductor stops the session and downloads the report for the test review.

**OPS-2 – Cabin leak during a test.** Cabin pressure drops 0.4 kPa/s. The console goes yellow, then red. An alarm sounds once, the controller acknowledges it with a note, sends `isolate_valve`, and sees the command go to VERIFIED. When pressure is back in limits the alarm clears.

**OPS-3 – Loss of signal.** The bench link drops for 10 s. Every value goes STALE after 2 s, the vehicle shows LOSS OF SIGNAL. When the link returns, the system reports exactly how many frames were lost. No old value is ever shown as current.

**OPS-4 – Ground system failure.** One ingest worker crashes mid-test; later the message broker restarts. No frame is lost, no alarm is duplicated, nobody has to run a manual fix.

**OPS-5 – Dangerous command.** A controller sends `vent_cabin`. The UI asks for a second confirmation and a reason. The command, who sent it, why, and its outcome are in the audit log.

**OPS-6 – Anomaly investigation.** Three days later, an engineer replays 14:00–14:20 of last Tuesday's test at 10× speed from the archive, with the same alarms appearing as they did live, clearly marked REPLAY.

## 3. Interfaces and data

The tool talks to the test article through a simulator for now, using the interfaces below; swapping the simulator for real bench hardware must not change anything downstream.

**Telemetry frame (vehicle → ground), 10 Hz per test article, Redis Stream `tm:<vehicle>`**

| Field | Type / unit | Notes |
| --- | --- | --- |
| `vehicle` | string | `NYX-TA1`, `NYX-TA2` |
| `seq` | uint32 | +1 per frame; resets to 0 on simulator restart |
| `t_onboard` | ISO 8601 UTC | time the vehicle stamped the frame |
| `cabin_pressure_kpa` | float, kPa | nominal 101.3 |
| `cabin_temp_c` | float, °C |  |
| `bus_voltage_v` / `bus_current_a` | float, V / A |  |
| `tank_pressure_bar` | float, bar | propellant tank |
| `rate_x/y/z_dps` | float, deg/s | attitude rates |
| `mode` | enum | `SAFE`, `STANDBY`, `TEST`, `FAULT` |
| `last_cmd_id` | UUID or null | last command executed on board |

**Command (ground → vehicle), stream `tc:<vehicle>`:** `cmd_id` (UUID), `name`, `args` (JSON), `issued_by`, `issued_at`, `reason` (required for hazardous commands). Command catalogue in `commands.yaml` with a `hazardous: true/false` flag.

**Limits file `limits.yaml`:** per parameter, yellow and red low/high, hysteresis %, persistence (frames), and an optional rate-of-change limit. Loaded at start and reloadable without restart; every change is versioned (file hash stored with each alarm).

**Archive (object storage, MinIO / S3 API):** `archive/<vehicle>/<YYYY>/<MM>/<DD>/<HH>.parquet` with a sidecar `.sha256`. One object per vehicle per hour.

**Simulator fault injection (for verification):** `leak`, `overheat`, `dropout` (silence N s), `freeze` (repeat last values), `seq_jump`, `duplicate`, `malformed` (send an invalid frame).

## 4. Requirements and acceptance criteria

47 requirements (40 must, 7 should), each with a measurable pass condition. Method: **T** = test (automated), **D** = demonstration, **I** = inspection (code/docs), **A** = analysis (measurement + written reasoning). Priority: **M** = must (blocks acceptance), **S** = should. The last column links each to the job posting.

### 4.1 Ingest and processing

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-ING-01 | ingest frames from ≥ 2 test articles in parallel via Redis Streams | both vehicles visible live for 2 h, 0 frames lost | T, D | M | OPS-1 | Event streaming |
| GS-ING-02 | validate every frame against the schema | `malformed` frame lands in `tm:dlq` with the reason; ingest continues | T | M | OPS-4 | Real-time ingestion |
| GS-ING-03 | drop duplicate frames per vehicle | `duplicate` fault: each `seq` stored once | T | M | OPS-4 | Real-time ingestion |
| GS-ING-04 | detect gaps and report lost frame count | `dropout 10 s` → one `GAP` event with 100 ± 1 lost frames | T | M | OPS-3 | Real-time ingestion |
| GS-ING-05 | handle a sequence reset without data loss or freeze | restart simulator: ingest resumes within 2 s, `SEQ_RESET` event logged | T | M | OPS-4 | Robust software |
| GS-ING-06 | run as ≥ 2 workers in one consumer group | 2 workers share load; per-vehicle order kept (document how) | T, A | M | OPS-4 | Distributed systems |
| GS-ING-07 | acknowledge a frame only after it is stored | kill a worker mid-stream: 0 frames lost, verified by seq count | T | M | OPS-4 | Distributed systems |
| GS-ING-08 | reclaim stuck messages and quarantine poison frames | entry idle > 30 s is reclaimed; frame failing 3× goes to DLQ | T | M | OPS-4 | Distributed systems |
| GS-ING-09 | keep ingest latency low | p95 from `t_onboard` to stored < 200 ms at 10 Hz × 2 vehicles | A | M | OPS-1 | Real-time ingestion |
| GS-ING-10 | scale beyond the nominal rate | 2 vehicles × 100 Hz for 5 min: lag stays < 1 s, numbers in README | A | S | – | Scalable systems |

### 4.2 Limits and alarms

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-ALM-01 | classify each value as nominal / yellow / red from `limits.yaml` | boundary values tested: exactly on, just inside, just outside | T | M | OPS-2 | Attention to detail |
| GS-ALM-02 | apply hysteresis | noise of ±1% around a limit raises 1 alarm, not many | T | M | OPS-2 | Safety-critical |
| GS-ALM-03 | apply persistence (N consecutive frames) | a single-frame spike does not alarm when N=3 | T | M | OPS-2 | Safety-critical |
| GS-ALM-04 | support rate-of-change limits | leak at 0.4 kPa/s alarms before the absolute limit | T | S | OPS-2 | Safety-critical |
| GS-ALM-05 | raise an alarm within 300 ms of the triggering frame | measured on 20 alarms, p95 < 300 ms | A | M | OPS-2 | Real-time |
| GS-ALM-06 | manage alarm life cycle ACTIVE → ACKED → CLEARED | who, when and note stored for every transition | T | M | OPS-2 | Mission operations |
| GS-ALM-07 | let a new check type be added without editing the worker | inspect: new class + registration only; shown in README | I | M | – | OOP, design patterns |
| GS-ALM-08 | reload limits without restart and record the limits version | change YAML, new limit active < 5 s, alarm shows file hash | T | S | – | Engineering best practice |

### 4.3 Commanding

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-CMD-01 | track every command QUEUED → SENT → RECEIVED → VERIFIED | `isolate_valve` reaches VERIFIED when seen in `last_cmd_id` | T | M | OPS-2 | Mission operations |
| GS-CMD-02 | mark FAILED on timeout | not received in 5 s or not verified in 15 s → FAILED with reason; both cases tested | T | M | OPS-2 | Safety-critical |
| GS-CMD-03 | require 2-step confirmation + reason for hazardous commands | `vent_cabin` cannot be sent without both | T, D | M | OPS-5 | Safety-critical |
| GS-CMD-04 | write an append-only audit log | every command and alarm ack with user, time, reason; no update/delete path | I, T | M | OPS-5 | Safety-critical |
| GS-CMD-05 | prevent accidental double commands | same command from 2 operators within 2 s → second one rejected with message | T | S | OPS-5 | Robust software |

### 4.4 Operator console

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-UI-01 | show live values within 1 s of the frame | p95 frame-to-screen < 1 s (measured) | A | M | OPS-1 | Tools for flight controllers |
| GS-UI-02 | mark data STALE after 2 s and LOSS OF SIGNAL per vehicle | dropout: STALE at 2 s ± 0.5 s on every row; recovers on its own | T | M | OPS-3 | Tools for flight controllers |
| GS-UI-03 | show status as text and colour, grey when nominal | inspect: no state shown by colour only; contrast WCAG AA | I | M | OPS-2 | Tools for flight controllers |
| GS-UI-04 | chart any parameter over 2 min to 24 h | 24 h window loads < 2 s with min/max band (server downsampling) | T, A | M | OPS-6 | Modern JS framework |
| GS-UI-05 | list alarms with ack, filter, and a single alert for new red | Playwright: leak → yellow → red → ack → clear | T | M | OPS-2 | Automated testing |
| GS-UI-06 | show each command's status timeline | all 4 states with times visible | D | M | OPS-2 | Tools for flight controllers |
| GS-UI-07 | reconnect live updates on its own | restart API: UI recovers < 5 s, banner while disconnected | T | M | OPS-4 | Robust software |
| GS-UI-08 | use UTC everywhere | inspect: all times UTC with `Z` | I | M | – | Attention to detail |

### 4.5 Test sessions, data and archive

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-DAT-01 | start/stop named test sessions per vehicle | session stored with conductor, start, end | T | M | OPS-1 | Integrated testing |
| GS-DAT-02 | generate a session report | duration, min/max/avg per parameter, alarms, commands, gaps; CSV export | T | M | OPS-1 | Integrated testing |
| GS-DAT-03 | keep 24 h hot data in a time-series store | range query for 1 h of one parameter < 300 ms | A | M | OPS-6 | Time-series DB (bonus) |
| GS-DAT-04 | archive hourly to object storage | one Parquet + `.sha256` per vehicle per hour in MinIO | T | M | OPS-6 | Object storage |
| GS-DAT-05 | delete hot data only after the archive is verified | checksum re-read matches before delete; kill job mid-upload → nothing lost | T | M | OPS-6 | Object storage, robust |
| GS-DAT-06 | replay any past window at 1× or 10× | replay from archive shows same alarms as live; REPLAY banner | T, D | M | OPS-6 | Object storage |

### 4.6 Engineering, delivery and maintainability

| ID | The system shall… | Pass when… | Method | Prio | Scenario | Job posting point |
| --- | --- | --- | --- | --- | --- | --- |
| GS-ENG-01 | start fully with one command | fresh clone → `make up` → demo works first try | D | M | – | Containers (bonus) |
| GS-ENG-02 | recover from a broker restart | stop Redis 10 s → all services recover, no manual step | T | M | OPS-4 | Robust software |
| GS-ENG-03 | have automated tests at 3 levels | unit + integration (real Redis/Postgres) + end-to-end | T | M | – | Automated testing |
| GS-ENG-04 | run CI on every push | GitHub Actions: lint, mypy, tsc, all tests green | I | M | – | CI/CD (bonus) |
| GS-ENG-05 | expose health and structured logs | `/healthz` per service, JSON logs with vehicle + seq | I | S | – | Observability |
| GS-ENG-06 | be extendable by a new team member | "add a parameter" guide: done in < 15 min following it | D | S | – | Mentor juniors |
| GS-ENG-07 | keep a readable history | small commits, clear messages, no giant first commit | I | S | – | Git, agile |
| GS-ENG-08 | be documented for other teams | README: run, architecture, decisions + why, limitations, next steps | I | M | – | Well-documented code |
| GS-ENG-09 | use typed, configurable code | Python type hints + mypy clean; no hard-coded hosts | I | M | – | Best practices |
| GS-ENG-10 | backend in Python, UI in React + TypeScript | inspect | I | M | – | Python, JS framework |

## 5. Acceptance plan

The tool is accepted when all 40 must-requirements pass, at least 5 of 7 should-requirements pass, and every failure has a written non-conformance with a decision. Acceptance runs as 8 test procedures that replay the operational scenarios end to end, plus inspection of code and documents.

**Acceptance rule**

- Every **M** requirement: PASS, with evidence (test name, log, screenshot or measurement)
- **S** requirements: ≥ 5 of 7 PASS
- Any FAIL → a non-conformance report (NCR) in `docs/ncr/`: what failed, impact on the user, severity (major / minor), and decision: fix now, accept with a known limitation, or defer with a plan
- Measurements (method A) include how they were measured, on what machine, and the raw numbers

**Acceptance test procedures**

| ATP | Scenario | Steps (short) | Requirements covered | Result | Evidence |
| --- | --- | --- | --- | --- | --- |
| ATP-01 | OPS-1 nominal run | start session on TA1 + TA2, run 2 h, stop, download report | ING-01, ING-09, UI-01, DAT-01, DAT-02 |  |  |
| ATP-02 | OPS-2 cabin leak | inject `leak`, watch yellow → red, ack with note, send `isolate_valve`, see VERIFIED, alarm clears | ALM-01–06, ALM-08 (edit limits mid-test), CMD-01, UI-05, UI-06 |  |  |
| ATP-03 | OPS-2 command failure | send command with simulator paused 6 s and 16 s | CMD-02 |  |  |
| ATP-04 | OPS-3 loss of signal | inject `dropout 10`, check STALE / LOS timing, check GAP count | ING-04, UI-02 |  |  |
| ATP-05 | OPS-4 ground failures | kill worker mid-stream; inject `duplicate`, `malformed`, `seq_jump`; restart simulator; stop Redis 10 s; restart API | ING-02, 03, 05, 06, 07, 08, ENG-02, UI-07 |  |  |
| ATP-06 | OPS-5 hazardous command | send `vent_cabin` with and without confirmation; two operators send at once; check audit log | CMD-03, 04, 05 |  |  |
| ATP-07 | OPS-6 investigation | let archive job run; kill it mid-upload; verify checksums; replay a 20 min window at 10× | DAT-03–06, UI-04 |  |  |
| ATP-08 | Performance | 2 × 100 Hz for 5 min; measure ingest lag, alarm latency, screen latency, query time | ING-09, ING-10, ALM-05, UI-01, DAT-03 |  |  |
| INSP-01 | Inspection | fresh clone, CI, code, README, history, guides | ALM-07, CMD-04, UI-03, UI-08, ENG-01, 03–10 |  |  |

Fill **Result** with PASS / FAIL / PARTIAL and **Evidence** with a link (test file, CI run, screenshot, README section). If a test can't be automated, write the manual steps in `docs/atp/` so someone else can repeat them.

## 6. Deliverables

A real ground tool is handed over as software plus the evidence that it works. Deliver all of this in one repository.

| Deliverable | Where | Content |
| --- | --- | --- |
| Source code | repo root | backend (Python), console (React + TS), simulator, compose, Makefile |
| README | `README.md` | what it is, run in 3 commands, what to try first, screenshot |
| Design document | `docs/design.md` | architecture diagram, data flow, interfaces (section 3), key decisions with why, trade-offs, alternatives considered |
| Requirements trace | `docs/requirements.md` | this section 4 table with a column linking each ID to code and tests |
| Acceptance test report | `docs/atp-report.md` | section 5 table filled: PASS/FAIL, evidence links, measured numbers |
| Non-conformances | `docs/ncr/` | one file per failed or partial requirement |
| Operator guide | `docs/operator-guide.md` | 1 page: read the console, ack an alarm, send a command, run a replay |
| Developer guide | `docs/dev-guide.md` | add a parameter, add a limit check type, add a command, run tests |
| Release notes | `CHANGELOG.md` | version, what's in, known limitations |
| Demo | link in README | 5-minute screen recording of ATP-02, ATP-04 and ATP-07 |
| CI evidence | GitHub Actions | green run on the release tag |

## 7. Review board questions

In the real interview this is the follow-up call; in a real project it is the design and acceptance review. Answer each out loud in under a minute, pointing to a requirement ID or a test.

**From the flight controller**

1. How do I know the number on screen is current and not frozen?
2. I acked an alarm and the value went worse. What happens?
3. My command shows FAILED. Did the vehicle do it or not? What should I do?
4. Can I trust a replay to look exactly like what happened live?

**From the test conductor**

5. The test lasted 6 hours. Is the report still correct if the link dropped twice?
6. Someone changed a limit mid-test. How do I know which limits applied to which alarm?

**From the ground software lead**

7. Why Redis Streams and not Kafka? What would make you switch?
8. Where exactly could a frame be lost or processed twice? Prove it can't.
9. Two workers, one vehicle: how do you keep per-vehicle order for gap and limit logic?
10. A frame crashes the worker every time. What happens to the stream?
11. How does this scale to 10 vehicles at 100 Hz? What breaks first?
12. Why Parquet, why hourly objects, why delete only after checksum?
13. How would you swap JSON frames for real CCSDS packets?
14. What is not production-ready yet, and in what order would you fix it?

**From the hiring panel**

15. What did you cut to fit the time, and why those things?
16. Which test caught a real bug while you built it?
17. How did you use AI tools, and how did you check what they produced?
18. A new junior joins tomorrow. How do they add a parameter safely?

## 8. Milestones and review gates

Five gates over about 5 days, named like the reviews in a space project. Do not pass a gate until its exit check is true; that habit is what the real team will look for.

1. **Requirements review – end of day 0 (2 h).** Read sections 1–5, write your assumptions and open questions in `docs/assumptions.md`. *Exit:* you can explain every requirement ID in one sentence.
2. **Design review – end of day 1 (6 h).** `docs/design.md` with architecture diagram and decisions. Repo, compose (Redis, Postgres, MinIO), Makefile, CI with one passing test. One vehicle → one worker → storage → live table. *Exit:* thin end-to-end slice runs from a fresh clone.
3. **Implementation review – end of day 3 (12 h).** Day 2: ingest depth (ING-01–10), limits and alarms (ALM). Day 3: commanding (CMD), console (UI), test sessions (DAT-01/02). Tests written alongside. *Exit:* ATP-01 to ATP-06 pass.
4. **Test readiness review – middle of day 4 (4 h).** Archive and replay (DAT-03–06), performance run, all docs from section 6. *Exit:* every requirement has a test or an inspection note.
5. **Acceptance review – end of day 5 (3 h).** Fresh clone, run every ATP, fill section 5's Result and Evidence columns, write NCRs, record the demo, answer section 7 out loud. *Exit:* acceptance rule met, or each gap honestly documented.

When the real task arrives, reuse this structure: write requirement IDs from their brief, map each to a test, and hand over the same evidence package. That alone sets your submission apart.
