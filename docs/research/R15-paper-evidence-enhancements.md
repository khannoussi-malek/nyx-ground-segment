# R15 – Paper evidence round: what the literature changes in the design

**Drives:** GS-ING-07, GS-ENG-02, GS-UI-04, GS-DAT-03, method-A reqs, GS-EXT-03   **Answers review Q:** 8, 11

PaperLab workspace: **Nyx ground segment design** (the single Nyx workspace from 2026-11-14; the 9 papers of "Nyx alarm management" were copied in). New papers this round are listed under Sources. Passages quoted below were found with `plab.py find` in the full text unless marked UNVERIFIED.

## Question

The design (D-xxx) was built from docs, vendor references and spikes. Which decisions does the systems and HCI literature confirm, and where does it point at a gap?

## Findings

### E1 – Healthy frames can reach the DLQ as POISON during an operational-error restart loop (gap, high)

- D-ING-04 sends an entry to the DLQ when `times_delivered > 3`, on every read path. D-ING-24 exits without XACK on any operational Postgres error, and `restart: unless-stopped` restarts the worker, which re-reads its own PEL (D-ING-03). The takeover path (D-ING-11) uses `XAUTOCLAIM … 0` on the peer.
- **Measured (2026-11-14, `redis:8.2`, then 7-alpine):** one `XREADGROUP >` read plus three `XREADGROUP … 0` re-reads of the same consumer's PEL gives `times_delivered = 4`. Re-reading history increments the counter, and `XAUTOCLAIM` does too.
- So an operational error that persists while the connection stays up sends the in-flight batch to `tm:dlq` with `reason = poison` after about three restarts, which Docker's restart backoff reaches in under a second. Examples of such errors: the role is made read-only (SQLSTATE 25006), disk full (53100), `statement_timeout` (57014) or deadlock (40P01). The frames are not lost, but they are missing from `telemetry`, they raise POISON events, and they break chaos invariant I6 (`poison = 0`).
- The chaos soak (§11.5) has **no Postgres action**, so neither the soak nor the §3.6 test list exercises this path. `test_crash_every_time_frame_to_dlq` only covers a frame that really is bad.
- Literature: Yuan et al. (OSDI 2014) found that "almost all catastrophic failures (92%) are the result of incorrect handling of non-fatal errors explicitly signaled" (p. 9). The delivery counter conflates "this frame kills us" with "the database failed while this frame was in flight".
- **Fix tested in Redis:** `XCLAIM tm:<v> ingest <me> 0 <ids…> RETRYCOUNT 0 JUSTID` resets the counter to 0 (measured: 4 → 0, and 1 after the next re-read). Run it on the D-ING-24 controlled-exit path for the batch being rolled back, before `sys.exit(1)`. If Redis is down too, skip it; the existing behaviour remains. A real crash (`os._exit`, segfault, OOM kill, `CRASH_AT`) never reaches this path, so its frames still count and still reach the DLQ after 3 deliveries.
- **Test:** `test_operational_error_loop_keeps_frames`: make the ingest role read-only (`ALTER ROLE … SET default_transaction_read_only = on`) for 10 s while both vehicles stream, then revert. Expected: all frames stored, `tm:dlq` empty. **Chaos action:** the same toggle as an infrastructure action, so I6 covers it.

### E2 – Chart downsampling: add first/last per bucket and size the bucket to the pixel (UI fidelity, medium)

- D-DB-04 returns `min/max/avg/count` per `time_bucket`, with bucket = max(window / 1500, 100 ms).
- M4 (Jugel et al., PVLDB 2014) groups the series into one time span per pixel column and keeps min, max, **first and last** per group. Plain MinMax "does not guarantee an error-free line visualization of the time series. It ignores the important first and last tuples of the each group" (p. 5).
- Change: add `first(value, t)` and `last(value, t)` (TimescaleDB built-ins) to the query, and let the client pass the chart's pixel width as the bucket count instead of the fixed 1500. uPlot already draws the min/max band, and the line goes through first/last instead of avg. MinMaxLTTB (Van Der Donckt et al., IEEE VIS 2023) is the option if fewer points are needed; not needed at 24 h × 10 Hz.

### E3 – p95 with a confidence interval (measurement, low cost)

- D-PERF-03 reports nearest-rank percentiles from one run.
- Hoefler & Belli (SC 2015) recommend reporting rank statistics with a non-parametric 1−α confidence interval and checking whether n is large enough (pp. 5, 8). For p95 the CI comes from binomial order statistics (stdlib, about 10 lines). This also states precisely why D-PERF-04 needs n ≥ 59.
- Change: the perf report gives p95 together with its 95 % CI (ranks from `binom`), computed in the same stdlib script.
- Confirmation: Karimov et al. (ICDE 2018) define latency from the generator's timestamp, so queueing time counts ("the longer an event stays in a queue, the higher its latency", p. 3). This is D-PERF-01's `t_onboard`-based method. They also keep the driver off the system-under-test machines (p. 3). Nyx can't on one laptop, so D-PERF-08 should state that limitation in one line.

### E4 – Error-handler lint (cheap, follows E1)

