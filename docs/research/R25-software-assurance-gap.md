# R25 – Software assurance for a ground segment: ECSS-E-ST-40C / ECSS-Q-ST-80C and NPR 7150.2D against what Nyx has

**Drives:** D-ENG-14/15 (test with every change, coverage gate), `docs/requirements.md`, `docs/atp-report.md`, `docs/ncr/`, `docs/reviews/`, CI (`.github/workflows/ci.yml`), EXT-03   **Asked by:** coordinator (nyx-c3), 2026-11-21

**Evidence levels:**
- **[S]** standard text, quoted with clause and page;
- **[C]** Nyx files read on `origin/main` at 0b664ff;
- **[A]** analysis, mine.

The standards were fetched on 2026-11-21 and saved in `papers/web/` (git-ignored; URLs in [SOURCES.md](SOURCES.md)). PaperLab was unavailable and wasn't needed: the standards are the primary sources.

## Question

Which criticality category or class would a space customer assign to Nyx's software? What evidence do ECSS-E-ST-40C, ECSS-Q-ST-80C and NPR 7150.2D then require? Which of it does Nyx already have, and what is missing, cheapest first?

## F1 – Classification: ECSS category C (candidate), NASA Class C, with a safety-critical determination still owed (gap, cheap)

**The standards apply to ground software.**
- [S] ECSS-E-ST-40C (6 March 2009), §1 Scope, p. 12: the Standard "is applicable, to the extent defined by the tailoring process, to all the elements of a space system, including the space segment, the launch service segment and the ground segment".
- [S] ECSS-E-ST-70C (31 July 2008), Annex K.2, p. 123, lists among the ground software systems "the EGSE system" and "simulation, test and training tools". Nyx monitors and commands a vehicle during test campaigns: EGSE-class ground software.

**ECSS category.**
- [S] ECSS-Q-ST-80C Rev.1 (15 February 2017), Annex D.1, Table D-1, pp. 90–91: the category comes from the severity of the functions the software is involved in (I–IV, defined in ECSS-Q-ST-30/-40, not read here) and from compensating provisions.
  - Category B is "Software involved in category I functions AND: at least one of the following compensating provisions is available … A hardware implementation … An operational procedure", or "Software involved in category II functions AND: no compensating provisions exist".
  - Category C is the same pattern one level down.
- [A] Nyx's worst function is `vent_cabin`, "Irreversible loss of cabin atmosphere" (`mission/commands.yaml`). In a test campaign that means loss or damage of the test article, not of crew, so a critical or major consequence. The compensating provisions are the test conductor's procedure and the bench's own interlocks. **Candidate: category C.** Category B applies only if the system hazard analysis rates the vent as critical (II) with no compensating provision. That is for the owner and the customer's safety team to confirm.

**NASA class.**
- [S] NPR 7150.2D (effective 8 March 2022), Appendix D, pp. 82–83. Class C covers "(d) Software used for the testing of space assets", with the example "Software that supports prelaunch integration and test". Class B explicitly excludes "Systems (e.g., simulators, emulators, facilities) used in testing Class B systems containing software in a development environment". **Nyx is Class C.**
- [S] Separately, SWE-205 (§3.7.1, p. 26): "The project manager … shall determine if each software component is considered to be safety-critical per the criteria defined in NASA-STD-8739.8." If the commanding path counts as safety-critical, SWE-134 applies (see [R24](R24-command-verification-pus.md)), and so does SWE-219 (§3.7.4, p. 27): "100 percent code test coverage using the Modified Condition/Decision Coverage (MC/DC) criterion for all identified safety-critical software components".

**Gap and change.**
- [C] No document states Nyx's category, class or safety-critical components.
- **Change:** `docs/assurance/criticality.md`, about 1 h, holding:
  - the candidate ECSS category and the reasoning above;
  - NASA Class C;
  - a per-component safety-critical determination. Candidates: `gs.commanding` (args check, `decide`, outcome), the API's confirm token and interlock, and the alarm engine (a missed RED is a hazard to the test). Everything else not safety-critical.

  Every later item takes its scope from this file.

