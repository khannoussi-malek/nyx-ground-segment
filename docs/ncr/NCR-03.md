# NCR-03: fresh-clone defects in the procedures and setup, found at Gate 5

- **Requirement:** ENG-01 (a fresh clone runs the demo first try), ENG-08 (README: run in three commands), ATP-08 (performance procedure).
- **What failed:** acceptance was run from a fresh clone of `main`. Four defects in the written procedures and setup showed up, none in the ground-segment behaviour:
  1. The README's first command, `git clone`, failed as written: the URL pointed to a repository that does not exist. Fix `5156c01`. Pinned by `tests/meta/test_readme_sections.py::test_readme_clone_url_matches_the_repository`.
  2. The first run N aborted: a fresh clone has no `e2e/node_modules`, so `make perf` and `make e2e` could not start the browser; the summary lists five FAIL lines (kept in `docs/perf/run-n-2026-11-18-aborted/`, not a measurement). Fix `47d4257`. Pinned by `tests/meta/test_makefile.py::test_playwright_targets_install_the_e2e_packages`.
  3. The archiver logged a MinIO client pool warning ("Connection pool is full") every 5 s; the pool was too small for the pass and its health probes. Fix `f5815a2`. Pinned by `tests/unit/test_s3_client.py::test_client_pool_fits_the_pass_and_two_s3_health_probes`.
  4. The ATP psql commands failed from a script (no TTY for `docker compose exec`). Fix `2416a7c` (`exec -T`); no automated test pins it.
- **Impact on the user:** a newcomer following the README would have failed at step one; none of the four touched stored data, alarms or commanding.
- **Severity:** minor.
- **Status:** CLOSED
- **Decision:** fix now. All four are fixed on branch `gate5-acceptance` and in the 1.0.0 release.
