# R22 – LOS/AOS thresholds and the post-outage grace: what ground stations and failure-detector theory say

**Drives:** D-ING-08, D-ING-21, D-ALM-04, D-UI-15, D-UI-21, NCR-04   **Asked by:** coordinator, 2026-11-19

Sources:
- documents fetched and quoted on 2026-11-19: NASA DSN 810-005 module 208 Rev. B, CCSDS 130.1-G-3, RFC 6298;
- PaperLab workspace **Nyx ground segment design**: Chen, Toueg & Aguilera 2002 and Hayashibara et al. 2004 are metadata only (no free PDF), so they are cited for the method; Dixon et al. 2007 is abstract only;
- one check against the real alarm engine ([`scripts/r22_slow_source.py`](scripts/r22_slow_source.py)).

## Question

Today:
- **LOS:** 2 s of vehicle-time gap or silence (`link.py` `STALE_S = 2.0`).
- **After a broker outage:** the LOS clock holds for a fixed `RESUME_GRACE_S = 5 s` (`worker.py:57`). It was taken from the simulator's worst resume time: send backoff capped at 1 s plus a 2 s Redis client timeout, about 3 s (NCR-04).

Is a fixed 2 s / 5 s defensible, or should it scale with the frame rate or the measured resume time?

## Findings

### F1 – Ground stations count missed frames; they don't run a fixed timeout (context)

- **NASA DSN** (810-005, 208 Rev. B, pp. 15–17): the frame synchronizer has four modes: Search, Verify, Lock, Flywheel.
  - On losing lock, it "will continue this process until from 0 to 31 ASMs have been missed at which point it will switch to the Search mode".
  - To regain lock, "[t]his number of frames that must be successfully detected before declaring lock can be set over the range of 0 to 31".
  - Table 2 lists "Maximum flywheel frames 0 to 31".
- **CCSDS 130.1-G-3** (p. 9-10, Fig. 9-3, two "Flywheel" and two "Acquire" states): "Once locked, some number of consecutive ASMs may be missed in flywheel states before a loss of lock is declared".
- **Takeaway:** loss is declared after **N missed frame periods**, with **hysteresis** (M good frames to re-acquire), both counted in frames, so the time scales with the frame rate.
  - Nyx's 2 s is 20 periods at 10 Hz, inside the DSN's 0–31, and 200 periods at 100 Hz.
  - The steady-state jitter is small (ingest p95 49.9 ms at 1,000 frames/s, `scaling.md`), so 2 s has about 40× margin at today's rates. **The fixed 2 s is defensible at 10 and 100 Hz.**

### F2 – Gap: a source slower than 2 s per frame never alarms, and LOS flaps (bench-facing, high)

- **The same `STALE_S` drives two rules:**
  - the LOS clock;
  - the engine's data-STALE reset (`link.on_frame`: vehicle-time gap > `STALE_S` → `fold.py:113` `reset`), which restarts every persistence ring (D-ALM-04).
- **Effect:** at a frame period above 2 s, every frame is a data-STALE gap, so every ring restarts and never reaches N = 3.
- **Checked with the real engine** (`_slot_step`, cabin-temp limits, 20 frames at 35.0 °C, RED):

| Frame period | Debounced level after 20 RED frames |
| --- | --- |
| 0.1 s | RED |
| 1.0 s | RED |
| **3.0 s** | **NOMINAL: no alarm, ever** |