## F2 – Coverage: line only; the standards measure statements and decisions, and MC/DC for safety-critical parts (gap, cheap to medium)

- [S] ECSS-E-ST-40C §5.8.3.5b, p. 64, sets code coverage by criticality:

  | Coverage | A | B | C | D |
  | --- | --- | --- | --- | --- |
  | Statement | 100% | 100% | AM | AM |
  | Decision | 100% | 100% | AM | AM |
  | MC/DC | 100% | AM | AM | AM |

  "'AM' means that the value is agreed with the customer and measured as per ECSS‐Q‐ST‐80 clause 6.3.5.2". The expected output is a "Code coverage verification report [DJF, SVR; CDR, QR, AR]". §5.8.3.5c: coverage "shall be measured by analysis of the results of the execution of tests".
- [S] NPR 7150.2D:
  - SWE-189 (§4.5.9, p. 36): coverage measurements are "selected, implemented, tracked, recorded, and reported";
  - SWE-190 (§4.5.10, p. 37): "measured by analysis of the results of the execution of tests";
  - SWE-219 for safety-critical components (F1).
- [C] What Nyx has:
  - CI runs `pytest --cov=gs --cov-fail-under=80`. `pyproject.toml` has no `[tool.coverage]` section and no `branch = true`, so the gate counts **lines**, not decisions.
  - `tests/meta/test_except_coverage.py` checks that every except handler runs (D-ENG-21). Decision coverage itself is not measured.
- **Change, cheapest first:**
  1. Turn on branch coverage (`[tool.coverage.run] branch = true`) and report the statement and decision percentages in CI. One config line. The 80 % figure becomes the "AM" value agreed for category C.
  2. A **per-module gate of 100 % statement and decision** for the F1 safety-critical components, through `coverage report --include=… --fail-under=100` as a second CI step. That is category B's level, applied only where it matters.
  3. **MC/DC for `decide()`, the args check and the confirm check** (SWE-219), by test design: one table row per condition showing that condition independently changes the outcome. A short analysis note goes with it in the coverage report. [A] I know of no maintained MC/DC measurement tool for Python, so the evidence is the test table plus the analysis that NPR and ECSS both accept: E-40C §5.8.3.5d allows "analysis, inspection or review of design" where tests can't reach the percentage.

## F3 – Trace: requirements → decisions → tests → ATP exist; hazards and the reverse direction are missing (gap, cheap)

- [S] NPR 7150.2D SWE-052 (§3.12.1, Table 1, p. 31) requires two-way traceability for Class A, B and C:
  - higher-level → software requirements;
  - software requirements → **system hazards**;
  - software requirements → software design components;
  - **design components → code**;
  - software requirements → verification;
  - software requirements → non-conformances.
- [C] What `docs/requirements.md` has: 52 requirement rows, each with method (T/D/I/A), decisions, planned evidence, ATP and status.
  - The NCRs name their requirement (`NCR-02.md`: "Requirement: ENG-06").
  - There is no hazard column and no hazard list.
  - Design → code is only implicit, through D-IDs mentioned in code comments.
- **Changes:**
  1. A short **hazard list** (H-1 inadvertent vent, H-2 missed RED alarm, H-3 false or late LOS, H-4 wrong command outcome shown, H-5 data loss in archive), with a "Hazards" column in `requirements.md`.
  2. A generated **code → D-ID index**: `grep` for `D-[A-Z]+-[0-9]+` in `src/`, inverted, written as a table, with a meta test that fails when a D-ID referenced in code doesn't exist in `design.md`.

  Both are a few hours, and both run automatically.

## F4 – Verification report generated from the actual run (gap, medium; the highest-value customer document)

