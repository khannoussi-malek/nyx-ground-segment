# Developer guide

## Add a parameter
1. Add it to `mission/parameters.yaml` (name `^[a-z][a-z0-9_]{0,62}$`, `unit`, `type: float`, `description`). Optional checks go in `mission/limits.yaml`. Nothing under `src/` changes (`tests/meta/test_add_parameter.py`).
2. Run `make migrate`: the owner role adds the column; the app role cannot `ALTER TABLE`.
3. No restart needed. Until the column exists, a running worker keeps the newest valid set and records `LIMITS_REJECTED: run make migrate`, re-checking every 10 s (D-DB-11, Q18). A hot reload that adds an unknown parameter is rejected the same way.
4. The simulator emits only the parameters in `gs.simulator.nominals`; add one there if you want values.
5. **Editing `mission/`** (`parameters.yaml`): a raw parameter goes under `parameters:`, before the `derived:` key, which stays the last key; appending a line at the end of the file puts it under `derived:` and the set is rejected. Calibration is a table, `calibration: {table: [[raw, eng], ...]}` (at least 2 points, raw strictly increasing; the table must cover the red limits plus the deadband). A derived parameter is `derived: {name: {unit, expr: "bus_voltage_v * bus_current_a"}}` (inputs earlier in the file; `abs`, `min`, `max`, `sqrt`) or `{unit, rate_of: <parameter>, window_s: <= 3600}`. Per-mode limits: `checks_by_mode: {SAFE: [...]}` in `limits.yaml`; `{MODE: []}` means not monitored in that mode (also no out-of-calibration-range alarm there); a mode not listed uses `checks`.
6. Run `make migrate` after adding a raw parameter with a calibration (it adds `<name>` and `<name>_raw`) or a derived parameter; a reload that needs a column that does not exist yet is rejected with "has no telemetry column".

## Add a limit check type
One new module in `src/gs/alarms/checks/`: a pydantic config with `type: Literal[...]`, a frozen, JSON-serialisable state dataclass, `initial()` and `step(state, sample, current) -> (state, level)`, decorated with `@register`. The package imports every module, so the worker is not edited. Copy `limit.py` or `roc.py`. The property test serialises the state at every batch boundary and must give the same transitions as an uninterrupted run (ALM-07, D-ALM-10).

## Add a command
Add an entry to `mission/commands.yaml`: `description`, `hazardous`, `hazard` (required when hazardous), typed `args` with bounds, optional `receive_timeout_s` / `verify_timeout_s` no longer than the defaults (5 s and 15 s). The API and the simulator read the same file and enforce the same bounds. The simulator reads it at start, so **restart the simulator after a bound change** (ruling R-o). Ingest follows a reload; the API refuses a reload that turns a command from hazardous to not hazardous (G31).

## Change a stream interface
Stream entries outlive the code that wrote them, and every stream model except the frame header forbids extra fields, so even an optional new field makes the other version reject the entry (D-ING-27). **Rule: restart all services together** (`make up`) after any change to `frame`, `tc`, `tcack`, `dlq` or `ui-event`. The CI lint job prints "stream interface changed: restart all services together" when an exported stream schema differs from `main` (`scripts/stream_schema_notice.py`; information only). Regenerate with `make gen` and commit `interfaces/` and `ui/src/server/schema.d.ts`.

## API request bodies
Request models are strict. A UUID or datetime field that arrives as a JSON string must be declared `Annotated[UUID, Field(strict=False)]` (FastAPI validates the decoded JSON in Python mode, where a strict UUID rejects the string); see `AckRequest.request_id`. Database errors on a route are returned as 503 `UNAVAILABLE` ("database unreachable") through `gs.api.dbguard.db_503`, never as a 500. A new database route takes its connection with `connect(request)` (one 3 s pool wait) and wraps the queries in `with db_503(request):`; a value Postgres rejects (`psycopg.DataError`) then comes back as 422 `VALIDATION_ERROR`.

## Archive, backfill and replay
The archiver service writes each finished UTC hour of each vehicle to MinIO as `<vehicle>/<yyyy>/<mm>/<dd>/<HH>.parquet` with a `.sha256` sidecar, re-reads it and compares rows, and only then lets the hot rows go. Two settings, both in `.env`: `HOT_RETENTION_H` (default 25; hot rows are deleted once the hour is archived, verified and this old; 0 deletes at once, so a test that needs the hot rows after archiving sets it high) and `ARCHIVE_LOOP_S` (default 300, seconds between passes; set 2 in a test). Test hooks need `GS_TEST_HOOKS=1`; `ARCHIVER_CRASH_AT=archive:after_put` kills the archiver after the upload, before the row is recorded.

NYX-TA9 is the backfill vehicle: it has no live frames. `docker compose run --rm --no-deps simulator gs-sim backfill --vehicle NYX-TA9 --from <UTC> --to <UTC> [--fault name@offset_s[:json]] [--pause-at-seq n]` pushes whole past hours through the normal stream (inside the last 7 days minus 1 h) and prints how many frames it sent. Stop the archiver first when a test needs the hour unarchived. The manual walk-through is `docs/atp/ATP-07.md`; replay and the console are in the operator guide.