- The same source would also show LOS between every two frames (2 s of silence), with an AOS on each frame and a loss tone at each onset (D-UI-21).
- **When it bites:** the simulator runs at 10 Hz, so nothing fails today. Slow housekeeping telemetry (0.1–1 Hz) is common on real vehicles, and the bench interface isn't defined yet (§3.5).
- **Change (cheap, uses existing state):** `seq.py` already tracks an exponentially smoothed `period_s` per vehicle (`period = 0.9·period + 0.1·dt`, the same estimator shape as RFC 6298's SRTT). Use `stale_s = max(STALE_S, K · period_s)` with **K = 5** missed periods, for both the LOS clock and the data-STALE reset.
  - **No change at 10 or 100 Hz:** the 2 s floor wins.
  - **At 1 frame per 3 s:** 15 s.
- **Tests:**
  - `unit/test_link.py::test_slow_source_not_stale`: period 3 s, no data-STALE and no LOS between frames; LOS after 5 periods of silence.
  - `unit/test_engine.py::test_slow_source_still_alarms`: the table above as a test.

### F3 – The 5 s grace is a timeout fixed from one producer; make it the producer's parameter (medium)

- **Failure-detector theory** frames a timeout as a trade-off between **detection time** and **false detections**: Chen, Toueg & Aguilera 2002 define failure-detector QoS by "how fast [it] detects actual failures" and "how well it avoids false detections" (metadata; wording from the paper's abstract).
  - Adaptive detectors set the timeout from the measured arrival-delay distribution rather than a constant (Hayashibara et al. 2004, the φ accrual detector; metadata).
  - TCP does the same with a floor: RTO = SRTT + max(G, K·RTTVAR), K = 4, and an RTO under 1 s "SHOULD be rounded up to 1 second" (RFC 6298 §2).
- **What the grace bounds:** the **producer's** resume time after the broker returns, not ingest's. NCR-04 traced it to the simulator's own settings (≤ 1 s backoff + 2 s client timeout). Before the grace, about 1 CI run in 6 showed a false LOS, so the resume tail sat above 2 s on CI runners.
  - A real bench source will have different reconnect logic and a different resume time, so 5 s may be wrong in either direction.
- **Change:**
  1. **Make the grace a per-source value from the interface definition**: the source's documented maximum time from broker-up to its first frame, plus a margin. Default 5 s, as today. Add "maximum resume time after a broker outage" to the §3.5 ICD-B checklist.
  2. **Measure it rather than assume it:** log `resume_ms` (broker up → first new frame, per vehicle) on every recovery. The chaos soak already causes broker outages, so it yields the distribution. Then check grace ≥ mean + 4·sd (the RFC 6298 rule) and ≥ the observed maximum.
  3. **Optional, later:** set the grace adaptively from the logged values, as max(5 s, mean + 4·sd), capped at 30 s. Only worth doing if bench sources vary. One constant plus the check in 2 is enough for one simulator.
- **Evaluate the way the theory does:** report two numbers for the outage scenarios (chaos and `test_broker_banner`): the **false-LOS rate** per 100 outages and the **detection time** of a real loss after an outage (the grace plus `stale_s`, 7 s today). A change is better only if it lowers one without raising the other past the operator's need.

### F4 – Why false LOS matters more than a slightly later true LOS (human factors, confirmation)

- **The trade-off:** a false LOS triggers the banner and the loss tone (R18 F1), a sound that tells the operator monitoring is lost.
- **The evidence:** Dixon, Wickens & McCarley 2007 (verified against the abstract): "False alarm-prone automation hurt overall performance more than miss-prone automation. False alarm-prone automation also clearly affected both operator compliance and reliance, whereas miss-prone automation appeared to affect only operator reliance." Effect sizes need the full text.
- **The direction this supports:** set the grace conservatively, toward fewer false LOS, which is what F3's "grace ≥ mean + 4·sd" does. Make up for the later detection with the GROUND PROCESSING DOWN – BROKER banner, which already covers the outage itself (D-UI-15).

## Decision candidate

- **Chosen:**
  - **F2: `stale_s = max(2 s, 5 · period_s)`** for LOS and data-STALE. It is a real silent failure for slow sources, a one-line fix that reuses `period_s`, and it changes nothing at today's rates.
  - **F3 (1)–(2): the grace becomes the source's documented resume bound**, checked against logged resume times.
- **Kept:** 2 s at 10/100 Hz, defensible (F1); 5 s as the simulator's value.
- **Rejected:**
  - A grace that scales with the frame rate: resume time is producer reconnect logic, not frame spacing.
  - A fully adaptive grace now: one producer, little variance; revisit with a second source.

## Open questions → assumptions.md

- A-R22-1: K = 5 missed periods for slow sources. The DSN allows 0–31 flywheel frames, and 5 is a conservative middle. Should the owner prefer a per-vehicle configured value?
- A-R22-2: The bench's maximum resume time after a broker outage, an ICD-B item. Default 5 s.

## Sources

- NASA DSN Telecommunications Link Design Handbook 810-005, module 208 Rev. B, Telemetry Data Decoding, pp. 15–17: https://deepspace.jpl.nasa.gov/dsndocs/810-005/208/208B.pdf
- CCSDS 130.1-G-3, TM Synchronization and Channel Coding: Summary of Concept and Rationale (June 2020), p. 9-10: https://ccsds.org/Pubs/130x1g3e1.pdf
- RFC 6298, Computing TCP's Retransmission Timer (2011): https://www.rfc-editor.org/rfc/rfc6298
- Chen, Toueg & Aguilera 2002, On the quality of service of failure detectors, IEEE Trans. Computers, 10.1109/12.980014 (metadata)
- Hayashibara, Défago, Yared & Katayama 2004, The φ accrual failure detector, SRDS, 10.1109/RELDIS.2004.1353004 (metadata)
- Dixon, Wickens & McCarley 2007, Human Factors, 10.1518/001872007X215656 (abstract)
- Nyx: `src/gs/ingest/link.py`, `seq.py`, `fold.py`, `worker.py`; `docs/ncr/NCR-04.md`; `docs/perf/scaling.md`

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