- [S] ECSS-E-ST-40C:
  - Annex M is the Software verification report (SVR, p. 149);
  - Annex L is the validation specification (SVS, p. 142);
  - the coverage report in F2 is part of the SVR.
- [S] NPR SWE-052: "Software requirements to the software verification(s)" in both directions.
- [C] `docs/atp-report.md` is a strong, hand-written validation record: per ATP, the requirements, the result and the raw evidence. `requirements.md` names the **planned** test for each requirement. Nothing checks that the named test exists and **passed in this run**.
- **Change:** `scripts/verification_matrix.py`, run after the test jobs in CI. It:
  - reads pytest JUnit XML and Playwright JSON;
  - maps each requirement's named test IDs to pass, fail or missing;
  - writes `verification-matrix.md` as a CI artifact per commit, and per release tag into `docs/atp/records/`.

  CI fails when a requirement's named test no longer exists (drift). This turns the trace into an as-run SVR without new manual work.

## F5 – Security assessment and dependency evidence (gap, cheap)

- [S] NPR 7150.2D:
  - SWE-135 (§4.4.4, p. 34): "use static analysis tools … to, at a minimum, detect defects, software security, code coverage, and software complexity";
  - SWE-156 (§3.11.2, p. 30): "perform a software cybersecurity assessment … including risks posed by the use of COTS, GOTS, MOTS, OSS, or reused software components";
  - SWE-159 (§3.11.5): test the identified mitigations.
