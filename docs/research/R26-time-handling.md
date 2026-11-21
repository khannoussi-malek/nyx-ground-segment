# R26 – Time handling: UTC, TAI, leap seconds, onboard clocks and what breaks in Nyx

**Drives:** D-SIM-02/03, D-ING-13, D-ING-18, D-ING-21, D-REP-05, archive sort (`archiver/parquet.py`), ING-09 latency, the §3.5 bench ICD   **Asked by:** coordinator (nyx-c3), 2026-11-21

**Evidence levels:**
- **[S]** standard or authoritative text, quoted with clause and page;
- **[D]** vendor or tool documentation, quoted;
- **[T]** tested on 2026-11-21 (pydantic 2.14.3, Python 3.14, PostgreSQL 17.11 in `nyx-db`);
- **[C]** Nyx code read on `origin/main` at f35af22;
- **[A]** analysis, mine.

Sources are saved in `papers/web/` (git-ignored; URLs in [SOURCES.md](SOURCES.md)).

## Question

Nyx stamps every frame with `t_onboard`, an ISO 8601 UTC string with µs (D-SIM-02). It accepts only `t_onboard ∈ [now − 7 d, now + 5 min]` (D-ING-18) and keys storage on `(vehicle, t_onboard, seq)` (D-ING-13). The sequence tracker, LOS, data-STALE and FROZEN all run on vehicle time. What do UTC, TAI, GPS time, leap seconds, the CCSDS time codes and onboard clock correlation imply? What breaks at a leap second or with a drifting onboard clock?

## Background (what the standards say)

**Leap seconds are still possible, but not soon, and they end by 2035.**
- [S] IERS Bulletin C 72 (Paris, 6 July 2026): "NO leap second will be introduced at the end of December 2026 … from 2017 January 1, 0h UTC, until further notice : UTC-TAI = -37 s". Leap seconds can still be added "at the end of the months of December or June".
- [S] CGPM 2022, Resolution 4: the CGPM "decides that the maximum value for the difference (UT1-UTC) will be increased in, or before, 2035". So the leap second ends by 2035 at the latest, but until then a step remains possible every six months. The resolution doesn't exclude a negative one.

**The CCSDS time codes separate TAI from UTC.**
- [S] CCSDS 301.0-B-4 (November 2010):
  - Level-1 **CUC**: "The CCSDS-Recommended epoch is that of 1958 January 1 (TAI) … This time code is not UTC-based and leap-second corrections do not apply" (§3.2, p. 3-2).
  - **CDS**: "Since this code is UTC-based, the leap second correction must be made" (p. 3-3).
  - The **ASCII** time code, which Nyx's ISO `t_onboard` resembles, is "UTC-based and leap second corrections must be made", with seconds "00-59 (-58 or -60 during leap seconds)" (p. 3-6).
  - Annex D (p. D-1): "Computation of the difference between two UTC times requires knowledge of any intervening leap seconds in order to achieve a true difference."

**Onboard clocks are correlated on the ground.**
- [S] ECSS-E-ST-70-41C, ST[09] time management (§6.9.1.1, p. 160): "All spacecraft maintain a spacecraft time reference, which can be downlinked in time reports. The ground segment can perform a correlation between the reported spacecraft time and the UTC … used by the ground segment." CUC "is suitable for … spacecraft time measurement"; CDS "is typically used to report on-board time that is synchronized with a ground time reference, e.g. TAI, UTC".

**Nyx's tools can't represent a leap second.**
- [D] Python `datetime` assumes "that every day has exactly 24*60*60 seconds (There is no notion of 'leap seconds' here.)". PostgreSQL: "Technically, PostgreSQL does not use UTC because leap seconds are not handled."
- [T] Python: `datetime.fromisoformat("2016-12-31T23:59:60.500000+00:00")` → `ValueError: second must be in 0..59, not 60`.
- [T] A pydantic 2.14.3 strict model, which is how Nyx parses frames (D-ING-18), raises a **ValidationError** on `"2016-12-31T23:59:60.500000Z"`.
- [T] PostgreSQL 17.11: `'2016-12-31 23:59:60+00'::timestamptz` → `2017-01-01 00:00:00+00`, silently moved to the next second. `'2016-12-31 23:59:60.5+00'` → `ERROR: date/time field value out of range`.

**Clocks handle leap seconds in different ways.**
- [D] A kernel or NTP clock either **repeats** 23:59:59 (step) or **smears** the second. Google's "recommended leap smear standard is a 24-hour linear smear from noon to noon UTC … aligning with practices in AWS and Google Public NTP". So a source and a ground host can differ by up to about 0.5 s during the smear, or disagree on which second was repeated.

## Findings

