# R23 – Mission database standards: how XTCE, ECSS-E-ST-70-31C, Yamcs and OpenC3 COSMOS model calibration, derived parameters, context checks and command preconditions

**Drives:** D-EXT-01, D-EXT-02, D-EXT-03, D-EXT-04, D-EXT-05, D-EXT-06, D-EXT-07, D-EXT-08, D-EXT-09, D-ALM-01, D-ALM-04, EX-22, A-EXT-5, A-EXT-7   **Asked by:** owner, via coordinator, 2026-11-21

Sources (all read on 2026-11-21; PaperLab was not reachable, so none of this is in the library):
- **[S] standard text read:**
  - CCSDS 660.0-B-2 (XTCE v1.2, Blue Book, February 2020, the CCSDS publication of OMG formal/18-10-04), full PDF text extracted and read for the sections cited;
  - the XTCE 1.2 schema `SpaceSystem.xsd` (OMG, 2018-02-04), whose `documentation` elements carry the element descriptions; I read the text of the elements cited;
  - ECSS-E-ST-70-31C (31 July 2008), full PDF text extracted from the ECSS download; cited by clause and table number. **I did not verify page numbers for ECSS, so none are given.**
- **[V] vendor docs read:** Yamcs Server Manual and OpenC3 COSMOS configuration pages, read through a page-summarising fetch tool. A statement is used only when the tool quoted or paraphrased it; where a page was silent, this note says "not stated", never "not supported".
- **[2] secondary:** none relied on for a decision.
- Cross-checked against `mission/*.yaml` and design.md §11.3 (D-EXT-01..09) on this branch.

## Question

EXT-01's remaining parts (after the 47 spec requirements) are calibration (D-EXT-04), derived parameters (D-EXT-05), mode-dependent checks (D-EXT-06), command preconditions `allowed_modes` (D-EXT-08) and FROZEN over raw values (D-EXT-09). The design says table-only calibration, a small expression grammar, and that `mission/` "keeps XTCE names so an export can be written" (design.md §5 cut list). Is that how real ground software models it? Where does Nyx diverge, does it matter for a test campaign, and what would an XTCE export buy?

## Findings

### F1 – XTCE calibration: four forms, selectable by context; Nyx has one of them [S]

- XTCE puts calibration in the data **encoding** of a parameter (CCSDS 660.0-B-2 §6.1.2.1, p. 16: the encoding "includes size in bits, byte order, data type, calibrations and parity checks").
- XSD `CalibratorType` is a choice of **SplineCalibrator** ("piecewise defined function"), **PolynomialCalibrator**, **MathOperationCalibrator** (a postfix/RPN expression) and, in the context list, a **ContextCalibrator**.
- **Spline:** at least 2 points; "The line may be interpolated and/or extrapolated as needed"; per-point `order` 0 (step), 1 (linear, default) or 2 (quadratic).
- **MathOperation:** RPN over constants, the parameter's own value and `ParameterInstanceRef` operands, with operators `+ - * / % ^` and more (I read the first six).
- **ContextCalibrator:** "Describe a calibrator that depends on a matching value using a ContextMatch. When the context matches for the calibrator, the default calibrator is overridden".
- Nyx's D-EXT-04 = SplineCalibrator order 1 with **no extrapolation**; a 2-point table is a linear calibration. Polynomial, order 0/2 splines, math and context calibrators are not built (A-EXT-5, EX-22).

### F2 – ECSS-E-ST-70-31C: calibration is an "interpretation function"; extrapolation is an explicit per-curve choice [S]

- **Clause 6.4.8 (numbering as the text extraction shows it; re-verify, see Open questions):** "fully populated" means, for reporting data:
  - d.3 "All interpretation functions (required to calibrate raw values) shall be provided together with the conditions under which they are applied";
  - d.4 "When the validity of a reporting data depends on specific conditions, the validity conditions shall be provided";
  - d.5 "Ground monitoring checks shall be provided for all reporting data together with the conditions under which they are applied" (exception: data "that can take any possible value");
  - d.6 status consistency checks; d.7 "The corrective actions to be taken whenever a reporting data violates a given check".