- [C] What Nyx has:
  - ruff with `E, F, I, UP, B, BLE001, S110, FIX` and `mypy --strict` cover defects;
  - Redis ACLs with an ACL-log check, an append-only audit log, a session cookie and the `X-GS-Client` header check cover security design.
  - Missing: a security rule set (only `S110` of bandit's `S` family), a complexity metric, a dependency vulnerability scan, an SBOM, and a written assessment.
- **Change:**
  - **Static analysis:** add ruff `S` (bandit rules) and `C901` (complexity), with justified per-line ignores.
  - **Dependency scans:** add `pip-audit` (or `uv`'s audit) and `npm audit --omit=dev` to CI.
  - **SBOM:** a CycloneDX SBOM per release tag.
  - **Assessment:** a one-page `docs/assurance/security-assessment.md` covering the threat model (loopback deployment, single site), the controls with their tests, and the OSS risks (pinned versions, lockfiles).

  About half a day in total.

## F6 – Plans, release record and review map: practice exists, the documents a customer files don't (gap, cheap, documentation only)

[S] ECSS-E-ST-40C asks for:
- a Software development plan (Annex O, SDP, p. 160);
- a Software release document (Annex G, SRelD, p. 116);
- a Software user manual (Annex H, SUM, p. 119);
- a review plan (Annex P, SRevP, p. 166).

[S] ECSS-Q-ST-80C asks for a Software product assurance plan (Annex B, SPAP, p. 81).

[C] Nyx has most of the substance, under other names:
- `operator-guide.md` and `dev-guide.md` are the SUM;
- `CHANGELOG.md` with tags v1.0 and v1.0.1 is release history;
- the reviews (`design-review-1..3`, `alternatives-review`, `extensions-review`, `simplification-review`) are review records;
- NCR-01..04 carry an owner disposition each, and `bug-log.md` exists;
- the TDD and test-with-every-change rule (D-ENG-14/15), a review per task and the CI gates are its assurance practice.

**Change:**
- **SPAP and SDP:** one page, `docs/assurance/plan.md`, mapping each existing practice to the Q-80 / E-40 clause it satisfies, with the reviews mapped onto SRR/PDR/CDR/QR/AR.
- **SRelD:** per release, the commit, image digests, lockfile hashes, **open NCRs and known problems**, and links to the ATP report and verification matrix. The CHANGELOG has the changes but not the open problems or the configuration.

These are the first documents a customer's product-assurance engineer asks for. They take hours, not days.

## What Nyx already has that a customer will credit (no change)

| Evidence | Nyx artefact | Standard it answers |
| --- | --- | --- |
| Requirement-based acceptance with raw data and confidence intervals | `docs/atp-report.md`, `docs/perf/*`, `docs/atp/records/` | E-40C SVS/SVR (validation), NPR SWE-052 (req → verification) |
| Non-conformance handling with disposition | `docs/ncr/NCR-01..04` (requirement, impact, severity, owner decision) | Q-80C NCR process, NPR SWE-052 (req → non-conformance) |
| Regression testing on every change | CI: unit, integration, system, e2e; test with every change (D-ENG-14) | NPR SWE-191 |
| Static analysis | ruff, `mypy --strict`, exception-handler coverage meta test | NPR SWE-135 (defects part) |
| Change tracking | git history, conventional commits checked in CI (`check_commits.py`), CHANGELOG | NPR SWE-080 |
| Fault injection and soak | chaos soak and stall trials (§11.5, NCR-01), property tests (EXT-03, in progress) | E-40C robustness verification |

## Decision candidate

- **Chosen, cheapest first:**
  1. **F1:** a criticality file.
  2. **F2 (1):** branch coverage on.
  3. **F3:** a hazard list and the code → D-ID index.
  4. **F6:** the plan and the release record.
  5. **F5:** security scans and the assessment.
  6. **F2 (2–3):** 100 % statement and decision, plus MC/DC by test design, on the safety-critical slice.
  7. **F4:** the generated verification matrix.
- **Because:** together they let Nyx answer a product-assurance review in the standards' own terms. Most items are documents or a CI flag on top of practice Nyx already follows. F4 is the largest and the most convincing: an as-run verification matrix per commit.
- **Rejected:**
  - Writing every E-40C DRD in full. Tailoring is explicitly allowed (E-40C Annex R; "to the extent defined by the tailoring process"), so map instead.
  - 100 % MC/DC everywhere: not required for category C, and NPR asks for it only for safety-critical components.

## Open questions → assumptions.md

- A-R25-1: Category C (ECSS) and Class C (NASA). Confirm with the owner, and with a customer's safety team if one exists.
- A-R25-2: Which components are safety-critical (F1 list)? Default: commanding path and alarm engine.
- A-R25-3: The agreed ("AM") coverage for category C. Default: 80 % statement and decision overall, 100 % on the safety-critical slice.

## Sources

- ECSS-E-ST-40C, Software (6 March 2009): https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-40C6March2009.pdf. Cited: §1 p. 12, §5.8.3.5 p. 64, Annexes G p. 116, H p. 119, L p. 142, M p. 149, O p. 160, P p. 166, R.
- ECSS-Q-ST-80C Rev.1, Software product assurance (15 February 2017): https://ecss.nl/wp-content/uploads/2017/04/ECSS-Q-ST-80C-Rev.1(15February2017).pdf. Cited: Annex B p. 81, Annex D.1 / Table D-1 pp. 90–91.
- ECSS-E-ST-70C, Ground systems and operations (31 July 2008): https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-70C31July2008.pdf. Cited: Annex K.2 p. 123.
- NPR 7150.2D, NASA Software Engineering Requirements (effective 8 March 2022): https://nodis3.gsfc.nasa.gov/npg_img/N_PR_7150_002D_/N_PR_7150_002D_.pdf. Cited: SWE-052 p. 31, SWE-080 p. 39, SWE-135 p. 34, SWE-156 / SWE-159 p. 30, SWE-189 p. 36, SWE-190 / SWE-191 p. 37, SWE-205 p. 26, SWE-219 p. 27, Appendix D pp. 82–83.
- NASA-STD-8739.8B (approved 2022-09-08), the safety-critical criteria referenced by SWE-205 (see R24).
- Nyx: `pyproject.toml`, `.github/workflows/ci.yml`, `docs/requirements.md`, `docs/ncr/`, `docs/reviews/`, `docs/atp-report.md`, `CHANGELOG.md`, `mission/commands.yaml`.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