- Yuan et al. found that 23 % of catastrophic failures came from error handling that simple checks would catch: empty handlers, `TODO` handlers, and aborts on non-fatal errors (p. 10).
- `pyproject.toml` selects only `E, F, I, UP, B`. Adding ruff `BLE001` (blind `except`) and `S110` (`try/except/pass`) makes those mistakes fail CI. Any intentional broad catch (e.g. the tracker transaction, D-ING-24 (3)) gets an inline `noqa` that explains it.

### Confirmations (no change)

- **Ack after commit, plus dedup before user code** (D-ING-02, D-ING-13) is MillWheel's delivery protocol: check dedup data, run user code, commit pending changes, then ACK the senders (Akidau et al. 2013, p. 6).
- **Snapshot of state in the same transaction as the output** (D-ALM-11) is the atomic state-plus-output commit that Flink (Carbone et al. 2017) and Kafka Streams (Wang et al. 2021) use for exactly-once processing. **VERIFIED** for Flink in full text (Carbone et al. 2017 p. 8: "Once a snapshot is fully committed the version is atomically incremented"; p. 9: sinks use "transactions to offer delivery guarantees"). Kafka Streams (Wang et al. 2021): **STILL UNVERIFIED** (ACM PDF blocked).
- **Report freeze on the vehicle watermark** (D-DB-10) matches the watermark semantics of Begoli et al. (PVLDB 2021).
- **Seeded, replayable chaos soak** (D-EXT-15 `CHAOS_SEED`) plus pure `step/decide/classify` functions follow the FoundationDB deterministic-simulation approach in spirit (Zhou et al. 2021, p. 8); there is nothing to add at this scale.

### Out of scope, recorded for v1+

- Learned anomaly detection on telemetry. If it is ever added, evaluate it on ESA-ADB (Kotowski et al. 2024) or the OPS-SAT benchmark (Ruszczak et al. 2025) with event-wise metrics. Wu & Keogh (ICDE 2022) show that many anomaly benchmarks can be solved by one-line baselines, so results on simulator data would prove little.
- Metastable failures (Bronson et al., HotOS 2021; Huang et al., OSDI 2022): retry or backlog loops that keep a system degraded after the trigger is gone. The relevant Nyx loops are the simulator buffer flush (D-SIM-05) and the takeover drain. Nyx's drain is bounded (6000 frames) and the workers have headroom, so this needs no design change. Huang et al. name the condition to keep: a rebooted server must be "fast enough to catch up with the missed and ongoing load" (p. 7). For Nyx, the D-PERF-09 headroom number states that.

## Decision candidate

Chosen: E1 (reset counter on the controlled exit, plus test and chaos action), E2, E3, E4. Because: E1 is a correctness gap against I6 and ING-07, reproduced in Redis. E2–E4 are a few lines each and backed by the papers above. Rejected: changing the poison rule to count only in-process failures (loses the crash-every-time case that RV-06 needs); a separate retry-count key in Redis (a second source of truth for the counter that RETRYCOUNT already provides).

## Risks / what would make us switch

- If `XCLAIM … RETRYCOUNT` behaviour changes in a later Redis, the E1 test catches it.
- If a real operational error is frame-specific (e.g. a lock timeout caused by one frame), resetting would loop forever. The current classes don't contain such a case. If one appears, classify it as data (D-ING-24 (2)).

## Open questions → assumptions.md

- A-R15-1: Should the Postgres read-only toggle join the chaos soak's infrastructure actions (it adds one more action to the 90–120 s schedule)? Default: yes.

## Sources (PaperLab: Nyx ground segment design)

Akidau et al. 2013 MillWheel (PVLDB) · Akidau et al. 2015 The Dataflow Model, 10.14778/2824032.2824076 · Begoli et al. 2021 Watermarks, 10.14778/3476311.3476389 · Carbone et al. 2017 State management in Apache Flink, 10.14778/3137765.3137777 · Wang et al. 2021 Consistency and Completeness (Kafka), 10.1145/3448016.3457556 · Gray & Cheriton 1989 Leases, 10.1145/74850.74870 · Karimov et al. 2018 Benchmarking Distributed Stream Data Processing Systems, 10.1109/icde.2018.00169 · Dean & Barroso 2013 The Tail at Scale, 10.1145/2408776.2408794 · Hoefler & Belli 2015 Scientific benchmarking of parallel computing systems · Jugel et al. 2014 M4, 10.14778/2732951.2732953 · Van Der Donckt et al. 2023 MinMaxLTTB, 10.1109/vis54172.2023.00013 · Zhou et al. 2021 FoundationDB, 10.1145/3448016.3457559 · Yuan et al. 2014 Simple Testing Can Prevent Most Critical Failures (OSDI) · Bronson et al. 2021 Metastable failures, 10.1145/3458336.3465286 · Huang et al. 2022 Metastable failures in the wild (OSDI) · Wu & Keogh 2022, 10.1109/icde53745.2022.00116 · Kotowski et al. 2024 ESA-ADB, arXiv:2406.17826 · Ruszczak et al. 2025 OPS-SAT benchmark, 10.1038/s41597-025-05035-3 · Helland 2016 Life Beyond Distributed Transactions, 10.1145/3012426.3025012 · Raasveldt & Mühleisen 2019 DuckDB, 10.1145/3299869.3320212


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| Atomic state + output commit (Flink, Kafka Streams) | VERIFIED (Flink) / STILL UNVERIFIED (Kafka) | Carbone 2017 p. 8–9 | D-ALM-11 |

CONTRADICTED: none.