- **Table 6-77 (interpretation data):** an *ordered* list of interpretation functions, each with a **Selection Condition** ("the first one in the list is applied") and a **Type** `{linear interpolation, IFL}`; IFL is a scripting language (Annex B.6).
- **Table 6-78 (linear interpolation):** calibration points (raw, engineering) plus **Extrapolation** `{extrapolate, invalid}`: a value outside the range "is extrapolated from the last 2 (or first 2) calibration points of the curve or is declared invalid".
- **Takeaway:** in the base ECSS data model table interpolation is the first-class form, and polynomials exist only through IFL scripts. **Nyx's "outside the table = invalid, never extrapolate" is exactly ECSS's `invalid` option**, chosen globally instead of per curve. This supports D-EXT-04 as designed.

### F3 – Raw vs calibrated: the standards and both tools check the calibrated value by default; the raw side is a separate "validity" notion [S, V]

- **XTCE:** `AlarmRangesType`: "Range values are in calibrated engineering units". `ParameterInstanceRef` has `useCalibratedValue` default `true`. `ValidRange` has `validRangeAppliesToCalibrated` default `true` and is "Not to be construed as an alarm definition, violations of the valid range make a parameter value 'unreasonable'".
- **Yamcs:** calibrators "operate on signed double-precision floating-point numbers" (data-types page); the **parameter archive stores both** the engineering value and the raw value per time (parameter-archive page); valid ranges "mark out-of-range parameter values as invalid".
- **OpenC3:** limits are checked on the converted value, so thresholds are in engineering units; the raw value is kept and shown in a details dialog.
- **Yamcs alarm sheet:** whether triggers apply to calibrated values is not stated on the page I read; I did not confirm it.
- **Takeaway:** D-EXT-04 (checks, derived values and charts on engineering, raw kept in `<name>_raw`) matches. XTCE and Yamcs treat an out-of-range *raw* value as "invalid/unreasonable", which is not an alarm. **Nyx merges the two on purpose** (invalid + RED "OUT OF CALIBRATION RANGE", EX-03). That is more conservative than the standards (a pegged sensor alarms) and right for a bench; flight ops often separate "sensor invalid" from "limit violated" to avoid a red on a dead sensor. Record it as a deliberate divergence.

### F4 – Derived parameters: XTCE has MathAlgorithm and triggered algorithms, ECSS wants an expression language, Yamcs retains stale inputs [S, V]

- **XTCE (§6.1.2.6, p. 20):** "A Math Algorithm is a simple mathematical operation with two operands (each of which may be a fixed or a parameter instance value) and an operand [sic]". An `InputOutputTriggerAlgorithm` has triggers that "may be either periodic, or event based (new parameter or container instance)". Anything richer is an external algorithm.
- **ECSS clause 6.2.6 and Table 6-84:** synthetic parameters support triggers on update of contributing data or on an event, conditional branching, local and **persistent variables** (note 2: "the delta between the current value and the previous value"), "Mathematical, time and string functions", and units on constants with free mixing of compatible units. Annex B.7 (SPEL) defines the language.
- **Yamcs algorithms:** run on input update or periodically; inputs expose engineering value, raw value, monitoring result and range condition; outputs "can be calibrated and can generate alarms". **If an input has no new update, "the previous value of that parameter will be retained"**; an input missing at trigger time is `null`.
- **OpenC3:** a derived item has offset and size 0 and type `DERIVED`, filled by a `READ_CONVERSION`; of generic (Ruby/Python code) conversions it says they "are not a good long term solution".
- **Takeaway for Nyx:**
  - D-EXT-05's grammar (`+ − × ÷`, unary minus, parentheses) is **a subset of the XTCE MathOperation operators**, so `expr` converts mechanically to RPN. `rate_of` has no XTCE primitive (custom algorithm or `ChangeAlarmRanges`).
  - Nyx is **below ECSS 6.2.6** (no functions, conditionals, unit arithmetic; the only persistent state is `rate_of`). A scoping choice; the cheap gap with real use is **functions** (a rate magnitude needs `sqrt`).
  - Nyx computes every derived value from the **same frame** and gives NULL for a missing input (D-EXT-05, D-ALM-04). That avoids the Yamcs hazard where a stale input silently keeps a derived value alive. A strength; pin it with a test (M4).
  - Whitelisted `ast` instead of free code agrees with the OpenC3 warning.

