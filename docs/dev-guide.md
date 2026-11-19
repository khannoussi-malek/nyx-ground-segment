# Developer guide

## Add a parameter
1. Add it to `mission/parameters.yaml` (name `^[a-z][a-z0-9_]{0,62}$`, `unit`, `type: float`, `description`). Optional checks go in `mission/limits.yaml`. Nothing under `src/` changes (`tests/meta/test_add_parameter.py`).
2. Run `make migrate`: the owner role adds the column; the app role cannot `ALTER TABLE`.
3. No restart needed. Until the column exists, a running worker keeps the newest valid set and records `LIMITS_REJECTED: run make migrate`, re-checking every 10 s (D-DB-11, Q18). A hot reload that adds an unknown parameter is rejected the same way.
4. The simulator emits only the parameters in `gs.simulator.nominals`; add one there if you want values.

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
- Static: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src tests scripts perf`, `make gen && git diff --exit-code -- interfaces ui/src/server/schema.d.ts`.
- System (Docker; stops and starts containers, one run at a time): `uv run pytest tests/system`.
- End to end: `make e2e` (Playwright, browser). UI unit tests: `cd ui && npx vitest run`.
- Performance: `make perf`, `make perf-p`, `make soak` (see above).
