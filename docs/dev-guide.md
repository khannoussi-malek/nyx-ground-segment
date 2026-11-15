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
Stream entries outlive the code that wrote them, and every stream model except the frame header forbids extra fields, so even an optional new field makes the other version reject the entry (D-ING-27). **Rule: restart all services together** (`make up`) after any change to `frame`, `tc`, `tcack`, `dlq` or `ui-event`. The CI lint job prints "stream interface changed: restart all services together" when an exported stream schema differs from `main` (`scripts/stream_schema_notice.py`; information only). Regenerate with `make gen` and commit `interfaces/` and `ui/src/api`.

## API request bodies
Request models are strict. A UUID or datetime field that arrives as a JSON string must be declared `Annotated[UUID, Field(strict=False)]` (FastAPI validates the decoded JSON in Python mode, where a strict UUID rejects the string); see `AckRequest.request_id`. Database errors on a route are returned as 503 `UNAVAILABLE` ("database unreachable") through `gs.api.dbguard.db_503`, never as a 500.

## Run the tests
- Unit, integration and meta: `make up`, then `uv run pytest` (80 % coverage gate; `-m spike` runs the Schemathesis spike).
- Static: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy --strict src tests scripts perf`, `make gen && git diff --exit-code -- interfaces ui/src/api`.
- System (Docker; stops and starts containers, one run at a time): `uv run pytest tests/system`.
- End to end: `make e2e` (Playwright, browser). UI unit tests: `cd ui && npx vitest run`.
- Performance run N (10 minutes, writes `docs/perf/run-n-<UTC date>/`): `make perf`.