### F5 – Context-dependent alarms: XTCE ContextAlarm is an ordered, first-match list over any parameter; `checks_by_mode` is the special case on `mode` [S, V]

- **XTCE:** `NumericContextAlarmListType`: "An ordered collection of numeric alarms associated with a context. A context is an alarm definition on a parameter which is valid only in the case of a test on the value of other parameters. Process the contexts in list order." `ContextAlarmList`: "The first match that evaluates to true takes precedence." The context is a `ContextMatch` = `MatchCriteria`: `Comparison`, `ComparisonList` (implicit AND), `BooleanExpression`, or a custom algorithm. `NumericAlarmType` adds `StaticAlarmRanges`, `ChangeAlarmRanges` and a `minViolations` count ("the counts to go out of alarm is the same").
- **Alarm ranges:** up to six, "less severe ranges are a subset of more severe ranges (outside), or more severe ranges are a subset of less severe ranges (inside)"; each range is a `FloatRangeType` with **explicit `minInclusive/minExclusive/maxInclusive/maxExclusive`**.
- **Yamcs:** same model ("Contextual alarms are evaluated from top to bottom, until a match is found. If no context conditions apply, the default context applies"); results `IN_LIMITS, WATCH, WARNING, DISTRESS, CRITICAL, SEVERE`; `minViolations` defaults to 1 and "affects only event generation, not the monitoring result". The parameter-archive page says the applicable alarm ranges are stored alongside each value.
- **ECSS Tables 6-85 and 6-87:** an **ordered list of limit sets, each with a Validity Condition** "such as the mission phase or the status (operational or standby) of the associated unit", "the first one in the list is applied"; limits are danger and warning, each with a **corrective activity**.
- **OpenC3:** **limits sets** (`DEFAULT` first, then named sets such as thermal vacuum); `persistence` = consecutive samples before a state change; "A value exactly equal to a limit is placed in the outer (more severe) state"; `LIMITS_GROUP` enable/disable per subsystem. **One set is active at a time, switched by a call (`set_limits_set`); the pages read describe no telemetry-driven selection.**
- **Nyx vs this:**
  - `checks_by_mode` + default `checks` = XTCE/Yamcs context list with one context type = ECSS validity-conditioned limit sets with one condition type. Same expressiveness for the use case.
  - Nyx is **stronger than XTCE/Yamcs in one way**: the mode comes from the *same frame* as the value, so no "mode last seen 3 s ago" race, and replay picks the same set. It is stronger than OpenC3 (operator-selected set, not replayable).
  - **ECSS's data model has no hysteresis or persistence** (no match in the extracted text); XTCE has only `minViolations`. Nyx's deadband and persistence (D-ALM-02/03) go beyond the standards; do not export them as if XTCE had them.
  - **On-limit rule:** D-ALM-01 lists "Yamcs/XTCE default (on-limit = nominal)" as the rejected alternative. XTCE has **no single default**: each range chooses inclusive or exclusive. OpenC3 matches Nyx (outer state). Nyx's rule is representable in XTCE; the design's wording about XTCE is stronger than the schema supports.

### F6 – "Not valid in this mode": XTCE and ECSS have validity conditions; Nyx has only checks-by-mode [S]