### F1 – Nyx assumes the onboard clock is UTC; a real bench clock that isn't loses all its data (gap, high for any real bench; dormant with the simulator)

- [C] The simulator stamps `t_onboard` with its host's wall clock (D-SIM-03), on the same machine as ingest. Nyx stores `t_onboard` as UTC and **rejects** frames outside `[now − 7 d, now + 5 min]` (`frame.py:10-11, 41`) to the DLQ with reason `schema`.
- [S][A] Real vehicles keep their own time reference (CUC since 1958 TAI, or an agency epoch), and the ground correlates it with UTC (ST[09]). A bench whose clock:
  - boots unset (epoch zero),
  - runs more than 5 min fast,
  - is a GPS receiver after a week-number rollover,
  - or uses TAI or GPS time without conversion,

  would have **every frame rejected**. The console would show LOSS OF SIGNAL plus FRAMES REJECTED (D-UI-25), and stored telemetry would stop entirely.
- [A] ING-09's latency (`t_received − t_onboard`) and the console's data age include any clock offset directly. A 300 ms offset fails ING-09's 200 ms p95 with no real latency at all.
- **Change, before any bench ICD:**
  1. **ICD fields** (§3.5): the onboard time format (CUC, CDS or ASCII), epoch, time scale (TAI, UTC or GPS), sync source and expected drift.
  2. **Two times per frame:** keep `t_onboard` as **received** (raw vehicle time), and add a ground-derived `t_utc` from one `correlate(vehicle, t_onboard)` function. For the simulator it is the identity, so nothing changes today. For a bench it is UTC_ref + (OBT − OBT_ref)·(1 + drift), from ST[09]-style time reports or from a periodic sync frame.
  3. **The D-ING-18 window checks `t_utc`, not raw time.**
  4. **A `TIME_OFFSET` health signal**, giving the median of `t_received − t_utc` over 60 s, YELLOW above a configured bound (default 100 ms, below ING-09's budget). A bad clock then raises an alarm instead of silently discarding data.
- **Tests:**
  - `test_bench_clock_offset_10min_flags_not_drops`: identity correlation with a 10 min offset gives TIME_OFFSET, and a configured correlation stores the frames;
  - `test_correlation_identity_for_simulator`.

### F2 – A leap second, or any backward clock step, breaks frame parsing or reorders vehicle time (gap, medium; dormant until a leap second or a host clock step)

**(a) A CCSDS-correct `:60` is rejected.**
- [S][T] A source that follows the CCSDS ASCII code or ITU UTC sends `23:59:60.x` during a positive leap second. Nyx's strict pydantic frame model rejects it, so about 10 frames per vehicle at 10 Hz go to the DLQ as a generic schema error, and an INTERFACE_MISMATCH may follow.
- [D] Postgres couldn't store it either, and Python can't represent it.

**(b) A stepped or repeated second is stored but sorted wrongly.**
- [C][A] If a clock repeats 23:59:59, or a laptop host steps its clock back after sleep (NTP correction, a more frequent trigger than leap seconds), `t_onboard` goes back while `seq` goes on:
  - **Live:** the tracker sees `d == 0`, so IN_ORDER (`seq.py`, the classification goes by `seq`). The data-STALE check (`dt > STALE_S`) doesn't fire on a negative `dt`. The storage key stays unique because `seq` differs. **Live is correct.**
  - **Archive, replay and charts:** they read in vehicle-time order. The archive sorts by `(t_onboard, seq)` (`archiver/parquet.py:41-42, 68`) and replay reads each hour through `window_hours`, "sorted (t_onboard, seq)" (`archiver/window.py:77`). So the two copies of the repeated second **interleave**: `(59.0, s100), (59.0, s110), (59.1, s101), (59.1, s111) …`.
    - The replayed values zigzag within that second, and charts and `time_bucket` queries show two values per instant.
    - Replay streams the **recorded** alarm history, so it raises no false events. D-REP-05: "Recomputation is a test, not the replay mechanism".
    - D-REP-05's (b) determinism **test** re-runs `step()` over recorded frames. Fed in `(t_onboard, seq)` order, seq 101 after 110 with `dt > 0` takes the tracker's "behind expected" path (SEQ_RESET or SEQ_ANOMALY), which live never raised. The test would then fail on any recorded run that holds a clock step.
- [A] Durations across the step (latency, session length, the report's expected frame count) are off by 1 s. CCSDS Annex D's warning applies.

**(c) A smeared clock** shifts `t_onboard` by up to about 0.5 s against a non-smearing ground host. ING-09 and the data age show it for up to 24 h.

**Change:**
1. **ICD rule:** Nyx's `t_onboard` is **POSIX UTC** (86 400 s days, no `:60`). This is what Python and Postgres store. A source adapter that receives CCSDS ASCII, CDS or CUC converts with the IERS table (UTC − TAI = −37 s since 2017, a one-line file updated from Bulletin C). A `:60.x` input maps to `23:59:59.999999` with `leap: true` in the frame's ingest metadata, rather than a generic schema DLQ.
2. **Recompute in live order:** D-REP-05 (b), and any future recomputation, iterates by `(epoch, seq)`, the vehicle's own order and the order the live engine saw. The replay stream may do the same per hour, which costs nothing at 36 k rows, so that a stepped second plays back in arrival order. The archive keeps its `(t_onboard, seq)` sort for range reads.
3. **Same leap policy on the ground host and the bench:** both step or both smear (chrony `leapsecmode`), noted in the dev guide and the ICD.

**Tests:**
- `test_leap_second_frame_normalized`: a `23:59:60.5Z` frame is stored as `23:59:59.999999` with the leap flag, not DLQ'd;
- `test_determinism_across_repeated_second`: a recorded run with a 1 s backward step mid-hour still reproduces the recorded transition keys (D-REP-05 b);
- `test_tracker_backward_step_in_order`: pins today's correct live behaviour.

### F3 – Ground clock: monotonic where it matters, wall clock at one check (low)

- [C] The read loop's LOS silence clock and the resume grace use `time.monotonic()` (`worker.py`), which are immune to ground clock steps.
- The D-ING-18 window, `t_received` and the ING-09 latency use wall-clock UTC, so a ground NTP step shows up in latency and in the window.
- **Change:** documentation only. The dev guide states that the host must run NTP/chrony, with the same leap policy as F2 (3). Optionally, `/healthz` reports `degraded` when the clock isn't synchronized (`chronyc tracking` / `timedatectl` status). Skip it for one laptop.

### F4 – GPS and TAI time sources (information)

- [A] A bench that stamps GPS or TAI time differs from UTC by a whole number of seconds that changes only at leap seconds. TAI − UTC = 37 s since 2017 (Bulletin C 72), and GPS time is a fixed offset from TAI, so it stays a few seconds ahead of UTC.
- F1's `correlate()` and F2's IERS table cover it. Without them, a TAI-stamped bench would look 37 s in the future: inside the 5-min window, so accepted, but with every latency 37 s off and ING-09 failed.

## Decision candidate

- **Chosen:**
  - **F1:** the ICD time fields, `t_utc` from `correlate()` with identity for the simulator, the window on `t_utc`, and a TIME_OFFSET alarm. Do it before any bench ICD is frozen.
  - **F2:** the POSIX-UTC ICD rule with leap normalization in the adapter, and recomputation (and optionally the replay stream) in `(epoch, seq)` order.
  - **F3:** documentation only.
- **Because:** F1 turns a silent total loss of a vehicle's telemetry, caused by a wrong clock, into an alarm with the data kept. F2 (2) keeps the determinism test valid after a host clock step, which can happen on any day, not only at a leap second. Both are small: one function, one sort, and their tests.
- **Rejected:**
  - Storing TAI throughout: Postgres, the console and every report work in UTC, and conversion at the edge is simpler.
  - Supporting `:60` end to end: neither Python nor Postgres can represent it.
  - Dropping the D-ING-18 window: it still catches garbage timestamps, once checked on correlated time.

## Open questions → assumptions.md

- A-R26-1: The TIME_OFFSET bound. Default: 100 ms median over 60 s (below ING-09's 200 ms p95).
- A-R26-2: The bench's onboard time format and sync source, an ICD-B item. Unknown until a bench exists.
- A-R26-3: Leap policy for the deployment host. Default: chrony step mode (the Linux default), with the bench matching.

## Sources

- IERS Bulletin C 72 (6 July 2026): https://datacenter.iers.org/data/latestVersion/16_BULLETIN_C16.txt
- CGPM 2022, Resolution 4, On the use and future development of UTC: https://www.bipm.org/en/cgpm-2022/resolution-4
- CCSDS 301.0-B-4, Time Code Formats (November 2010): https://ccsds.org/Pubs/301x0b4e1.pdf. Cited: pp. 3-2, 3-3, 3-6, D-1.
- ECSS-E-ST-70-41C, ST[09] time management, §6.9.1, p. 160 (saved with R24).
- Google, Leap Smear: https://developers.google.com/time/smear
- Python `datetime` documentation: https://docs.python.org/3/library/datetime.html
- PostgreSQL 17, Date/Time Functions and Operators: https://www.postgresql.org/docs/17/functions-datetime.html
- Nyx: `src/gs/common/interfaces/frame.py`, `src/gs/ingest/seq.py`, `src/gs/archiver/parquet.py`, `src/gs/ingest/worker.py`, design D-SIM-02/03, D-ING-13/18.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
