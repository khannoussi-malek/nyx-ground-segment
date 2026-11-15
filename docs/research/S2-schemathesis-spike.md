# S2 – Schemathesis spike against the ASGI app (Task 24, D-EXT-18, R17 F2)

**Decision: PASS.** Task 29 turns `tests/integration/test_rest_fuzz.py` into the CI check `test_schemathesis_asgi` (replacing EX-24's informational test). The only failures are the 501 route stubs that other tracks have not built yet (see below), which is what a spike on a half-built branch should show.

## Setup
- **Version:** `schemathesis==4.32.7` (dev group, `uv.lock` updated; hypothesis comes with it). R19 ran the same version at `04aab84`. 4.33.0 is exactly 14 days old on 2026-11-15; I kept the version R19 already verified instead of the borderline newest one. Move the pin when Task 29 lands if wanted.
- **Run:** `uv run pytest -m spike tests/integration/test_rest_fuzz.py -q -s` on `gate3b/data` at the Task 23 commit, real Redis and Postgres from this worktree's stack. The gate does not collect it: the `spike` marker is registered and `addopts = "-m 'not spike'"` deselects it (P-pf13; verified: `17 deselected`).
- **Code:** R19's, reused. `schemathesis.openapi.from_asgi("/openapi.json", app)`, an `@schema.auth()` class that logs in through `/api/login` and sets the `session` cookie and `X-GS-Client: console` on every case, checks `not_a_server_error` (`schemathesis.checks`) and `response_schema_conformance`, `status_code_conformance`, `content_type_conformance` (`schemathesis.specs.openapi.checks`). A dedicated `fuzz` user in a test-local users file (P-pf8/P-pf18), not fc1. `POST /api/commands` is excluded (the fuzzer cannot promise a `vid()` target).

## Questions the brief asks
| Question | Result |
| --- | --- |
| OpenAPI 3.1 / JSON Schema 2020-12 with pydantic's hoisted `$defs` loads? | Yes, no errors; 17 operations parametrised |
| In-process ASGI, lifespan? | Yes; Schemathesis runs the app's lifespan itself |
| Auth hook works? | Yes: every case reached the handler authenticated (no 401 failures); the fuzz user never hit the login limit |
| Cases per operation | Hypothesis default: ~100-140 for operations with a body or parameters, 3 for parameterless GETs |
| Result | 13 passed, 4 failed in 66 s |

Generated cases (counted in the test, includes negative and method-probing cases): `GET /api/telemetry/{vehicle}/{param}` 142, `POST /api/login` 124, `POST /api/sessions` 128, `POST .../stop` 109, `GET .../report` 109, `GET .../report.csv` 109, `POST /api/alarms/{id}/ack` 26, parameterless GETs 3 each.

## The four failures: all 501 stubs, nothing real
`GET /api/mission-db/frame-schema` (Track B, Task 12), `GET /api/commands`, `GET /api/alarms`, `POST /api/alarms/{alarm_id}/ack` (Track A). Each fails the same way: `ST101 Server error` and `ST103 Undocumented status code` (received 501). They go green when those tasks land. Nothing else found: no 5xx and no schema, content-type or status-code mismatch on login, logout, me, vehicles, mission-db, events, sessions (start, stop, list, report JSON and CSV) or the telemetry buckets. The `Allow` header (R19 fix 1) and documented 400s (R19 fix 3) from Track 0 are in effect.

## Raw output (tail)
```
FAILED tests/integration/test_rest_fuzz.py::test_schemathesis_asgi[GET /api/mission-db/frame-schema]
FAILED tests/integration/test_rest_fuzz.py::test_schemathesis_asgi[GET /api/commands]
FAILED tests/integration/test_rest_fuzz.py::test_schemathesis_asgi[GET /api/alarms]
FAILED tests/integration/test_rest_fuzz.py::test_schemathesis_asgi[POST /api/alarms/{alarm_id}/ack]
4 failed, 13 passed in 66.61s (0:01:06)
```
One failure block:
```
ST101 Server error
ST103 Undocumented status code   Received: 501   Documented: 200, 401, 503
[501] Not Implemented: {"error":"NOT_IMPLEMENTED","message":"GET /api/mission-db/frame-schema"}
curl -X GET -H 'X-GS-Client: console' -H 'Cookie: [Filtered]' http://testserver/api/mission-db/frame-schema
```

## For Task 29
- Remove the 501s by finishing Tasks 4-12 first, then drop the `spike` marker and run it in the gate.
- Keep the fuzz user separate from fc1/fc2; `/api/login` already documents 429.
- Fuzzing writes real rows (sessions, audit log) on the configured vehicle; the hook uses a `vid()` vehicle so nothing collides with the demo vehicle.
- `POST /api/commands` stays excluded unless the generated vehicle can be forced to a `vid()` target.

## Dependency age (P-pf41)
Every package newly added to `uv.lock` must be at least 14 days old, transitive ones included. Three were younger and are held back in `[tool.uv] constraint-dependencies`: graphql-core 3.3.2 (2026-11-06) to 3.3.1, hypothesis 6.170.15 (2026-11-07) to 6.170.14, markdown-it-py 4.3.0 (2026-11-11) to 4.2.0. The spike gives the same result after the re-lock (13 passed, the same 4 stub failures). Drop the constraints when the newer releases are 14 days old.