- **XTCE:** `ParameterProperties/ValidityCondition`: "Optional condition that must be true for this Parameter to be valid" (a `MatchCriteria`).
- **ECSS Table 6-73:** **validity conditions**, "The expression whose result determines whether interpretation of the reporting data is meaningful in the given domain"; and clause 6.4.8 d.4 above.
- **Nyx:** a parameter that is off in STANDBY (an unpowered sensor) can only get (a) the default `checks`, and a false alarm, or (b) a `checks_by_mode` entry with **no** checks. D-EXT-06 does not say whether `checks_by_mode: {STANDBY: []}` is legal or means "fall back to default". That ambiguity is the real gap.
- **Does it matter for a campaign?** A little: campaigns switch TEST/SAFE/STANDBY on purpose, and nuisance reds on a parameter that means nothing in a mode are what ALM-02 tries to avoid. The cheap form (an explicit empty list) costs nothing in the schema.

### F7 – Command preconditions: all four have the concept and check the latest telemetry; none of the docs read states a freshness rule [S, V]

- **XTCE (§6.1.3.2.4, p. 24; XSD `TransmissionConstraintType`):** "A CommandTransmission constraint is used to check that the command can be run in the current operating mode and may block the transmission of the command if the constraint condition is true." It is a `MatchCriteria` over parameters (or a custom algorithm), with `timeOut` ("Pause during timeOut, fail when the timeout passes") and `suspendable`. **"Block ... if the constraint condition is true" is ambiguous** (it reads as if a true condition blocks), whereas Yamcs's page says constraints are "conditions that have to be valid in order to send a command". An export needs a test for the sense.
- **Significance (p. 24):** levels `none, watch, warning, distress, critical, severe`, and "different significance levels set as driven by the operating context" (`ContextSignificanceList`). Nyx's `hazardous: bool` is one default level.
- **Interlock (p. 24):** blocks following commands until this command reached a verification stage; this is D-CMD-07.
- **Verifiers (p. 24):** the book lists eight states (TransferredToRange, TransferredFromRange, Received, Accepted, Queued, Execution, Complete, Failed); the XSD enumeration I read has nine (`release, transferredToRange, sentFromRange, received, accepted, queued, executing, complete, failed`). The `CheckWindow` is "a time period where the verification must test true to pass".
- **ECSS:** Table 6-93 puts **Pre-Transmission Validation (PTV)** in state 2 for telecommands; Table 6-106: "PTV Condition ... whether the initiation of the telecommand is allowed"; the PTV "applies only to the telecommand itself". Acceptance by the destination is a *separate*, later state (PUS service 1). Table 6-89 gives each activity a **criticality** `{non critical, locally critical, mission critical}`.
- **Yamcs:** constraints are "usually checked against telemetry"; with no delay the cached values are checked and the command fails if not met; with a delay they are re-checked "over a configured interval". **They can be disabled in the API request, but only with the CommandOptions privilege** (command-processing page). Verifiers run after release; the first to declare completion aborts the others. The XTCE loader page notes XTCE requires **all** `CompleteVerifiers` to pass while Yamcs completes on the first.
- **OpenC3:** `HAZARDOUS` adds a confirmation dialog; the nearest precondition is a `VALIDATOR` class with `pre_check` (can block based on current telemetry) and `post_check`; the command page read has no `CONSTRAINT` keyword. Enterprise adds Command Authority and Critical Commanding.
- **Nyx vs this (D-EXT-08):**
  - Checking `mode` against the latest stored frame before the hazard step = XTCE TransmissionConstraint = ECSS PTV = OpenC3 `pre_check`. Matches.
  - **Nyx adds a freshness rule** (mode older than 2 s gives `MODE_UNKNOWN`). None of the docs read states one; Yamcs's "cached parameter values" can be arbitrarily old if the link is down. Stricter, deliberate (EX-14).
  - **No wait.** XTCE and Yamcs can pause until a constraint holds. Nyx refuses at once and the operator retries. Fine for a bench.
  - **No override.** Yamcs lets a privileged user bypass constraints. Nyx's answer is "don't restrict safing commands" (A-EXT-7). Acceptable; record it as an owner decision.
  - ECSS separates **ground PTV from on-board acceptance**. A-EXT-7 says "the vehicle stays the final authority", but D-EXT-07/08 commit only the **ground** check (and the simulator enforcing arg bounds, D-ENG-17). A command that passes the ground check at t and meets another mode on board at t + 1 s is the TOCTOU case only the vehicle can close. The simulator should reject a command whose `allowed_modes` lacks its current mode, through the normal verification path, and a test should inject past the API.
  - Only `mode` is a constraint input; XTCE allows any parameter and boolean combinations (`ComparisonList` is AND-only, so "mode in [TEST, SAFE]" needs a `BooleanExpression` on export).

