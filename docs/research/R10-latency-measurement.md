# R10 – Latency measurement method

**Drives:** GS-ING-09, GS-ING-10, GS-ALM-05, GS-UI-01, GS-DAT-03 (all method A), ATP-08, §5 rule "Measurements (method A) include how they were measured, on what machine, and the raw numbers"   **Answers review Q:** 11

## Question

How do we measure p95 onboard→stored (ING-09), triggering frame→alarm (ALM-05), frame→screen (UI-01), consumer lag at 2 × 100 Hz (ING-10) and 1 h query time (DAT-03) so that the numbers hold up in review? That means: which clock each stamp comes from, where each stamp is taken, how many samples we need, how p95 is computed, and what the report must contain. Everything runs in docker compose on one laptop: Docker Desktop on macOS, with the browser on the macOS host.

## Findings

### 1. Clock sources

- All containers on one Docker host share the host kernel's `CLOCK_REALTIME`. Linux time namespaces virtualize only `CLOCK_MONOTONIC*` and `CLOCK_BOOTTIME*`: "time namespaces do not virtualize the CLOCK_REALTIME clock" — [time_namespaces(7)](https://man7.org/linux/man-pages/man7/time_namespaces.7.html). So simulator `t_onboard`, the Redis stream ID, Postgres `clock_timestamp()` and worker `time.time()` all come from **one clock** (the VM kernel), and differences between them have no offset error.
- On macOS, Docker Desktop runs containers in a LinuxKit VM that "has its own internal clock". With no sync it "loses roughly 2ms for every 3s", and Docker added an SNTP client that resyncs to the host clock — [Docker blog, 2019](https://www.docker.com/blog/addressing-time-drift-in-docker-desktop-for-mac/). After host sleep the VM clock has been reported to lag by seconds to hours until Docker restarts — [docker/for-mac#2076](https://github.com/docker/for-mac/issues/2076), [forum thread](https://forums.docker.com/t/syncing-clock-with-host/10432). Neither source has an official current docs page (UNVERIFIED for the Docker 29 engine).
- **Spike S1 (measured 2026-11-13, 17:17–17:21 UTC), VM clock vs macOS clock.** Machine: Apple M4 Pro, 12 cores, 24 GB RAM, macOS 27.2.1; Docker Desktop engine 29.1.2, kernel 6.12.54-linuxkit, VM 12 CPU / 8 GB; clocksource `arch_sys_counter`. Method: NTP-style UDP ping. A `python:3.12-slim` container answers with `time.time()`; the client computes `offset = t_remote − (t0+t1)/2`, and the error is ≤ RTT/2. Offset is the median of the 5 % lowest-RTT pings.
  - macOS → container, 18 rounds × 400 pings over 3.5 min: offset **−0.31 to −1.00 ms** (VM behind the host), RTT min 0.21–0.33 ms. Raw rounds: −0.496, −0.311, −0.360, −0.400, −0.351, −0.390, −0.434, −0.350, −0.392, −0.698, −0.736, −0.781, −0.935, −0.997, −0.767, −0.814, −0.770, −0.778 ms.
  - container → container, n = 2000: offset 0.054–0.083 ms. The true value is 0 (same kernel), so this is the **error floor of the method**.
  - The VM process list shows no `ntpd`, `chronyd` or `sntpc` process, so the current sync mechanism is UNVERIFIED (it is probably host-driven over vsock). The offset stayed bounded, so something keeps resyncing the VM.
  - Conclusion: crossing the VM↔macOS boundary adds about 1 ms of uncertainty. That does not matter against the 1 s UI-01 budget, but only if the laptop does not sleep during or just before the run.
- Python: `time.monotonic()` and `perf_counter()` have an undefined reference point, "so that only the difference between the results of two calls is valid". `time.time()` "can return a lower value than a previous call if the system clock has been set back" — [Python time docs](https://docs.python.org/3/library/time.html). Use `time.time()` (or `time_ns()`) for stamps that cross processes, and `perf_counter()` only for durations inside one process (DAT-03).
- Browser: `performance.now()` is monotonic, relative to `timeOrigin`, and coarsened to 100 µs when not cross-origin isolated. `Date.now()` has 1 ms resolution and "may have been impacted by system and user clock adjustments, clock skew" — [MDN performance.now](https://developer.mozilla.org/en-US/docs/Web/API/Performance/now). For a 1 s budget, 1 ms is plenty, and `Date.now()` is in the same wall-clock domain as the offset ping. This avoids the separate question of `timeOrigin` skew.
- Postgres: `now()` = `transaction_timestamp()` returns "the start time of the current transaction", and its value does "not change during the transaction". `clock_timestamp()` "returns the actual current time" — [PG 17 §9.9.5](https://www.postgresql.org/docs/17/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT). **R6's `t_received DEFAULT now()` is wrong for ING-09:** with batched inserts it stamps the start of the transaction, which is before the INSERT runs, so it understates latency. Change it to `DEFAULT clock_timestamp()`.
- Redis stream IDs carry a free middle stamp. The ID's ms part "is actually the local time in the local Redis node generating the stream ID". If the clock goes backwards, the previous entry's time is used — [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/). With ms resolution in the VM clock domain, this splits ING-09 into onboard→XADD (including AOF `always` fsync, R3's unmeasured cost) and XADD→stored.

### 2. Where to stamp

| Metric | Start stamp | End stamp | Clock domain | Tool |
| --- | --- | --- | --- | --- |
| ING-09 onboard→stored | `t_onboard` (simulator `time.time()`, ISO 8601 **with µs**) | `telemetry.t_received DEFAULT clock_timestamp()` (per row, at INSERT) | VM kernel, one clock | SQL export → CSV; nearest-rank p95 in Python |
| ING-09 stage split (diagnostic) | `t_onboard` | stream ID ms → `t_received` | VM | same CSV, extra column `t_xadd` (the worker keeps the entry ID) |
| ING-10 lag | — (sampled every 1 s) | `XINFO GROUPS` `lag` + `pending` per `tm:<v>`; time lag = `clock_timestamp() − max(t_onboard)` per vehicle | VM | `perf/lag_sampler.py` (1 Hz loop) → CSV |
| ALM-05 frame→alarm | `t_onboard` of the frame that **completes** the condition (the N-th frame for persistence N), stored on the alarm row as `trigger_t_onboard` | `alarm.raised_at DEFAULT clock_timestamp()` | VM | SQL |
| UI-01 frame→screen | `t_onboard` from the WS payload | `Date.now()` in a nested `requestAnimationFrame` after the React commit that shows `seq ≥` this frame | VM → macOS, corrected by the measured offset | in-page probe; Playwright `page.evaluate` collects → CSV |
| DAT-03 1 h query | `perf_counter()` before the HTTP GET | `perf_counter()` after the body has been read | one process, monotonic | Python loop, 100 random 1 h windows |

- Both DB stamps are taken at INSERT, before COMMIT, so they leave out commit/WAL-flush time. That bias is small but UNVERIFIED. Check it once by having the worker log `time.time()` after `commit()` returns for one run, and report the difference.
- `requestAnimationFrame` runs its callback "before the next repaint". Its timestamp is "the end time of the previous frame's rendering", and "the soonest your newly calculated value(s) will be rendered is in the next frame". rAF is "paused … in background tabs or hidden iframes" — [MDN rAF](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame). Stamping inside a **second** rAF after the commit means the frame holding the change has been rendered. Compositor/display scan-out ("photons") is not included (UNVERIFIED, ~1 refresh). The Playwright page must stay in the foreground.
- **UI attribution without selection bias:** the console coalesces updates, so many frames are never drawn on their own. The probe keeps a `seq → t_onboard` map. Each paint stamps **every** pending frame with `seq ≤` displayed seq, so every frame gets a sample: "on screen, or superseded by something newer, at t". Frames that never reach the browser are counted as missing, not dropped.
- UI offset correction: before and after the run the probe makes 50 `fetch('/api/health')` calls, which return `server_time = time.time()`. It keeps the minimum-RTT sample and computes `offset = server_time − (t0+t1)/2` (the same method as S1). Report offset_before, offset_after and RTT/2. If |Δ| > 5 ms (a host sleep or clock step), the run is invalid. On a Linux CI runner the browser and containers share one kernel, so offset ≈ 0 by construction.

### 3. Percentiles honestly

- Latency is "multi-modal … heavily skewed to the right", so use nonparametric statistics. A percentile claim means "at least 99% of all measurement results took at most 3 seconds" — [Hoefler & Belli SC15, §3.1](https://htor.inf.ethz.ch/publications/img/hoefler-scientific-benchmarking.pdf). That definition is **nearest-rank**: `sorted(x)[ceil(0.95·n) − 1]`, using the stdlib with no interpolation. `statistics.quantiles` defaults to `method='exclusive'` (rank i/(m+1)) — [Python statistics](https://docs.python.org/3/library/statistics.html). With n = 20 it interpolates between the 19th and 20th values, so state the method in the report.
- **Sample size, distribution-free (computed: `ci.py` in the scratchpad spike, binomial order statistics).** The probability that the max of 20 samples is still below the true p95 is 0.95²⁰ = **0.358**. So with ALM-05's 20 alarms no sample value is a 95 % upper confidence bound on p95. The smallest n where the max is such a bound is **59**. Upper 95 % bound on p95: n = 100 → 2nd-largest; n = 300 → 9th-largest; n = 3000 → 131st-largest. Hoefler Rule 5: "For nondeterministic data, report confidence intervals". Their nonparametric CI is from Le Boudec.
- Volumes: ING-09 at 2 × 10 Hz over 10 min gives 12 000 samples, and UI-01 the same. ALM-05: run **60** scripted alarms and report both the spec's 20 and all 60. DAT-03: 100 queries.
- **Store raw samples, not HdrHistogram.** At 2 × 100 Hz × 300 s = 60 000 rows × ~40 B ≈ 2.4 MB of CSV, and §5 demands the raw numbers anyway. HdrHistogram's value is bounded memory and its CO correction (below). Neither is needed here.
- **Coordinated omission (CO).** "Monitoring tools drop a lot of potential samples in the result set". A closed-loop tester "will wait to send the next request", so a 100 s stall shows up as one sample instead of ~10 000 — [Gil Tene, How NOT to Measure Latency, QConSF 2015 slides](https://videog.infoq.com/downloads/pdfdownloads/presentations/QConSF2015-GilTene-HowNOTtomeasureLatency.pdf), [summary](https://highscalability.com/your-load-generator-is-probably-lying-to-you-take-the-red-pi/). HdrHistogram's fix synthesizes the missing samples: `recordValueWithExpectedInterval` / `copyCorrectedForCoordinatedOmission`. These are "mutually exclusive, and only one of the two should be be used" — [HdrHistogram JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html).
  - Our producer is **open loop**, and every frame is its own sample with its own `t_onboard`. A backlog therefore shows up as many large samples, not one, and **no correction is needed**, under three conditions:
    - (a) The simulator ticks on an absolute schedule (`next = start + k·period`), not with `sleep(period)` after work. Otherwise a slow simulator silently lowers the offered rate. Report the achieved rate and the `t_onboard` interval jitter.
    - (b) The simulator stamps `t_onboard` before XADD and keeps buffered frames' original stamps (A-R3-1).
    - (c) Frames never stored, or never painted, are reported as a count, never silently dropped.
  - DAT-03 is closed-loop, but its client is single and sequential, and it measures query cost, not responsiveness under load. CO is irrelevant there.
- **Warm-up and exclusions are declared before the run.** Hoefler: "the first measurement iteration should be excluded … It will not affect the median or other ranks if enough measurements are taken". Rule: drop the first 10 s of each run from the p95 metrics, but report the count and max of the dropped samples. No faults are injected in ATP-08, so A-R4-3 (takeover window excluded) does not arise. If a run includes a worker kill, report that window as a separate row and never drop it silently.

### 4. ING-10 load and lag

- `XINFO GROUPS` `lag` is "the number of entries in the stream that are still waiting to be delivered to the group's consumers, or a NULL when that number can't be determined". It is `entries_added − entries_read`. `entries_read` is "only a heuristic" and goes NULL after `XGROUP SETID` to an arbitrary ID, or when entries between last-delivered and last-generated were deleted or trimmed. Then it "is restored automatically" — [XINFO GROUPS](https://redis.io/docs/latest/commands/xinfo-groups/).
- `lag` excludes delivered-but-unacked entries, so the backlog is `lag + pending`. Both count entries, while ING-10 says "lag < 1 s", which is a time. Therefore sample every 1 s, per vehicle: `lag`, `pending`, and **time lag = `clock_timestamp() − max(t_onboard)`** (the existing `(vehicle, t_onboard DESC)` index makes this one index probe). Time lag includes the ≤ 10 ms frame period at 100 Hz. Pass = **max over all samples** < 1 s ("stays"), not p95. A NULL `lag` is recorded as NULL and footnoted.
- Two runs: **run N** at 2 × 10 Hz for 10 min (ING-09, ALM-05, UI-01, DAT-03 pass/fail at the specified rate), and **run P** at 2 × 100 Hz for 5 min (ING-10 pass/fail; the other metrics reported for information). This takes 15 min plus setup. One optional **run P′** with `--appendfsync everysec` is identical otherwise. It quantifies R3's fsync cost through the onboard→XADD stage split.

### 5. Report format

Hoefler Rule 9: "Document all varying factors … complete experimental setup (e.g., software, hardware, techniques)". The R6 spike lost 5–8× to a host load of 14–23, so record `uptime` before and after, and require an idle host.

```markdown
## ATP-08 <metric> — <PASS/FAIL> (<req ID>)
Requirement: <pass condition, verbatim>.  Run: <N|P>, <date UTC>, git <sha>, `make perf RUN=<N|P>`.
Machine: <CPU>, <cores>, <RAM>, <OS ver>; Docker <engine ver>, VM <cpus>/<mem>; compose limits: <none|…>;
images: <name@digest …>; load avg before/after: <x>/<y>; laptop on AC, sleep disabled.
Method: start = <stamp>, end = <stamp>, clock domain = <…>; offset correction = <n/a | before/after ms ± RTT/2>.
Window: <start–end UTC>, warm-up excluded: first 10 s (<k> samples, max <x> ms); other exclusions: <none|…>.
Samples: n = <n>, missing/never stored = <m>; achieved rate = <Hz>, t_onboard jitter p99 = <ms>.
| n | p50 | p95 (nearest-rank) | p99 | max | 95 % upper bound on p95 | target |
| --- | --- | --- | --- | --- | --- | --- |
Raw: `docs/perf/<run>-<metric>.csv` (columns: vehicle, seq, t_onboard, t_xadd, t_end, latency_ms).
Reproduce: `make perf RUN=<N|P>` → `scripts/perf_report.py` regenerates this table from the CSVs.
Notes/NCR: <link if FAIL/PARTIAL>.
```

## Decision candidate

**Chosen:**
- **Clock domain.** End-to-end stamps all live in the VM kernel clock, starting from `t_onboard`. `t_received` and `raised_at` use `clock_timestamp()` (not `now()`), and the Redis entry ID gives a free middle stamp. The only cross-domain metric is UI-01. It is corrected by a browser-side min-RTT offset ping before and after the run, and invalidated if the offset moves by > 5 ms.
- **Samples and statistics.** Raw samples go to CSV in `docs/perf/`, with nearest-rank percentiles from the stdlib and a distribution-free upper bound on p95. ALM-05 uses 60 alarms. Warm-up exclusions are declared in advance. CO is avoided by an open-loop, absolute-schedule producer plus per-frame samples.
- **ING-10.** A 1 Hz sampler records `XINFO GROUPS` lag + pending and the time lag. Pass = max time lag < 1 s.

**Because:** every stamp is one line of code or one column default; it adds no dependency; and §5's "raw numbers" requirement is met directly.

**Rejected:**
- HdrHistogram: bounded memory and CO correction are not needed for an open loop with ≤ 60 k samples.
- `now()`: transaction-start bias.
- Playwright polling the DOM for a value: it adds a polling interval, and samples only frames that happen to be visible.
- Measuring UI latency in macOS time only: `t_onboard` is stamped in the VM, so it cannot be done.
- 20 alarms only: no confidence bound is possible.

## Risks / what would make us switch

- **Host sleep or a clock step mid-run** breaks UI-01 (VM clock lag after wake has been reported). Mitigations: before/after offset check, `caffeinate -dims` during runs, and rerun on CI Linux, where one kernel removes the issue.
- **The `clock_timestamp()` stamp excludes COMMIT.** If the post-commit check shows > 10 ms bias, stamp `t_stored` in the worker after commit and log it to CSV instead.
- **A simulator that cannot hold 100 Hz in Python** (GIL, JSON encode) would understate the load. The achieved-rate and jitter line in the report exposes it. If it fails, run one simulator process per vehicle.
- **The probe must stay out of production paths.** Gate it behind `?perf=1` and keep it in its own module.
- **More samples than memory (10 vehicles × 100 Hz for hours, review Q11):** switch to HdrHistogram (Python `hdrhistogram` package), still with no CO correction needed for the open loop.

## Open questions → assumptions.md

- **A-R10-1 (ASSUMED):** "stored" = row inserted, stamped `clock_timestamp()` at INSERT. Commit time is excluded, its bias is measured once and reported. R6 DDL changes `DEFAULT now()` → `clock_timestamp()`.
- **A-R10-2 (ASSUMED):** ALM-05 starts at the `t_onboard` of the frame that completes the alarm condition (the N-th frame for persistence, the window end for ROC), not the first out-of-limit frame. The alarm row stores `trigger_seq` and `trigger_t_onboard`.
- **A-R10-3 (OPEN):** run 60 alarms instead of the spec's 20 (n ≥ 59 needed for a 95 % bound). Report both.
- **A-R10-4 (ASSUMED):** UI-01 ends at the browser paint (nested rAF + `Date.now()`), not display photons. Frames superseded before paint take the stamp of the paint that showed a newer seq.
- **A-R10-5 (OPEN):** ING-10 "lag < 1 s" = max over 1 s samples of time lag (`now − newest stored t_onboard`). Entry lag (`XINFO` lag + pending) is reported alongside.
- **A-R10-6 (OPEN):** ING-09, ALM-05 and UI-01 pass/fail at the specified 10 Hz in run N. ATP-08's 100 Hz run P carries ING-10, with the other metrics reported for information.
- **A-R10-7 (ASSUMED):** percentile = nearest-rank; the first 10 s of each run are excluded, with the excluded count and max reported.
- **A-R10-8 (ASSUMED):** `t_onboard` is serialized with µs precision (ISO 8601 `YYYY-MM-DDTHH:MM:SS.ffffffZ`). With whole seconds, ING-09 is unmeasurable.
