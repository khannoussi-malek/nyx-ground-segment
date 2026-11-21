# Software assurance plan map (SPAP / SDP), candidate

One page. It maps each practice Nyx already follows to the clause it satisfies, so the practice can be shown
to a product-assurance review in the standards' terms (R25 F6,
[R25](../research/R25-software-assurance-gap.md)). It is the SPAP (ECSS-Q-ST-80C Annex B, p. 81) and SDP
(ECSS-E-ST-40C Annex O, p. 160) content in one place, not a new process. The ECSS category (C) and NASA class (C)
are **candidates for the owner to confirm**: see [criticality.md](criticality.md). Clauses are only those R25
reads and cites; any other cell says "no clause cited in R25". A row marked "Annex" cites the annex, not a numbered
requirement.

| Practice (where it lives) | ECSS-E-ST-40C / Q-ST-80C | NPR 7150.2D |
| --- | --- | --- |
| Test first, a test in the same commit as the change (D-ENG-14/15, `docs/requirements.md`) | Annex M (SVR); no other clause cited in R25 | SWE-052 requirements to verification |
| One review per task, a final whole-branch review (`.superpowers/sdd/`) | E-40C reviews, Annex P (SRevP) | no clause cited in R25 |
| Design and requirement reviews (`docs/reviews/*`) | Annex P (SRevP); the files are review records, not yet mapped to a milestone (R25 F6 asks for it, owed) | no clause cited in R25 |
| CI gates: ruff (incl. bandit `S`, `C901`), `mypy --strict`, generated-code check (`.github/workflows/ci.yml`) | no clause cited in R25 | SWE-135 static analysis |
| Dependency audit, SBOM, pinned locks ([security.md](security.md)) | no clause cited in R25 | SWE-156 cybersecurity assessment |
| Coverage gate 80 %, branch coverage, except-handler meta test (`pyproject.toml`, D-ENG-21) | E-40C 5.8.3.5 (coverage values for category C are "agreed with the customer"; 80 % is the current gate, its adequacy a candidate for the owner, A-R25-3) | SWE-189, SWE-190 |
| Fault-injection system tests (`tests/system`); property and chaos tests are planned (EXT-03), not built | no clause cited in R25 | no clause cited in R25 |
| Tests of the security CI gates ([security.md](security.md)) | no clause cited in R25 | SWE-159 (test the cybersecurity mitigations): not met, the gates are run in CI but no test checks that they fail on a bad input |
| ATPs and ATP report (`docs/atp/`, `docs/atp-report.md`) | E-40C Annex L (SVS), validation | SWE-052 requirements to verification |
| Requirement, decision, test trace (`docs/requirements.md`, [design-trace.md](design-trace.md)) | E-40C Annex M (SVR) | SWE-052 two-way trace |
| Hazard list and criticality ([hazards.md](hazards.md), [criticality.md](criticality.md)) | Q-80C Annex D.1, Table D-1 | SWE-205, SWE-052 requirements to hazards |
| NCRs with an owner decision and a Status line (`docs/ncr/`, `docs/bug-log.md`) | no clause cited in R25 | SWE-052 requirements to non-conformances |
| As-run verification matrix per CI run (`scripts/verification_matrix.py`, artifact `verification-matrix`) | E-40C Annex M (SVR) | SWE-052 requirements to verification |
| Release record on `v*` tags (`scripts/release_record.py`, artifact `release-record-<tag>`: commit, image IDs of a local rebuild, lockfile hashes, open NCRs, matrix) | E-40C Annex G (SRelD, p. 116) | no clause cited in R25 |
| Operator guide, dev guide (`docs/operator-guide.md`, `docs/dev-guide.md`) | E-40C Annex H (SUM, p. 119) | no clause cited in R25 |
| CHANGELOG with tags (`CHANGELOG.md`) | E-40C Annex G, release history | no clause cited in R25 |

## How the pieces connect

- `docs/requirements.md` names the planned test for each requirement. CI runs the tests and writes their
  results as JUnit XML (pytest) and JSON (Playwright).
- `scripts/verification_matrix.py` joins the two into `verification-matrix.md`: each named test is `pass`,
  `fail`, `xfail`, `skipped` (with its reason, listed) or `missing`. CI fails on `missing` (drift), on an `xfail`
  (an expected failure is not passing evidence) and on a skip without a reason (a bare `@pytest.mark.skip` counts as
  no reason). Evidence outside these reporters (perf runs, scripts, UI unit tests) shows as `not in run`.
- On a `v*` tag the `release-record` job adds the configuration and the open NCRs to the matrix.

## Not covered here

Per-module 100 % coverage and MC/DC for the safety-critical components (R25 F1/F2) depend on the owner's
confirmation of the safety-critical determination.