### F8 – FROZEN over raw: no standard has it; raw is the right basis when calibration can change [S, V + design reasoning]

- XTCE, ECSS and the Yamcs/OpenC3 pages read define no frozen-value detector (OpenC3 has change-based limits `SEQUENCE/CHANGE/STATIC`, a per-item check, not a link-level freeze flag). D-EXT-09 is Nyx's own rule and cannot be validated against a standard.
- **Reasoning:** an engineering value is a deterministic function of the raw value and the set in force. Raw bit-identical for 2 s implies engineering-identical under one set. A **calibration reload** changes the engineering value with identical raw, so a rule over engineering values would flap or be masked at a reload; a rule over raw is immune. D-EXT-09 is sound; no change.

### F9 – Versioning of calibration: the standards say little; Nyx's hash-per-set goes further than anything I could confirm [S, V]

- I found no calibration versioning clause in the XTCE book text (calibration appears only in encoding descriptions) or in the ECSS tables read (the `Domain Applicability` flags AIV/Operations/Software select data per customer, not versions). The Yamcs parameter-archive page says nothing on a calibration change (not stated; Open questions). Yamcs 5.8.8+ can write MDB sub-trees "saved to disk in XTCE format" (mdb page); I found no version identifier.
- **Nyx** records `set_sha256` on alarms and transitions, `LIMITS_APPLIED(first_seq, …)` in the event stream, stores raw, and keeps every set's bytes in `limits_versions` (D-EXT-02). Raw plus the set at `first_seq` recompute any stored engineering value later. Gaps in F10.

### F10 – Safety points for flight-ops style use (question 4)

1. **Which value is stored and checked.** Nyx stores raw (`<name>_raw`) and engineering (`<name>`) and checks engineering. Matches OpenC3, XTCE defaults and Yamcs's two-value archive.
2. **Mixed calibrations in one column.** After a reload, rows before and after use different curves in one column. The sets are recoverable by `first_seq`, but **nothing in export, compare or charts flags a window spanning two `parameters_sha256` values.** A user comparing sessions across a recalibration sees a step that is not physical. EX-25 already audits `hazardous` changes in `LIMITS_RELOADED`; it should also list **parameters whose calibration table or unit changed** (M1).
3. **Units.** XTCE and Yamcs put units on the type; ECSS gives units to engineering values. In Nyx `unit` is in `parameters.yaml` and limits are bare numbers. A reload that changes `unit` (kPa to Pa) with a new table but an untouched `limits.yaml` loads, unless the D-EXT-04 coverage check (table range covers red limits plus deadband) fails. It would for 1000x, not for a mild change. A unit change should be loud (M1).
4. **Out-of-range raw.** Covered (F2, F3). Nyx's RED is conservative. If a table is later widened, a formerly RED pegged value becomes a valid reading; that is a calibration change, so M1 covers it.
5. **Derived from invalid input.** An out-of-range calibrated input must give NULL for the derived value too. D-EXT-05 lists "missing input", D-ALM-04 lists "raw outside its calibration table"; the two meet at "derived input is NULL". Test it explicitly (M4).
6. **A sensor invalid by mode (F6)** must neither alarm nor look nominal.

### F11 – What an XTCE export would cost and buy [S for the target model; cost is design judgement]

