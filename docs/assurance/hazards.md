# Hazard list for the test-campaign ground segment (candidate)

Scope and status: a short list of what Nyx can cause or fail to prevent during a test campaign, with the controls
already built and the gaps. It supports [criticality.md](criticality.md) and NPR 7150.2D SWE-052 (software
requirements to system hazards). The list is a proposal from research notes
[R24](../research/R24-command-verification-pus.md) and [R25](../research/R25-software-assurance-gap.md). **To be
confirmed by the owner / system hazard analysis.**

Why a table here and not a column in `requirements.md`: the requirements table has eight columns and a meta test
that parses it (`tests/meta/test_requirements_trace.py`), so a column would touch every row and the test. This
section keeps the trace in one place. `tests/meta/test_design_trace.py` checks that every requirement and decision
named here exists.

Severity is the effect on the test article or the test, not on crew. It is a candidate rating only.

## Hazards

### H-1 Unintended hazardous command (vent_cabin)

- Cause: operator error, a repeated click, a replayed or stolen confirm token, a script bypassing the console, a
  wrong `tc` entry.
- Effect: irreversible loss of the test article's cabin atmosphere.
- Controls built: single-use user-bound token with typed vehicle and reason (D-CMD-06, CMD-03); double-command
  interlock (D-CMD-07, CMD-05); argument validation against `commands.yaml` (D-CMD-09); authenticated session
  identity, never from the body (D-CMD-10); append-only audit (D-CMD-08, CMD-04).
- Gaps: no prerequisite check (R24 F2; D-EXT-08 designed, not built); one telecommand fires the hazard, with no
  arm/enable step (R24 F3, ECSS-E-ST-70-11C §5.5.2b); one person can confirm alone (R24 F5, owner decision).

### H-2 Missed or late RED alarm

- Cause: persistence or hysteresis hides a real excursion; the engine falls behind; the alert is lost on takeover.
- Effect: the test conductor does not react to a limit violation.
- Controls built: red needs no persistence count of its own (D-ALM-03); rate-of-change check (D-ALM-05, ALM-04);
  only RED alerts audibly and once (D-ALM-09, D-ALM-08, UI-05); engine state survives a worker kill (D-ALM-11,
  ING-07); latency measured, p95 under 300 ms (ALM-05); every classification is a pure, property-tested function
  (D-ALM-10, EXT-03).
- Gaps: none known; the 300 ms figure is a requirement, not a hard guarantee under overload (ING-10 covers 100 Hz).

### H-3 False or hidden loss of signal, stale value shown as fresh

- Cause: frozen sensor values, a stalled ingest, a heartbeat that outlives its worker, a UI clock error.
- Effect: the operator trusts old data, or ignores a healthy vehicle.
- Controls built: STALE at 2 s and LOS shown on every value (UI-02, D-ALM-04); FROZEN detector (D-ING-21); per-vehicle
  heartbeat written from inside the read loop, so a stuck loop stops it (D-ING-25); gap counted and shown (ING-04,
  D-ING-16); stale and LOS recover on their own (UI-02).
- Gaps: none known.

### H-4 Wrong command outcome shown

- Cause: VERIFIED means "started", not "done"; a failed execution has no field in `tcack`; a late echo after a
  timeout.
- Effect: the operator believes a hazardous or corrective command took effect, or that it did not.
- Controls built: monotonic states and terminal FAILED with late evidence kept beside it (D-CMD-04, D-CMD-11,
  CMD-02); outcome text for each case (D-CMD-02); timeline shows all four times (UI-06).
- Gaps: no completion or effect check (R24 F1); `tcack` cannot carry an execution failure (R24 F4, cheap now,
  costly after a bench ICD is frozen).

### H-5 Lost or duplicated telemetry

- Cause: producer or consumer crash, Redis outage, redelivery, a malformed frame.
- Effect: a hole or a repeat in the record; a wrong alarm.
- Controls built: ack only after commit (D-ING-02, ING-07); store key makes a duplicate a no-op (D-ING-13, ING-03);
  producer buffer during a Redis outage (D-SIM-05, ENG-02); poison frames to the DLQ with the reason (D-ING-05,
  D-ING-27, ING-02); gaps counted, not hidden (ING-04, ING-01).
- Gaps: none known.

### H-6 Wrong limit set or command definition applied

- Cause: a bad or half-edited `mission/` file goes live.
- Effect: alarms judge against the wrong band, or a command is allowed with a wrong range.
- Controls built: the three files validated as one set, the reload refused on any error (D-EXT-01, EXT-01); band
  order checked at load (D-ALM-01); the sha256 of the limit set stored on every alarm and a LIMITS_APPLIED event
  (D-ALM-12, ALM-08); limits events are never pruned (D-DB-09).
- Gaps: no second-person review of a mission change; the change is traceable, not approved.

### H-7 Corrupt archive shown as truth in replay or export

- Cause: bit rot or a partial upload in object storage.
- Effect: a post-test analysis built on altered data.
- Controls built: hash on upload, full re-read, content check, row written only after verification (D-ARC-02,
  D-ARC-03, D-ARC-04, DAT-04); hot data deleted only after verification (D-ARC-05, DAT-05); rolling scrub raises
  ARCHIVE_CORRUPT (D-ARC-11); replay equals recorded rows (D-REP-01, D-REP-05, DAT-06).
- Gaps: the scrub detects only; there is no repair (D-ARC-11).

## Requirement to hazard trace

| Hazard | Requirements that control it |
| --- | --- |
| H-1 | CMD-03, CMD-05, CMD-04, CMD-01 |
| H-2 | ALM-01, ALM-03, ALM-04, ALM-05, ALM-06, UI-05, EXT-03 |
| H-3 | UI-02, ING-04, ING-05, UI-07 |
| H-4 | CMD-01, CMD-02, CMD-04, UI-06 |
| H-5 | ING-01, ING-02, ING-03, ING-04, ING-07, ING-08, ENG-02 |
| H-6 | EXT-01, ALM-08, ALM-01 |
| H-7 | DAT-04, DAT-05, DAT-06 |

Every requirement not listed controls no identified hazard.
