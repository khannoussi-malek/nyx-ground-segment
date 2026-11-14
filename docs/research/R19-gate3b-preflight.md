# R19 – Gate 3b pre-flight: Schemathesis trial and the hidden-tab watchdog

**Drives:** D-EXT-18 (R17 F2), D-UI-03/04/08/21 (R18 F1)   **Plan:** `docs/superpowers/plans/2026-11-15-gate3b-console-commanding.md` Tasks 13, 14, 24, 28, 29

Done before Track C/D start, so the plan can change instead of the code. Part 1 is the Task 24 spike, run outside the branch. Part 2 is web research, with sources.

## Part 1 – Schemathesis trial against the real app (Task 24): PASS

**Setup.**
- **Version:** `schemathesis==4.32.7` (2026-10-27), the newest release at least two weeks old on 2026-11-14 (4.33.0 is 13 days old). It brings `hypothesis` 6.170.15; the repo pins no hypothesis yet, so there's no conflict.
- **Where it ran:** a separate venv (`uv run --frozen --with`), with the repo, `uv.lock` and the branch untouched. It ran against `create_app()` from `gs-api` on `gate3b-console-commanding` at `04aab84`, with the dev stack up (real Redis and Postgres) and a fresh `vid()` vehicle.
- **Surface tested:** the 7 operations that exist on the branch today (`/api/login`, `/api/logout`, `/api/me`, `/api/vehicles`, `/api/mission-db`, `/api/events`, `/healthz`).

| Question from the plan | Result |
| --- | --- |
| Loads OpenAPI 3.1 / JSON Schema 2020-12 with pydantic's hoisted `$defs`? | Yes, no errors (34 component schemas) |
| In-process ASGI, no server? | Yes. `schemathesis.openapi.from_asgi("/openapi.json", app)`. Schemathesis runs the app's **lifespan itself**, once per process, so no fixture is needed (the real Redis and Postgres are needed) |
| Auth hook with the session cookie and `X-GS-Client: console`? | Works, see the code below |
| SSE route `/api/events`? | Passes; Schemathesis caps stream events (`max_stream_events`), so it doesn't hang |
| Cases per operation | 1 per GET without parameters (nothing to vary); `POST /api/login` 100 passing + 25 invalid in 17 s; whole run < 20 s |
| Plan's check names | `not_a_server_error` from `schemathesis.checks`; `response_schema_conformance`, `status_code_conformance`, `content_type_conformance` from `schemathesis.specs.openapi.checks` (**not** `schemathesis.openapi.checks`, which raises ImportError) |

```python
schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.auth()
class Session:
    def get(self, case, ctx):
        client = schemathesis.python.asgi.get_client(app)
        r = client.post("/api/login", json={"user": FUZZ_USER, "password": FUZZ_PW}, headers=CLIENT_HEADERS)
        assert r.status_code == 204, r.text
        return client.cookies["session"]
    def set(self, case, data, ctx):
        case.cookies = {**(case.cookies or {}), "session": data}
        case.headers = {**(case.headers or {}), **CLIENT_HEADERS}

@schema.parametrize()
def test_schemathesis_asgi(case):
    case.call_and_validate(checks=[not_a_server_error, response_schema_conformance,
                                   status_code_conformance, content_type_conformance])
```

**What it found** (no 5xx; every response body matched its schema):

1. **A 405 drops the `Allow` header** (default checks, every operation, first case). `app.py` `_http_error` builds a new `JSONResponse` and doesn't pass `e.headers`. RFC 9110 §15.5.6 requires `Allow` on a 405. **Fix (one line):** `JSONResponse(…, headers=e.headers)` in `_error`/`_http_error`. With that fix applied in the test only, the failure disappears.
2. **Wrong methods on `/api/*` are answered by the UI's static mount at `/`.** `POST /api/nosuch` returns 405 instead of 404, and `OPTIONS /api/login` returns `Allow: GET, HEAD`, which is the static files' answer, not the route's. It's harmless, and the plan's four checks don't test it. **No change**; it is noted here so a later default-check run isn't a surprise.
3. **A body that isn't valid JSON or UTF-8 gets an undocumented 400.** FastAPI raises `HTTPException(400, "There was an error parsing the body")`, which reaches our handler as `ErrorBody` `BAD_REQUEST`. `status_code_conformance` fails because the routes document 204/401/403/422/503 only. **Fix:** add 400 to `_errors(...)` on every route with a body: login now, and ack, commands and sessions in this gate.

**Decision: PASS.** Task 29 can make `test_schemathesis_asgi` the CI check that replaces EX-24's informational test, after fixes 1 and 3. Two points for Task 29:
- **Use a dedicated fuzz user** in the auth hook, not `fc1`. Once Task 20's rate limit is in, any fuzz case that posts a wrong password for the hook's user would make the hook itself get 429 and turn the run flaky. Also add 429 to `/api/login`'s documented statuses.
- The trial covered today's 7 operations. Commands, ACK and sessions arrive with Tracks A–D, so the CI check only means something after Task 27 (the plan already puts it in Task 29). `POST /api/commands` stays restricted to `vid()` vehicles, as planned.

## Part 2 – Hidden console tab: can the loss-of-monitoring tone be late?

**Short answer:** only for NO LIVE CONNECTION by silence, and only if the 3 s watchdog is a polling `setInterval`. Every other tone trigger arrives as an SSE event and doesn't depend on a timer.