| Nyx element | XTCE target | Fidelity |
| --- | --- | --- |
| `parameters.<name>` (`unit`, `type: float`) | `FloatParameterType` + `UnitSet`, `Parameter` | exact. The name regex `^[a-z][a-z0-9_]{0,62}$` is inside XTCE `NameType` (forbids `. / : [ ] space`) |
| `table: [[raw, eng], …]` | `SplineCalibrator` order 1 in the data encoding | exact; "no extrapolation" is a Nyx rule (XTCE extrapolates), so also emit a `ValidRange` on the raw value (`validRangeAppliesToCalibrated="false"`) |
| `limit` check, YELLOW/RED | `StaticAlarmRanges`: Warning / Critical with explicit inclusive attributes | exact on bands; YELLOW to Warning, RED to Critical is a judgement |
| `persistence` N | `minViolations` | exact |
| `hysteresis_pct` / `hysteresis_abs` | none seen | **lost**; ECSS has none either |
| `roc` check | `ChangeAlarmRanges` (`changePerSecond`, span of interest) | close; the ≥ 80 % samples rule is not representable |
| `checks_by_mode` | `ContextAlarmList` with `Comparison(mode == X)`; `mode` exported as an enumerated parameter | exact |
| `derived.expr` | `MathAlgorithm` / `InputOutputTriggerAlgorithm` with RPN `MathOperation` | mechanical (infix to postfix) |
| `derived.rate_of` | custom algorithm | **no portable form** |
| `allowed_modes` | `TransmissionConstraint` with `Comparison` or `BooleanExpression` | exact; test the "block if true" sense (F7) |
| `hazardous` + `hazard` | `DefaultSignificance` (`critical`) | lossy (1 of 6 levels) |
| `receive/verify_timeout_s` | `Received` / `Complete` verifiers with `CheckWindow` | exact enough |
| FROZEN, `check_set` rows, `set_sha256`, deadband floor | none | outside XTCE |

- **Cost (judgement):** export only: one pure function `to_xtce(MissionDb) -> bytes` (about 200 lines), **one unit test that validates the output against the published `SpaceSystem.xsd`** (offline, deterministic) and one golden file. Roughly 1 to 1.5 days. **Import** is a different order of work: XTCE has many encodings and containers Nyx has no model for, and Yamcs's own XTCE loader lists unsupported items (`OnContainerUpdateTrigger`, `ToBinaryTransformAlgorithm`, alarms on `StringParameterType`), so even a mature reader is a subset. Do not build import.
- **Buy:** interop evidence ("a Yamcs instance can load Nyx's database"), a hard check that D-EXT-01..08 only use concepts the standard has (the lossy rows are the honest list of Nyx extensions), and a credible story for the take-home audience. It buys **no** run-time behaviour; the 47 requirements and ATPs are unaffected.
- **Optional smoke test:** loading the export into a stock Yamcs would prove more than schema validity; not done here.

## Decision candidate

Ranked for EXT-01's remaining parts. "Touches" lists the D-IDs the implementer session would amend. None changes a must-requirement's pass condition.

### Must (decide or pin before the part is built; each is small)

