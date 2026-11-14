# R9 – Operator console

**Drives:** GS-UI-01..08, GS-DAT-06 (REPLAY banner), OPS-2, OPS-3   **Answers review Q:** 1, 4

Handoffs: latency method and histogram → R10. Alarm states and the `alert` flag → [R8](R8-alarm-logic.md). Downsample SQL → [R6](R6-timeseries-store.md). Two-field value model and Yamcs/Open MCT staleness → [R1](R1-prior-art-mission-control.md). Closes A-R3-6 and R1 open questions 3 and 5.

## Question

1. Live push: WebSocket or SSE? How does the console reconnect and resync, and how does it recover within 5 s after an API restart (UI-07)? How does the backend fan out without a second consumer group (A-R3-6)?
2. STALE and LOS: where does the staleness clock run? How do we get 2 s ± 0.5 s on every row (UI-02) and make sure "no old value is ever shown as current" (OPS-3)? Is the `freeze` fault "stale"?
3. ISA-101 "grey when nominal", text plus colour, and WCAG 2.2 AA: which palette?
4. How do we get "single alert for new red" (UI-05) and "sounds once" (OPS-2) under browser autoplay rules?
5. Which chart library covers 10 Hz live data and 2 min–24 h windows with a min/max band (UI-04)?
6. Do the command timeline and alarm list need a component library? How do we show UTC with `Z` (UI-08)?
7. What must the UI expose so frame-to-screen p95 can be measured (UI-01)?
8. Which stack and state model? What does the Playwright leak scenario look like?

## Findings