## Property and chaos tests
- **Properties** (`tests/property`, Hypothesis, D-EXT-14): `uv run pytest --no-cov tests/property` runs the `ci` profile (200 examples, derandomised, part of the normal suite). `HYPOTHESIS_PROFILE=nightly` searches 10 000 examples with a random seed that the run prints in the pytest header (`hypothesis profile: nightly, seed <n>`, so run without `-q`); to replay a failed night, take it from the log and run `HYPOTHESIS_PROFILE=nightly HYPOTHESIS_SEED=<n> uv run pytest --no-cov tests/property`, or give the workflow that seed as its input. P6 (the derived evaluator against an independent reference) and the derived and mode parts of P3 and P4 run in the same profile.
- **Pin a counter-example**: Hypothesis prints `@reproduce_failure(...)` or a minimal example. Copy the example into the property as `@example(...)` (kept forever, runs in every profile) or into a plain unit test with a name that says what it broke, then fix the code; never loosen the property.
- **Chaos soak** (D-EXT-15): `make chaos SEED=<n> MINUTES=<m>` (default 30; no SEED: random, printed first) starts the stack with `docker-compose.chaos.yml` and runs `chaos/soak.py`, which also starts the console watcher (I4: no OK row older than its vehicle's `max(2 s, stale_ms)` + 500 ms, read from the row's `data-stale-ms`; `chaos/watch_console.spec.ts` through `e2e/chaos.config.ts`; needs `make e2e/node_modules`). Run it on an idle stack, one at a time. It stops at the first violated invariant and prints the seed, the time and the action log; the same `SEED` replays the same schedules (the system's timing differs, so a race may not recur: keep `actions.jsonl`). Output is in `chaos/out/<UTC date>-<seed>/`: `actions.jsonl`, `invariants.csv`, `summary.md` (PASS/FAIL per invariant; I4 is reported there and does not change the verdict), `watch.json`, `watch.log`, and, on an I4 failure, the watcher's Playwright trace (`trace/trace.zip`: screenshots only, started after the login so it holds no password; git-ignored under `docs/chaos/*/trace/`, never commit it). I4 also fails when no parameter row is on screen for more than 10 s (a blank or logged-out watcher); rows that are shown but not OK, as during a broker outage or LOS, do not count as blind. A soak that was killed is repaired by the next one (see "Start-up repairs" in its summary). The verdict is FAIL, not PASS, when an invariant was never judged, the final pass could not read a source, the run was interrupted, or a 30 min run executed fewer than 15 infrastructure actions or 30 faults. **After `make chaos` the stack stays in chaos mode** (overlay networks, `GS_TEST_HOOKS=1` on the simulator, an append-only ledger growing about 7 MB/h): run `make up` to return to the core topology (it recreates the changed services), and delete `chaos/out/ledger/sim.jsonl` only while the stack is down (`make down`).
- **Nightly**: `.github/workflows/chaos-nightly.yml` (02:00Z and *Run workflow*, with an optional seed) runs the nightly property suite and a 30 min soak; it is red when a property or I1-I3 and I5-I8 fail. It is not a push gate. The folder is uploaded as the artifact `chaos-<run id>` for 30 days.
- A pure invariant check goes in `chaos/invariants.py` with a unit test on synthetic data first (`tests/unit/test_chaos_invariants.py`); an action in `chaos/actions.py` must undo itself in `finally`.

## Performance and soak runs
`make perf` is run N (10 min at 10 Hz, `docs/perf/run-n-<UTC date>/`). `make perf-p` is run P (2 vehicles at 100 Hz for 5 min; it sets `SIM_RATE_HZ=100` itself and restores 10). `make soak` is the 2 h ATP-01 count: it starts and stops the sessions itself, so run it on an idle stack. Procedures and pass lines: `docs/atp/ATP-08.md` and `docs/atp/ATP-01.md`.

## Console source layout (`ui/src`)
- `main.tsx` and `App.tsx` start the console; `gallery.tsx` is the dev-only state gallery (`/gallery.html`).
- `components/`: React components (panes, cards, dialogs, the board).
- `server/`: everything that talks to the ground API: the client (`api.ts`), the generated types (`schema.d.ts`, from `make gen`; never edit it), the event stream, login and session calls, replay.
- `state/`: the browser's copy of what the server sent: the store, value freshness, banners, the latency probe.
- `config/`: the pane list and default layout, the pane help notes.
- `tools/`: pure helpers with no React: formatting, UTC time, gauge and chart maths, alarm, command and event wording, audio.
- `styles/`: `style.css` and its contrast test. `test/`: shared fixtures.
- A test sits next to the file it tests.

## Run the tests
- Unit, integration and meta: `make up`, then `uv run pytest` (80 % coverage gate; `-m spike` runs the Schemathesis spike).
- Static: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src tests scripts perf chaos`, `make gen && git diff --exit-code -- interfaces ui/src/server/schema.d.ts`.
- System (Docker; stops and starts containers, one run at a time): `uv run pytest tests/system`.
- End to end: `make e2e` (Playwright, browser). UI unit tests: `cd ui && npx vitest run`.
  The specs edit `mission/limits.yaml` and `parameters.yaml` and restore them after each test; a run that refuses to start with "mission/ has local edits" was interrupted before: `git checkout -- mission`. `UPDATE_DOCS_IMG=1 make e2e` also refreshes the operator guide's `docs/img/console-leak.png`; a normal run writes no tracked file.
- Performance: `make perf`, `make perf-p`, `make soak` (see above).