| # | Recommendation | Touches | Why |
| --- | --- | --- | --- |
| M1 | **Calibration and unit changes are audited events.** `LIMITS_RELOADED` (or `LIMITS_APPLIED`) lists every parameter whose calibration table or `unit` changed; export and compare headers print `parameters_sha256` per segment and flag a window spanning two values. Stored engineering values stay as computed; raw is the repair path. | D-EXT-02, D-EXT-04, EX-25, GS-EXT-02 | F9, F10.2, F10.3 |
| M2 | **The simulator enforces `allowed_modes` itself** (normal REJECTED path), and a test injects a command past the API to show the vehicle, not only the ground, refuses it. | D-EXT-07, D-EXT-08, A-EXT-7, D-ENG-17 | F7 |
| M3 | **`checks_by_mode: {MODE: []}` is legal and means "not monitored in that mode"** (slot leaves, forced NOMINAL, `MODE_CHANGED`); an absent mode still falls back to `checks`. Write it into D-EXT-06 now: the model "grows with each feature", so a later change is a schema change. | D-EXT-06, D-EXT-03 | F5, F6 |
| M4 | **Pin NULL propagation with tests:** an out-of-range calibrated input gives a NULL derived value (never the last value), and a `rate_of` window resets on a NULL input. | D-EXT-05, D-EXT-04, D-ALM-04 | F4, F10.5 |
| M5 | **Keep as designed, with the reason written down:** table-only with "outside = invalid and RED" (= ECSS `invalid`), FROZEN over raw, same-frame mode for context checks, 2 s freshness on `allowed_modes`. | D-EXT-04, 06, 08, 09 | F2, F5, F7, F8 |

### Should

| # | Recommendation | Touches | Why |
| --- | --- | --- | --- |
| S1 | **Add a polynomial calibration with a mandatory `raw_range: [lo, hi]`**; outside it, the same NULL + RED path as a table. XTCE, Yamcs and OpenC3 all have it and real sensors ship as coefficients; the mandatory range removes the usual hazard (a cubic diverges outside its fit). One pure function, one boundary-table test. This reverses EX-22's "no user": the owner's goal ("works like real field software") is the user. | D-EXT-04, A-EXT-5, EX-22 | F1, F2, F3 |
| S2 | **Whitelist a few functions in `expr`:** `abs`, `min`, `max`, `sqrt` as `ast.Call` on a fixed name set, same node and depth limits, non-finite to NULL. Needed for vector magnitudes; ECSS 6.2.6 asks for math functions. | D-EXT-05, EX-11 | F4 |
| S3 | **One-way XTCE export**, validated against the published XSD in a unit test; document the lossy rows (F11). No import. | D-EXT-01, §5 cut list | F11 |
| S4 | **Fix the D-ALM-01 comparison text:** XTCE ranges choose inclusive/exclusive per range; there is no XTCE default of "on-limit = nominal". Nyx's rule matches OpenC3 and is representable in XTCE. | D-ALM-01 | F5 |

### Could

| # | Recommendation | Touches | Why |
| --- | --- | --- | --- |
| C1 | `valid_modes: [..]` per parameter (XTCE `ValidityCondition`, ECSS Table 6-73): stored, shown "not valid in this mode", derived values NULL. Only if M3 proves too coarse. | D-EXT-03, D-EXT-05 | F6 |
| C2 | Mode-dependent significance (`ContextSignificance`): hazardous only in some modes. | D-EXT-07, D-CMD-06 | F7 |
| C3 | Audited override of `allowed_modes` for a privileged role with a reason (Yamcs `CommandOptions`). Default: **no**. | D-EXT-08 | F7 |
| C4 | Conditional calibration (`ContextCalibrator`, ECSS selection condition) keyed on `mode`, only if a sensor changes gain by mode. | D-EXT-04 | F1, F2 |
| C5 | A reload dry run listing which parameters, checks and commands would change (M1 as a preview). | D-EXT-02, D-ALM-12 | F10 |

## Open questions → assumptions.md

Proposed entries (IDs are suggestions; the implementer session merges them, this note does not edit assumptions.md):