### 1. Push transport (spike S1: FastAPI 0.162.6 / Starlette 1.10.0 / uvicorn 0.54.0, Chrome for Testing 153 headless, Apple M4 Pro, localhost, 1 run per case)
- FastAPI has SSE built in: `fastapi.sse.EventSourceResponse` and `ServerSentEvent(data, event, id, retry)`. It sends a keep-alive `ping` comment every 15 s by default, and you read `Last-Event-ID` yourself as a header to resume ("Added in FastAPI 0.135.0") — [FastAPI SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- EventSource reconnects by itself: "if the connection between the client and server closes, the connection is restarted". `retry` sets the reconnection time in ms — [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events). On a network error the UA must "reestablish the connection". On reconnect it sends `Last-Event-ID` when the last ID is not empty. The default delay is implementation-defined, "probably in the region of a few seconds" — [WHATWG HTML §9.2](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- **Gotcha:** "if res's status is not 200, or if res's Content-Type is not text/event-stream, then fail the connection", and after that the UA "does not attempt to reconnect" — [WHATWG §9.2.2–9.2.3](https://html.spec.whatwg.org/multipage/server-sent-events.html). A proxy (Vite dev proxy, nginx) that answers 502 while the API restarts therefore kills EventSource for good. **S1 confirmed it:** after one 502, `readyState=2` (CLOSED) and no message for 8 s after the API was back.
- **S1, API killed for 2 s, `retry: 1000`:** retries at about 1 s intervals. First message 0.87 s after the API answered again, 3.0 s after the kill. It resumed gap-free from `Last-Event-ID` (id 19 → 20). **Same case with no `retry`:** one retry at about 3 s (first message 3.0 s after the kill). That suggests a Chromium default near 3 s, from one sample (**VERIFIED**: `EventSource::kDefaultReconnectDelay = 3000` in Chromium `event_source.cc`; the WHATWG spec only says "probably in the region of a few seconds").
- The WebSocket interface "doesn't support backpressure". MDN's WebSocket API page documents no reconnect, retry or backoff, so the app has to build it — [MDN WebSockets API](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
- Without HTTP/2, SSE shares the browser limit of **6** connections per browser + domain across tabs — [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events). One stream per tab is fine.
- Redis pub/sub is "at-most-once": if a subscriber disconnects, "the message is forever lost" — [Redis pub/sub](https://redis.io/docs/latest/develop/pubsub/). `XREAD` (no group) returns entries greater than a given ID, "fans out to all the clients" and does not remove entries. Use `$` only on the first call, then pass the last ID, "otherwise you could miss" entries — [XREAD](https://redis.io/docs/latest/commands/xread/)

### 2. Staleness, LOS, freeze
- Yamcs marks a value EXPIRED after the expected rate × tolerance. Open MCT's example staleness provider uses the time since the last datum (`stalenessInterval: 3000`) and pushes `{isStale, timestamp}` through `subscribeToStaleness` — see [R1 §2](R1-prior-art-mission-control.md) and its links. In both, the staleness decision comes from arrival time, not the value. Neither doc set describes detecting repeated identical values (**VERIFIED** by source search of yamcs/yamcs and nasa/openmct for frozen/stuck/repeat: none. **Correction:** Open MCT staleness is not arrival-based by definition; a pluggable provider decides `isStale`, and the bundled example just toggles it on a timer).
- A server-only staleness push cannot work when the push link itself is dead, so the browser has to own the clock. Browser wall clocks can be skewed, so the clock is `performance.now()` (monotonic, local), seeded with an **age measured on the server**: `age_ms = api_now − t_rx`. Both are ground-host clocks (same host in compose). The deadline is `arrival + (2000 − age_ms)`. A backlog replayed after reconnect arrives with large `age_ms`, so it is born STALE: no old value shows as current (OPS-3).
- Timer precision: a 100 ms `setInterval` re-check gives about 2.0–2.1 s on a foreground tab (by construction, not measured; R10 measures it). Background tabs are throttled. Chrome checks timers "once per second", and after more than 5 min hidden, silent and nesting ≥ 5, "once per minute" — [MDN setTimeout](https://developer.mozilla.org/en-US/docs/Web/API/Window/setTimeout). Tolerance is met for a visible console. Refocus re-evaluates immediately.
- **LOS vs DISCONNECTED:** these are two different failures. The API sends an `hb` event every 1 s, so the vehicle can be silent while the link is alive. No `tm` for a vehicle for 2 s with `hb` still arriving → **LOSS OF SIGNAL** (vehicle). No message at all for 3 s → **NO LIVE CONNECTION** (ground). In that case the vehicle state is unknown and every row is STALE. The worker emits authoritative `LOS`/`AOS` + `GAP` (lost count, R5) events for the report and the event log.
- **`freeze` ≠ stale.** Arrival is fresh but the data is not. Two cases: (a) the whole frame repeats, including `seq`/`t_onboard`. R5 dedup drops it, and the freshness clock is fed only by accepted frames, so rows go STALE on their own. (b) `seq` advances but values repeat. A real sensor at 10 Hz carries noise, so a **FROZEN** detector in the worker flags `acq=FROZEN` when every analog param is bit-identical (or `t_onboard` does not advance) for N = 20 accepted frames (2 s, the same budget as STALE). It runs in the worker, not the UI, so replay reproduces it (DAT-06). ISA-18.2 uses "stale alarm" for an alarm left active for more than 24 h — [Siemens alarm-mgmt white paper](https://assets.new.siemens.com/siemens/assets/api/uuid:234f1026-298f-49dc-8961-0c5223c38588/white-paper-alarm-management-2021-final.pdf) (secondary source; standard text not read). Keep the word STALE for data only and say so in the README glossary.

### 3. ISA-101 / WCAG
- High-performance HMI practice uses a light grey background and "foreground colours … sparingly to indicate abnormal situations". "Alarm indicators should be shown in multiple ways, with different colours, shapes, and text" — [IPD/processonline 2024](https://www.processonline.com.au/content/process-control-systems/sponsored/best-design-practices-creating-a-high-performance-hmi-1374659233). Tatsoft has "Use grayscale as default - Color only for abnormal conditions" and lists "Color for normal states" as a pitfall — [Tatsoft ISA-101 how-to](https://docs.tatsoft.com/display/FX/ISA-101+HMI+Compliance+How-to). The ISA-101.01-2015 text itself was not read, and the original Hollifield article returned 403 (**STILL UNVERIFIED** against the standard: ISA-101.01-2015 is paywalled, ISA's own article and control.com return 403; only vendor summaries were read).
- Evidence is mixed. A grayscale situation map caused more saccades than colour in a target-search task (n = 18) — [Liang et al. 2022](https://consensus.app/papers/details/w:8f68d664-6377-40c0-a534-abb9e6dd79eb/?utm_source=claude_desktop). Plain digital values led to reactive monitoring, and trend icons gave earlier detection — [Cook et al. 2014](https://consensus.app/papers/details/w:5fe6f4bc-7422-4909-a101-6dd2c62c4640/?utm_source=claude_desktop). Our reading: grey works *because* colour then means abnormal, and a sparkline per row is the cheap next step (not in scope).
- WCAG 2.2: SC 1.4.1 "Color is not used as the only visual means". SC 1.4.3 text ≥ 4.5:1 (large text, 18 pt or 14 pt bold, ≥ 3:1). SC 1.4.11 non-text ≥ 3:1 "against adjacent color(s)". Contrast = (L1+0.05)/(L2+0.05). L = 0.2126R + 0.7152G + 0.0722B with the 0.04045 sRGB threshold — [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

**Palette (S2: `palette/contrast.py` in the scratchpad applies the WCAG formula, self-checked on #777/#FFF = 4.48, and asserts every row).** The badge always carries text plus a glyph, so colour is never the only cue.

| State | Text + shape cue | Light (bg `#EBEBEB`) text / fill / border | text:fill · shape:bg | Dark (bg `#1C1C1C`) text / fill | text:fill · shape:bg |
|---|---|---|---|---|---|
| NOMINAL | value only, "OK" muted, no badge | `#3A3A3A` / – / – | 9.54 · – | `#C8C8C8` / – | 10.19 · – |
| YELLOW | ▲ `YEL LOW`/`YEL HIGH` + ↓/↑ | `#000000` / `#FFD54A` / `#7A5C00` | 14.87 · 5.25 (border) | `#000000` / `#FFD54A` | 14.87 · 12.07 |
| RED | ■ `RED LOW`/`RED HIGH` + ↓/↑, bold | `#FFFFFF` / `#B3141B` / – | 6.92 · 5.81 | `#000000` / `#FF6B6B` | 7.57 · 6.14 |
| STALE / FROZEN | ⏱ `STALE 3.4 s` / `FROZEN`, value *italic*, dashed border | `#4D4D4D` / – / `#6E6E6E` | 7.09 · 4.28 | `#B5B5B5` / – (border `#8F8F8F`) | 8.31 · 5.27 |
| LOS / NO LINK | ⊘ `LOSS OF SIGNAL 00:12` (inverse) | `#FFFFFF` / `#2B2B2B` | 14.16 · 11.88 | `#000000` / `#E0E0E0` | 15.91 · 12.91 |
| REPLAY | ⏵ `REPLAY 10× · <window>Z`, banner + 4 px frame | `#FFFFFF` / `#5B2A86` | 9.90 · 8.31 | `#000000` / `#C9A3F0` | 9.98 · 8.10 |

A STALE row that was RED keeps its limit as an **outline** badge (red border, no fill, text "was RED LOW"). Alarms never clear on missing data (R8), so the alarm list still shows it. Purple is used only for REPLAY and is never used for alarms.

### 4. Audio
- "[An AudioContext created before a gesture] will be created in the "suspended" state, and you will need to call `resume()` after the user gesture" — [Chrome autoplay](https://developer.chrome.com/blog/autoplay). Starting Web Audio "outside the context of handling a user input event is subject to autoplay rules". `navigator.getAutoplayPolicy("audiocontext")` checks the policy — [MDN Autoplay](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay). `state` ∈ suspended/running/closed/interrupted, plus a `statechange` event — [MDN state](https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/state)
- Plan: create and `resume()` the context inside the **login click** handler. Play one ~600 ms OscillatorNode beep (no audio file) on an `alarm` event with `alert: true` (R8 sets it only on entry/escalation to RED). Dedupe by transition key in a `Set` mirrored to `sessionStorage`. On (re)connect, snapshot alarms whose key is not in the set alert once. If `ctx.state !== "running"`, show a sticky ⚠ `SOUND OFF – click to enable` badge. A live AudioContext also exempts the tab from Firefox timer throttling — [MDN setTimeout](https://developer.mozilla.org/en-US/docs/Web/API/Window/setTimeout).

### 5. Charts (S3: esbuild 0.28.2 `--minify`, React external, gzip -9; measured, not README numbers)

| Lib (npm, 2026-11-13) | min / gzip | Renderer | Band | React | Notes |
|---|---|---|---|---|---|
| **uPlot 1.6.32** (MIT) | **52.6 / 23.5 KB** | canvas | `bands: [{series:[hi,lo], fill}]` in `uPlot.d.ts` | ~30-line `useEffect` wrapper (or third-party `uplot-react` 1.2.4) | README: 166,650 pts cold start in 25 ms; streaming 3,600 pts @60 fps 10 % CPU vs ECharts 70 % — [uPlot](https://github.com/leeoniya/uPlot). `tzDate`, `ms: 1`, explicit `width/height` + `setSize` (d.ts, [docs](https://github.com/leeoniya/uPlot/blob/master/docs/README.md)) |
| ECharts 6.1.0 | 1168.8 / 395.8 full; 574.7 / 197.6 tree-shaken line+grid+tooltip+zoom | canvas/SVG | stacked-area trick | wrapper or manual | README table lists 1000 KB, 55 ms init — [uPlot README](https://github.com/leeoniya/uPlot) |
| Recharts 3.10.1 | 386.2 / 115.6 | SVG | range `Area` (**VERIFIED**: `isRange` in recharts 3.10.1 `lib/cartesian/Area.js`, 2026-11-14) | native | Docs ask "Does your chart truly need to display all 50,000 data points?" and suggest aggregation — [Recharts perf](https://recharts.github.io/en-US/guide/performance/) |
| visx xychart 4.0.0 | 165.0 / 58.5 | SVG | build it | native | primitives, so the most code to write |

Data volume: 24 h = 1,500 buckets × 3 series (R6, < 2 s with 5–30× margin). Live 2 min = 1,200 pts at 10 Hz, 12,000 at 100 Hz. The 2 min window comes from the same endpoint (bucket = max(span/1500, 100 ms) = raw) plus a client ring buffer.

### 6–8. Components, UTC, latency hooks, state, tests
- UTC: `toISOString()` "is always … `YYYY-MM-DDTHH:mm:ss.sssZ` … The timezone is always UTC" and throws `RangeError` on an invalid date — [MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/toISOString). One helper `utc(ms) = new Date(ms).toISOString()` (`.slice(11,23)+'Z'` for times in a table). No Intl and no date library. uPlot axes use `tzDate: ts => uPlot.tzDate(new Date(ts), 'Etc/UTC')` (**VERIFIED**: uPlot 1.6.32 `tzDate` special-cases `tz == 'UTC' || tz == 'Etc/UTC'`, `dist/uPlot.esm.js` line 967, 2026-11-14). Range inputs must be parsed as UTC (append `Z`). `datetime-local` is local time.
- No component library. The alarm list is a `<table>` + `<select>` filters (vehicle, severity, state). Ack and hazardous confirm use native `<dialog>` with a required note. The command timeline is one row per command with 4 fixed columns QUEUED/SENT/RECEIVED/VERIFIED holding UTC times, plus FAILED(outcome). That is Yamcs's per-stage ack columns (R1).
- `useSyncExternalStore`: `getSnapshot` must return the same value while the store is unchanged ("compared by `Object.is`"). Declare `subscribe` outside the component — [React](https://react.dev/reference/react/useSyncExternalStore). A per-key store (`Map<vehicle/param, Cell>` + per-key listeners) re-renders only the changed row. At 100 Hz, apply queued messages once per `requestAnimationFrame`.
- Latency hooks for R10. Every value cell carries `data-vehicle data-param data-seq data-t-onboard data-acq data-limit`. After commit, the next rAF calls `performance.mark('nyx:render', {detail:{v,seq,t_onboard_ms}})` (`detail` is "Arbitrary metadata" — [MDN mark](https://developer.mozilla.org/en-US/docs/Web/API/Performance/mark)) and pushes to a `window.__nyx.renders` ring buffer. R10 computes `timeOrigin + startTime − t_onboard`. Same-host clocks are assumed. rAF ≈ paint: **STILL UNVERIFIED as a guarantee**. The WHATWG "update the rendering" steps run animation-frame callbacks before the rendering update, so the second rAF runs one rendering pass after the commit, but the spec does not define paint and lets the UA skip rendering opportunities. Double rAF is a heuristic upper bound on commit-to-paint; the D-PERF-02 report should say so.
- Playwright: `toHaveAttribute`/`toHaveText` auto-retry, default 5 s — [assertions](https://playwright.dev/docs/test-assertions). `page.clock` overrides `Date`, timers, rAF and `performance` — [clock](https://playwright.dev/docs/clock), so the 2 s rule gets a fast component test. ATP-04 uses real time and the `renders` log.
- Versions on npm 2026-11-13: React 19.3.1, Vite 8.3.8, @playwright/test 1.65.0, zustand 5.1.0.

## Decision candidate

**Chosen: SSE from FastAPI, fed by a Redis stream `ui:events` read with plain `XREAD` (no group).**
After DB commit, the worker `XADD ui:events MAXLEN ~ 3000` one message per accepted frame and per alarm/command/link transition (≈ 150 s at 2 × 10 Hz, 15 s at 2 × 100 Hz). Each API process runs one reader task `XREAD BLOCK 1000` from the last ID. It fans out to per-client bounded `asyncio.Queue(1000)`. Overflow closes that client, which reconnects and gets a snapshot. **Connect:** `since` = `Last-Event-ID` header or `?since=` query. If `since` is still in the stream, replay from it. Otherwise read the stream's last ID **first**, then build the snapshot from the DB, then stream from that ID. The client drops a `tm` whose `t_onboard` ≤ the shown one (R4 zombie case, A-R5-3) and an alarm whose transition key it already has. **Client:** `retry: 1000`. A watchdog recreates EventSource with `?since=<lastId>` (a new object loses `Last-Event-ID`) after 1 s when `readyState === CLOSED` (the 502 case) or after 3 s with no message. The banner shows from the first `error` until the next message.
Because: auto-reconnect plus resume is built into the platform and FastAPI. S1 measured 0.87 s from API back to data, against a 5 s budget. Traffic is one-way, since commands and acks go over REST POST. A stream instead of pub/sub means an API restart or a Redis blip loses no alarm event. No consumer group is involved, so ACKED trimming of `tm:*` is untouched (closes A-R3-6).
**Rejected:** WebSocket. Reconnect, resume, heartbeat and backpressure would all be hand-written, and bidirectional traffic is not needed. Redis pub/sub is at-most-once, and an API resubscribe gap drops `alarm`/`cmd` events. `XREAD` on `tm:*` carries raw values without limit state, so the UI would duplicate the alarm logic. Server-pushed STALE alone fails when the link is down.

**Chosen: browser-side freshness clock** (monotonic + server `age_ms`, 100 ms tick, 2.0 s), LOS = no `tm` for 2 s while `hb` is alive, worker-side **FROZEN** (N = 20 identical frames). **Chosen: uPlot** (23.5 KB gz, canvas, native bands) over ECharts (8× larger tree-shaken, 7× CPU when streaming per README) and Recharts/visx (SVG; Recharts' own docs push aggregation). **Chosen: no UI kit**, plain `useSyncExternalStore` store (zustand only if selectors get hairy), `toISOString()` for UTC, light theme first (dark palette verified but not built: YAGNI).

**Push protocol** (`event:` name → `data:` JSON; `id:` = `ui:events` entry ID on everything except `hello`/`snapshot`/`hb`):
```json
hello    {"server_ms":1763042400123,"resumed":true,"replay":null}
snapshot {"last_id":"1763042400000-0","vehicles":{"NYX-TA1":{"seq":1201,"t_onboard":"2026-11-13T14:00:00.100Z","age_ms":40,"mode":"TEST","last_cmd_id":null,
          "params":{"cabin_pressure_kpa":{"v":99.8,"lim":"YELLOW","range":"LOW","acq":"OK"}}}},
          "alarms":[{"alarm_id":"…","key":"NYX-TA1/cabin_pressure_kpa/0/1188/RAISE","state":"UNACK","sev":"YELLOW","alert":false}],
          "commands":[{"cmd_id":"…","name":"isolate_valve","states":{"QUEUED":"…Z","SENT":"…Z"}}]}
tm       {"v":"NYX-TA1","seq":1202,"t_onboard":"2026-11-13T14:00:00.200Z","age_ms":38,"mode":"TEST","last_cmd_id":null,"frozen":false,
          "params":{"cabin_pressure_kpa":{"v":99.76,"lim":"YELLOW","range":"LOW","acq":"OK"}}}
alarm    {"alarm_id":"…","key":"NYX-TA1/cabin_pressure_kpa/0/1230/ESCALATE","state":"UNACK","sev":"RED","range":"LOW","alert":true,
          "value":97.9,"t_onboard":"…Z","by":"system","note":null,"limits_sha256":"9f…"}
cmd      {"cmd_id":"…","v":"NYX-TA1","name":"isolate_valve","state":"VERIFIED","t":"…Z","outcome":null,"reason":null}
link     {"v":"NYX-TA1","state":"AOS","lost_frames":100,"t":"…Z"}      // LOS | AOS | FROZEN | UNFROZEN | SEQ_RESET
hb       {"server_ms":1763042401123}                                   // every 1 s
```
Replay (DAT-06) uses the same shapes on `/api/replay/<id>/events` with `"replay":{"id":"…","speed":10}` in `hello`. It goes into a **separate store instance**, never sounds, and the REPLAY banner + purple frame cannot be dismissed.

**Layout** (1280 px; one vehicle column per test article):
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ NYX GROUND · Session "Pressure test #4" · 2026-11-13T14:02:11Z · user ⚠SOUND OFF│
│ [ NO LIVE CONNECTION – reconnecting (2) – all values STALE ]  ← only when down │
├───────────────────────────────┬──────────────────────────────────────────────┤
│ NYX-TA1  TEST  last 0.1 s     │ NYX-TA2  ⊘ LOSS OF SIGNAL 00:07              │
│ param        value   state    │ param        value     state                 │
│ cabin_press  97.90   ■RED LOW↓│ cabin_press  *101.29*  ⏱STALE 7.1 s          │
│ cabin_temp   21.40   OK       │ cabin_temp   *21.02*   ⏱STALE 7.1 s          │
│ bus_voltage  28.01   OK  …    │ …                                            │
├───────────────────────────────┴──────────────────────────────────────────────┤
│ ALARMS [veh▾][sev▾][state▾]  14:01:58.300Z ■RED LOW cabin_press TA1 UNACK [ACK]│
├──────────────────────────────────────┬───────────────────────────────────────┤
│ CHART cabin_press TA1 [2m|1h|24h]    │ COMMANDS  name  QUEUED SENT RCVD VERIF │
│ ▁▂▃▅▇ min/max band + mean line (UTC) │ isolate_valve 14:02:01Z …Z  …Z  …Z ✔   │
└──────────────────────────────────────┴───────────────────────────────────────┘
```
**Playwright ATP-02 sketch:** login (gesture arms audio) → `POST /sim/fault leak` → `expect(cell).toHaveAttribute('data-limit','YELLOW',{timeout:15000})` → `'RED'` → `window.__nyx.alerts === 1` → ack dialog with note → state `ACKED` → send `isolate_valve` → timeline shows 4 UTC times → fault off → alarm row `CLEARED`.

## Risks / what would make us switch
- Corporate proxies that buffer `text/event-stream` (FastAPI sets `X-Accel-Buffering: no`) or a requirement to stream commands up → switch to WebSocket with the same message shapes.
- More than 6 tabs on HTTP/1.1 → serve over HTTP/2 or use one SharedWorker per origin.
- Simulator emits noise-free constants → FROZEN false positives. Then make the detector per-param opt-in in `limits.yaml`.
- `MAXLEN ~ 3000` is too short at 10 vehicles × 100 Hz (3 s window) → resume degrades to snapshot. That is still correct, just heavier.
- S1 is one run per case, on localhost, with no proxy. Repeat it in R10 through the real compose proxy.

## Open questions → assumptions.md
- **A-R9-1** (ASSUMED, closes A-R3-6) Live push = SSE over `ui:events` (worker `XADD` after commit, API `XREAD` no group, `MAXLEN ~ 3000`). Commands and acks use REST.
- **A-R9-2** (ASSUMED) The STALE clock runs in the browser (`performance.now()` + server `age_ms`). Freshness is fed only by accepted (non-duplicate) frames.
- **A-R9-3** (OPEN) Does `freeze` advance `seq`/`t_onboard`? Detector: all analog params bit-identical, or `t_onboard` not advancing, for 20 frames → `acq=FROZEN`, shown like STALE, worker-side.
- **A-R9-4** (ASSUMED, closes R1 Q5) LOS = no accepted frame for 2 s while `hb` is alive. NO LIVE CONNECTION is a separate banner (no message for 3 s).
- **A-R9-5** (ASSUMED, closes R1 Q3) A STALE row keeps its last limit as an outline "was RED LOW" badge.
- **A-R9-6** (OPEN, with A-R8-11) Replay never sounds and shows frame-driven transitions only, no live ACK overlay.
- **A-R9-7** (ASSUMED) The login click arms audio. One beep per `alert:true` transition key, no repeat until a new key. A visible SOUND OFF badge appears if the context is not running.
- **A-R9-8** (ASSUMED) The 2 s ± 0.5 s tolerance holds for a visible tab. Background-tab throttling is accepted and documented.
- **A-R9-9** (ASSUMED) Light (ISA grey) theme only. The dark palette is verified for later.

## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources or package source. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Chromium EventSource default retry ≈ 3 s | VERIFIED | Chromium `event_source.cc`: `kDefaultReconnectDelay = 3000`; WHATWG: "a few seconds" | D-UI-03 (own watchdog, independent of it) |
| Yamcs and Open MCT have no frozen-value detection | VERIFIED | source search of both repos | D-ING-21 (FROZEN is our addition) |
| "In both, staleness comes from arrival time" | **CORRECTED** for Open MCT | Open MCT staleness is provider-defined (`subscribeToStaleness`); Yamcs is arrival-based ("expiration time") | none (prior-art wording only) |
| ISA-101 grey background, colour for abnormal | STILL UNVERIFIED | standard paywalled; ISA article and control.com 403; vendor summaries only | D-UI-07 (rationale stands on vendor practice + R14 (b)) |
| Recharts range `Area` | VERIFIED | `isRange` in recharts 3.10.1 `Area.js` | rejected alternative |
| uPlot `tzDate(…, 'Etc/UTC')` | VERIFIED | uPlot 1.6.32 `uPlot.esm.js` line 967 special-cases UTC / Etc/UTC | D-UI-11, D-UI-13 |
| rAF ≈ paint (double rAF) | STILL UNVERIFIED as a guarantee | WHATWG runs rAF callbacks before the rendering update, but does not define paint and lets the UA skip rendering opportunities | D-UI-14, D-PERF-02: report should call double rAF a heuristic |

CONTRADICTED: none (one wording correction, no decision affected).
