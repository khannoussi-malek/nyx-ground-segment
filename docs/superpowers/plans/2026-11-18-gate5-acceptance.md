# Gate 5 Acceptance Plan

**Goal:** Run the spec's acceptance (§5) from a fresh clone, fill `docs/atp-report.md` with a result and evidence per ATP and per requirement, write NCRs only for real failures, record the demo, and tag `v1.0` on a green CI run.

**Spec:** spec §5 (acceptance rule, ATP table), §6 (deliverables), §7 (review questions, answered in `docs/design.md` §8), §8 Gate 5; `docs/design.md` §5.3 Gate 5 row; `docs/requirements.md` (each requirement's ATP and evidence).

**Where:** fresh clone of `main` at `7e696b3`, branch `gate5-acceptance`, stack ports +1400 (API 9400, DB 6832, Redis 7879, MinIO 10400, sim 9481) because another project's stack holds 5432.

## Acceptance rule (spec §5, verbatim intent)

- Every **M** requirement PASS with evidence; **S** requirements ≥ 5 of 7 PASS.
- Any FAIL → `docs/ncr/<id>.md`: what failed, impact on the user, severity (major/minor), decision (fix now / accept with a known limitation / defer with a plan).
- Measurements state how they were measured, on what machine, and the raw numbers. Nothing is rerun "until it passes"; a failed measurement is reported as is.

## Order (measurements on a quiet machine first, destructive suites last)

1. **INSP-01 setup evidence:** the clone, `make up` time and service health (done at start).
2. **ATP-07** on the fresh stack, following `docs/atp/ATP-07.md` step by step (the operator records each command's output). Before any e2e run: the replay e2e leaves an open ARCHIVE_CORRUPT on NYX-TA9.
3. **ATP-08** (`make perf`, `make perf-p`, `perf.scaling`) with nothing else running on the machine but the user's own idle stacks (recorded).
4. **ATP-01** (`make soak`, 2 h) under the same rule.
5. **Automated suites** (CI-equivalent): lint, unit + coverage, `after_coverage`, integration, system, e2e. They cover ATP-02 to ATP-06 and the automated inspection checks.
6. **Report:** `docs/atp-report.md` (spec §5 table filled: Result, Evidence, numbers), `docs/requirements.md` Status column, NCRs, INSP-01 findings.
7. **Demo:** a ≤ 5-minute recording of ATP-02, ATP-04 and ATP-07 (Playwright video of their specs), stored under `docs/demo/`, linked from the README.
8. **Release:** CHANGELOG `v1.0` with known limitations; README run steps and links checked; PR → CI green → merge → tag `v1.0` → CI green on the tag.

## Rulings for this gate

- The user's own stacks (ci-hub, paperlab) keep running during measurements; they are recorded with their CPU share, not stopped (not ours to stop).
- The main `nyx` stack stays as the user left it (stopped); the prep worktree stack is stopped.
- `make up` in the clone reused cached image layers; the report says so and gives the cold-build time from a separate `docker compose build --no-cache` measurement if it is taken, else states it was not measured.