| ID | Assumption / question | Why / impact if wrong | Status | Source |
| --- | --- | --- | --- | --- |
| A-R23-1 | Stored engineering values are never recomputed after a calibration change; history before and after stays as computed, raw is the repair path, and M1 flags the boundary. | A user expecting a continuous series across a recalibration is misled by charts and compare. | OPEN (owner) | R23 F9, F10 |
| A-R23-2 | A polynomial calibration, when built, requires `raw_range`; outside it the table path (NULL + RED) applies. | A polynomial with no range extrapolates silently. | OPEN | R23 S1 |
| A-R23-3 | `checks_by_mode: {MODE: []}` means "not monitored in that mode". | Without it a parameter unpowered in STANDBY alarms. | OPEN | R23 M3 |
| A-R23-4 | The simulator enforces `allowed_modes`; the ground check is not the only line. | A-EXT-7's "the vehicle stays the final authority" has no test otherwise. | OPEN | R23 M2 |
| A-R23-5 | No override of `allowed_modes` exists; safing commands ship unrestricted. | A real ops team may need an audited override; a bench does not. | ASSUMED (owner to confirm) | R23 C3 |
| A-R23-6 | An XTCE export maps YELLOW to Warning and RED to Critical; hysteresis, `check_set`, FROZEN and the deadband floor are not representable. | A consumer sees a weaker alarm model than Nyx runs. | ASSUMED | R23 F11 |
| A-R23-7 | Not verified here: whether Yamcs alarms and calibrator contexts apply to the calibrated value only (the alarm-sheet page does not say), how Yamcs treats an algorithm input outside its valid range, and whether XTCE's remaining alarm types carry hysteresis. Verify by running Yamcs or reading its source before the export claims equivalence. | The "lost" hysteresis row in F11 means "not seen in the types read", not "absent from XTCE". | OPEN | R23 F5, F11 |
| A-R23-8 | ECSS "fully populated" items d.1 to d.7 sit under clause 6.4.8 in the text extraction (no 6.4.9 heading appeared); confirm the clause number against the PDF before quoting it in a deliverable. | A wrong clause number in the evidence. | OPEN | R23 F2 |

## Sources

- CCSDS 660.0-B-2, XML Telemetric and Command Exchange (XTCE) v1.2, Blue Book, February 2020 (OMG formal/18-10-04). https://ccsds.org/Pubs/660x0b2.pdf, read 2026-11-21 [S]. Used: §6.1.2.1 (p. 16), §6.1.2.6 AlgorithmSet (p. 20), §6.1.3.2.4 to §6.1.3.2.8 (p. 24).
- OMG XTCE 1.2 schema `SpaceSystem.xsd`, 2018-02-04. https://www.omg.org/spec/XTCE/20180204/SpaceSystem.xsd, read 2026-11-21 [S]. Elements used: `CalibratorType`, `SplineCalibratorType`, `SplinePointType`, `MathOperationCalibratorType`, `MathOperatorsType`, `ContextCalibratorType`, `AlarmRangesType`, `FloatRangeType`, `NumericAlarmType`, `NumericContextAlarmListType`, `AlarmType` (`minViolations`), `ChangeAlarmRangesType`, `ParameterInstanceRefType` (`useCalibratedValue`), `ParameterPropertiesType` (`ValidityCondition`), `ValidRange` (`validRangeAppliesToCalibrated`), `TransmissionConstraintType`, `VerifierEnumerationType`, `CommandVerifierType`, `ContextSignificanceListType`, `NameType`.
- ECSS-E-ST-70-31C, Ground systems and operations, Monitoring and control data definition, 31 July 2008. https://ecss.nl/standard/ecss-e-st-70-31c-ground-systems-and-operations-monitoring-and-control-data-definition/ (download link on that page), read 2026-11-21 [S]. Used: clause 6.2.6, clause 6.4.8 (numbering to verify), Tables 6-73, 6-77, 6-78, 6-84, 6-85, 6-87, 6-89, 6-93, 6-106, Annex B.6 and B.7 (headings only).
- Yamcs Server Manual: Mission Database overview, Data Types, Alarm Definitions, Algorithm Definitions, Command Definitions; XTCE loader; Spreadsheet loader, Alarms sheet; Processors, Command Processing; Data Management, Parameter Archive. https://docs.yamcs.org/yamcs-server-manual/, read 2026-11-21 [V].
- OpenC3 COSMOS documentation: Telemetry, Command and Conversions configuration pages; Scripting API Guide (search listing only, for `set_limits_set`). https://docs.openc3.com/docs/, read 2026-11-21 [V].