**Chrome's rules** (https://developer.chrome.com/blog/timer-throttling-in-chrome-88 and its design doc):
- **Once a second** in a hidden tab: a timer is checked "once per second" while its chain count is < 5, or the page has been hidden < 5 min.
- **Once a minute** ("intensive wake-up throttling") when the page has been "hidden for more than 5 minutes" **and** the chain count is ≥ 5 **and** it has been "silent for at least 30 seconds". Chained timers and repeating intervals reach chain count 5.
- **No exemption for our case:**
  - Silent audio doesn't count: "Playing silent audio is not sufficient to opt-out the page — Chrome checks whether the audio is truly audible."
  - An open EventSource doesn't count either: the only connection exemption is WebRTC.
- A 10 s variant for fully loaded pages was approved at blink-dev (https://groups.google.com/a/chromium.org/g/blink-dev/c/5SZB2CFFGqE). Whether it is on by default is UNCONFIRMED.
- **macOS:** a window fully covered by another window is marked hidden by Chromium's occlusion heuristic (Electron release note on the Chromium fix, https://releases.electronjs.org/pr/52365; secondary source). So a console behind another window counts as hidden, not only a background tab.

**Effect on each D-UI-21 tone trigger:**

| Trigger | Arrives as | Hidden-tab delay |
| --- | --- | --- |
| LOS, FROZEN | `link` SSE event (server-side detection) | none expected: SSE events use the remote-event task source, not timers (WHATWG HTML §9.2; that Chrome doesn't throttle them is UNCONFIRMED but consistent with every source found) |
| GROUND PROCESSING DOWN | `hb.processing` every 1 s | none expected (same) |
| BROKER | `ground` SSE event | none expected (same) |
| NO LIVE CONNECTION, server closes the connection | EventSource `error` (spec: "Fire an event named error" before the reconnect wait) | none; `sse.ts` already calls `onError` |
| NO LIVE CONNECTION, host unreachable with no TCP close | **only the 3 s client timer** | up to **~60 s** if the timer is a `setInterval` poll and the tab has been hidden > 5 min |

**Change for Task 13 (cheap; no new file):**
1. **Build the 3 s watchdog as a one-shot timer re-armed on every SSE message:** `clearTimeout(t); t = setTimeout(noLink, 3000)` in the event handler. A timer created from a network event task has nesting level 1, so it stays in the once-a-second tier: worst case about 4 s instead of about 60 s. Don't use `setInterval` for this. The 100 ms age tick in `LiveTable` can stay an interval: it drives what is drawn, not the tone, and drawing doesn't matter while the tab is hidden.
2. **Run `toneKeys` (Task 14) on store updates, not on the render tick.** Then a tone follows the SSE event that caused it, not the next tick.
3. **On `visibilitychange` back to visible,** re-check `performance.now() − lastMessageAt` once, which covers a tab Chrome froze.
4. A Web Worker watchdog only if a manual check shows 1 falls short. Whether worker timers are exempt is UNCONFIRMED.

**Tests: Playwright can't see this.**
- Playwright's Chromium is always started with `--disable-background-timer-throttling`, `--disable-backgrounding-occluded-windows` and `--disable-renderer-backgrounding`, and headless adds `--mute-audio` (Playwright source, `chromiumSwitches.ts`). So `monitoring-tone.spec.ts` (Task 28) passes whether or not point 1 is followed.
- **Unit test (Task 13):** with fake timers, a message every 1 s keeps the banner off. 3 s without a message raises NO LIVE CONNECTION exactly once, and the test asserts that no `setInterval` was used.
- **Manual check, once, in the dry run** (OPS-1 or the ATP-08 machine). In real Chrome, cover the console window or hide its tab for more than 5 min, then cut the network with no TCP close (for example `docker network disconnect` the api container) and time the tone. Expected ≤ 4 s. Record it in the run notes.

**Audio from a hidden tab:**
- An AudioContext unlocked during a user gesture stays usable. Chrome's autoplay rule only suspends a context created before any gesture (https://developer.chrome.com/blog/autoplay).
- No source says desktop Chrome suspends a running context when the tab is hidden; that behaviour is reported for WebKit/iOS. **UNCONFIRMED for Chrome:** the manual check above also covers it (the tone must be heard, not just recorded in `data-last-tone`).
- In Playwright, arming inside `page.click()` on login (as Task 14 plans) is enough to reach `running`. Assert `ctx.state === "running"` and `data-last-tone`, never sound.

## Decision candidate

- **Chosen:** Part 1 fixes 1 and 3 plus the dedicated fuzz user; Task 29 proceeds. Part 2 points 1–3 in Task 13/14 plus the one manual dry-run check.
- **Why:** each is a line or two, and both protect a check the design relies on:
  - Fixes 1 and 3 make the Schemathesis CI check green for real rather than by excluding failing checks.
  - Points 1–3 keep the loss-of-monitoring tone (R18 F1) within seconds in the exact case it exists for: an operator not looking at the console.
- **Rejected:** a Worker watchdog now (unconfirmed benefit); routing wrong-method `/api/*` requests to 404 (finding 2, no operator effect); a Playwright test with throttling re-enabled (needs more than 5 min hidden per run).

## Sources

- Schemathesis 4.32.7 (PyPI); run logs in this session's scratchpad.
- Chrome timer throttling: https://developer.chrome.com/blog/timer-throttling-in-chrome-88; background tabs: https://developer.chrome.com/blog/background_tabs; autoplay: https://developer.chrome.com/blog/autoplay; Energy Saver freezing: https://developer.chrome.com/blog/freezing-on-energy-saver.
- WHATWG HTML, server-sent events: https://html.spec.whatwg.org/multipage/server-sent-events.html. RFC 9110 §15.5.6 (405 and `Allow`).
- Playwright `chromiumSwitches.ts` (default launch switches).
