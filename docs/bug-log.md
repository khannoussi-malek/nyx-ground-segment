# Bug log (D-ENG-19)

Real bugs that tests or review caught on the Gate 2 thin-slice branch.

| Date | Failing test | What it caught | Fix commit |
|---|---|---|---|
| 2026-11-14 | Playwright smoke (Task 19) | The SSE reconnect timer `deps.setTimeout(...)` threw "Illegal invocation" in real browsers, so the console never reconnected and an expired session never returned to login | `037181f` (browser-faithful fakes) |
| 2026-11-14 | `test_stop_within_grace` (Task 18) | The api exited 143 on SIGTERM because uvicorn re-raises the captured signal | `93fe3f3` |
| 2026-11-14 | Reviewer + `test_reconnect_rereads_own_pel` (Task 12, RED before the fix) | redis-py's built-in retry hid reconnects, so the own-PEL re-read never ran and non-idempotent XADDs could be re-sent | `0ffa95f` |
| 2026-11-14 | `test_precedence_vehicle_over_schema` (Task 11) | The vehicle-mismatch and t_onboard checks sat in a model-level validator that never ran when another field failed, so `vehicle_mismatch` could not outrank `schema` | `e2437c7` (checks moved to field validators in the same commit) |
| 2026-11-14 | Review + new loader tests (Task 6) | An unhashable YAML key raised a bare `TypeError` and deep nesting raised `RecursionError`, both escaping the safe loader | `b564bde` |
| 2026-11-14 | Review (Task 3) | The audit-log triggers were untested (only grants were); a trigger test was added | `2b48695` |
