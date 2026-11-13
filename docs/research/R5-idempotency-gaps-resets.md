# R5 – Idempotency, dedup, gap counting and sequence resets

**Drives:** GS-ING-03, GS-ING-04, GS-ING-05, GS-ING-07, GS-ALM-06 (no duplicate transitions), OPS-3, OPS-4   **Answers review Q:** 5, 8

Handoffs: Redis delivery / XACK / XAUTOCLAIM details → R3. Who owns a vehicle when 2 workers run, and what happens while two workers briefly hold the same vehicle → R4. Hypertable vs plain Postgres → R6. Alarm state machine content → R8. Replay must not write into live tables → R7.

## Question

1. What uniquely identifies a frame when `seq` (uint32) resets to 0 on simulator restart? Do we need a boot/session epoch, and should the frame get a `boot_id`?
2. How do we tell duplicate, reset, `seq_jump`, out-of-order and uint32 wraparound apart?
3. How do we count lost frames (`dropout 10 s` → one `GAP` with 100 ± 1) without raising the GAP twice after a crash and redelivery?
4. How do we make storage idempotent on a TimescaleDB hypertable, in batches?
5. How do we make sure a redelivered frame raises no second alarm (OPS-4)?
6. What general principle backs all this?

## Findings

**Sequence counters in real systems**
- CCSDS Space Packet: the Packet Sequence Count is 14 bits (bits 18–31 of the primary header), counted per APID, and "shall be continuous (modulo-16384). A re-setting … shall not take place unless it is unavoidable" — [CCSDS 133.0-B-2 §4.1.3.4.3](https://ccsds.org/Pubs/133x0b2e2.pdf)
- Its stated purpose is ordering and "detection of missing packets"; a gap may be signalled to the user as a Packet Loss Indicator, "as evidenced by a discontinuity in the Packet Sequence Count" — [CCSDS 133.0-B-2 §4.1.3.4.3 note 1, §3.3.2.3](https://ccsds.org/Pubs/133x0b2e2.pdf)
- "If the Packet Sequence Count is reset because of an unavoidable reinitialization of a process, the completeness of a sequence of Packets cannot be determined." The standard's answer is to pair the count with a time code "to provide unambiguous ordering during a long operational time period" — [CCSDS 133.0-B-2 §4.1.3.4.3 notes 2–3](https://ccsds.org/Pubs/133x0b2e2.pdf). This is the (counter + on-board time) key we choose below.
- PUS-C TM packets carry on-board time in the secondary header next to the counter: every TM (except time reports) has a secondary header with a 16-bit message type counter and an absolute time field; the primary-header count wraps at 2^14 — [ECSS-E-ST-70-41C §7.4.3.1, Fig. 7-7](https://ecss.nl/wp-content/uploads/2016/06/ECSS-E-ST-70-41C15April2016.pdf) (verified in R2).
- Flight software reports reboots on its own: NASA cFE Executive Services housekeeping carries `ResetType` and `ProcessorResets` ("Number of processor resets since last power on") — [cFE `default_cfe_es_msgstruct.h`](https://github.com/nasa/cFE/blob/main/modules/es/config/default_cfe_es_msgstruct.h). A boot count is the real-world way to tell an epoch; our frame has none.
- Kafka's idempotent producer works the same way: a producer ID plus a per-partition sequence that "start[s] from zero and [is] monotonically increasing"; a lower sequence is a duplicate, a higher one is "out-of-sequence" (loss), and a producer *epoch* fences an old instance after restart — [KIP-98](https://cwiki.apache.org/confluence/display/KAFKA/KIP-98+-+Exactly+Once+Delivery+and+Transactional+Messaging)

**Classifying sequence numbers (prior art: RTP)**
- RTP receivers keep `max_seq`, `cycles` (wrap count), `base_seq`; a seq less than `MAX_DROPOUT` (3000) ahead is in order with a gap; one that is ahead modulo 2^16 but numerically smaller "has wrapped around"; a very large jump is accepted only if the next packet follows it ("assume that the other side restarted … just re-sync"); anything just behind is a "duplicate or reordered packet" — [RFC 3550 App. A.1](https://www.rfc-editor.org/rfc/rfc3550#appendix-A.1)
- Loss is `expected = extended_max − base_seq + 1; lost = expected − received`, and stats are reset on re-sync "since multiple complete sequence number cycles may have been missed" — [RFC 3550 App. A.3, A.1](https://www.rfc-editor.org/rfc/rfc3550#appendix-A.3)
- Wrap-safe compare: serial number arithmetic treats `s2 > s1` when the forward distance is less than half the space; values exactly half apart are undefined — [RFC 1982 §3.2](https://www.rfc-editor.org/rfc/rfc1982#section-3.2)
- uint32 at 10 Hz wraps after 2^32 / 10 s ≈ 13.6 years, at 100 Hz ≈ 1.36 years (arithmetic). It will not happen in a test, but a unit test is cheap and a real CCSDS decoder (14 bit) wraps every 27 min at 10 Hz.

**Idempotent storage in Postgres / TimescaleDB**
- `ON CONFLICT DO NOTHING` needs no conflict target ("conflicts with all usable constraints … are handled"); `RETURNING` gives back "only rows that were successfully inserted" — [PostgreSQL INSERT](https://www.postgresql.org/docs/current/sql-insert.html). So the insert result tells us which frames were new.
- Timescale: "When you create a unique index, it must contain all the partitioning columns … You cannot create a unique index without `time`" — [Tiger Data docs, hypertables and unique indexes](https://www.tigerdata.com/docs/use-timescale/latest/hypertables/hypertables-and-unique-indexes)
- Upserts on hypertables use plain `INSERT … ON CONFLICT DO NOTHING`, "unique constraints on a hypertable must include the time column", same for rowstore and columnstore — [Tiger Data docs, upsert](https://www.tigerdata.com/docs/use-timescale/latest/write-data/upsert)
- `COPY` has no `ON CONFLICT`; by default "COPY will fail if it encounters an error"; `ON_ERROR ignore` (new in PG 17) covers only errors "converting a column's input value into its data type", not unique violations — [PostgreSQL COPY](https://www.postgresql.org/docs/current/sql-copy.html), [PG 17 release notes](https://www.postgresql.org/docs/release/17.0/). For COPY you load a temp staging table, then run `INSERT … SELECT … ON CONFLICT DO NOTHING` (standard pattern, follows from the two docs).
- psycopg ≥ 3.1 `executemany()` uses pipeline mode internally (one round trip for many statements) — [psycopg pipeline](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html); `executemany(..., returning=True)` keeps each statement's `RETURNING` rows — [psycopg cursor API](https://www.psycopg.org/psycopg3/docs/api/cursors.html). At 20–200 frames/s this is plenty; COPY is not needed (R6/R10 measure).

**At-least-once + idempotent consumer = effectively-once**
- MillWheel (Google, VLDB 2013): "Deliveries … are retried until they are ACKed in order to meet our at-least-once requirement, which is a prerequisite for exactly-once"; duplicates are found "by including this unique ID for the record in the same atomic write as the state modification", then discarded and ACKed — [Akidau et al. 2013 §6.1.1](https://www.cl.cam.ac.uk/~ey204/teaching/ACS/R212_2015_2016/papers/akidau_vldb_2013.pdf)
- Idempotent Consumer pattern: "record the IDs of processed messages in the database", either in a separate table or in the business rows themselves — [microservices.io](https://microservices.io/patterns/communication-style/idempotent-consumer.html)
- Exactly-once *delivery* is impossible (Two Generals / FLP); queues offer at-least-once and you "simulate exactly-once semantics by ensuring idempotency" — [Treat, "You Cannot Have Exactly-Once Delivery"](https://bravenewgeek.com/you-cannot-have-exactly-once-delivery/)
- The term exactly-once "is not formally defined" in stream processing literature — [Trofimov et al. 2019, arXiv:1907.06250](https://arxiv.org/abs/1907.06250). So in docs we say *effectively-once* and define it: every frame stored once, every event raised once.

## Decision candidate

**Chosen: three rules, all in one DB transaction per batch, XACK after commit.**

1. **Storage key = `(vehicle, t_onboard, seq)`**, unique index on the telemetry hypertable, insert with `ON CONFLICT DO NOTHING RETURNING seq, t_onboard`. It is what Timescale allows (time column inside), it is what CCSDS note 3 recommends (count + time), and it needs no derived state: a wrong classification can raise a wrong *event* but can never drop or double a *frame*.
2. **Epoch = `t_onboard` of the first frame after a detected reset** (first frame ever seen counts too). Derived from data only, so any worker, a restarted worker or a replay computes the same value. Stored on every event row and in a one-row-per-vehicle `seq_state(vehicle PK, epoch, last_seq, last_t, period_s)` table that is updated in the same transaction as the frames.
3. **Events and alarm transitions have deterministic unique keys** and are inserted with `ON CONFLICT DO NOTHING`:
   - `GAP`/`SEQ_JUMP`: `(vehicle, epoch, from_seq)`; `SEQ_RESET`: `(vehicle, epoch)`.
   - Frame-triggered alarm transitions (RAISE, ESCALATE, CLEAR): `(vehicle, param, epoch, trigger_seq, transition)`. Operator ACK: client-generated UUID as key (the request is idempotent, not the frame).
   - Only frames that the tracker sees as new (not `DUP_OR_LATE`) go to the alarm engine, so a replayed frame re-raises nothing. The unique key is the second guard for the R4 window where two workers handle the same frame.

Why this survives a crash: crash **before** commit → nothing written, frame redelivered, processed fresh, events raised once. Crash **after** commit, before XACK → redelivered frames conflict on insert, `seq_state` has already moved, so the tracker says `DUP_OR_LATE`, no event, then XACK. This is MillWheel's "unique ID in the same atomic write as the state modification".

**`boot_id` in the frame: not now.** §3 says swapping the simulator for bench hardware must not change anything downstream, so ground cannot rely on a field the frame does not define. Log it as an assumption and proposal: "a real vehicle should send a reset counter (cf. cFE `ProcessorResets`); if it does, the tracker uses it as epoch instead of the heuristic". That is a 3-line change in `step()`.

**Classification table** (per vehicle; `exp = last_seq + 1`; `d = (seq − exp) mod 2^32`; `behind = d ≥ 2^31`; `dt = t_onboard − last_t`; `by_time = round(dt / period) − 1`)

| # | Condition | Class | Store? | Tracker | Event |
| --- | --- | --- | --- | --- | --- |
| 1 | no state for vehicle | FIRST | yes | epoch = t | – |
| 2 | `d == 0` | IN_ORDER (incl. 2^32−1 → 0 wrap) | yes | advance | – |
| 3 | `0 < d < 2^31` and `abs(d − by_time) ≤ max(2, d/10)` | GAP (dropout) | yes | advance | `GAP lost=d` (+ `lost_by_time`) |
| 4 | `0 < d < 2^31`, time does not match | SEQ_JUMP (counter jumped, clock did not) | yes | advance | `SEQ_JUMP lost=d, lost_by_time` |
| 5 | `behind` and `dt ≤ 0` | DUP_OR_LATE | insert decides: 0 rows = DUPLICATE, 1 row = LATE | unchanged | counter only; no alarm eval |
| 6 | `behind`, `dt > 0`, `seq ≤ RESET_SEQ_MAX` (50) | SEQ_RESET | yes | new epoch = t | `SEQ_RESET`; if `seq > 0` also `GAP(epoch_new, from 0, lost=seq)` |
| 7 | `behind`, `dt > 0`, `seq > RESET_SEQ_MAX` | SEQ_ANOMALY (backward jump) | yes | new epoch = t | `SEQ_ANOMALY` (operator sees it) |

Thresholds: `RESET_SEQ_MAX = 50` because the simulator restarts at 0 and must resume within 2 s (ING-05), so the first seq seen after a restart is ≤ 20 at 10 Hz. Gap tolerance `max(2, 10 %)` absorbs scheduler jitter in the period estimate; `period` is an EWMA of in-order `dt` on the *vehicle* clock (no ground network jitter), starting from config (0.1 s) and adapting to 100 Hz for ING-10. Unlike RTP we use `t_onboard`, not a fixed `MAX_MISORDER`, to split duplicate from reset: RTP's window would call a restart after 3 s (last_seq 30) a duplicate run. ING-04's "100 ± 1" is checked on `lost` (seq based, exact); `lost_by_time` is the cross-check.

**Per-vehicle tracker** (pure function; checked in scratch, all 10 cases pass)

```python
MOD, HALF, RESET_SEQ_MAX = 2**32, 2**31, 50

def step(s: State | None, seq: int, t: datetime) -> tuple[State | None, list[Event]]:
    if s is None:
        return State(epoch=t, last_seq=seq, last_t=t, period=NOMINAL_PERIOD), []
    d = (seq - s.last_seq - 1) % MOD             # forward distance, wrap-safe (RFC 1982)
    dt = (t - s.last_t).total_seconds()
    if d >= HALF:                                # behind expected
        if dt <= 0:
            return s, [Event("DUP_OR_LATE", s.epoch, seq)]
        kind = "SEQ_RESET" if seq <= RESET_SEQ_MAX else "SEQ_ANOMALY"
        ev = [Event(kind, t, from_seq=s.last_seq)]
        if kind == "SEQ_RESET" and seq > 0:      # frames 0..seq-1 of the new boot never arrived
            ev.append(Event("GAP", t, from_seq=0, lost=seq, lost_by_time=seq))
        return State(t, seq, t, s.period), ev
    period = 0.9 * s.period + 0.1 * dt if d == 0 and dt > 0 else s.period
    ns = replace(s, last_seq=seq, last_t=t, period=period)
    if d == 0:
        return ns, []
    by_time = max(round(dt / s.period) - 1, 0)
    kind = "GAP" if abs(d - by_time) <= max(2, d // 10) else "SEQ_JUMP"
    return ns, [Event(kind, s.epoch, (s.last_seq + 1) % MOD, d, by_time)]
```

Worker loop per batch (one vehicle): `BEGIN; SELECT … FROM seq_state WHERE vehicle=$1 FOR UPDATE; fold step() over frames; INSERT frames ON CONFLICT DO NOTHING RETURNING; INSERT events ON CONFLICT DO NOTHING; alarm engine on new frames → INSERT transitions ON CONFLICT DO NOTHING; UPDATE seq_state; COMMIT; XACK`. The `FOR UPDATE` row lock also serialises two workers on one vehicle (input for R4).

**Test cases the tracker must pass** (all at 10 Hz unless stated)

| # | Input | Expected |
| --- | --- | --- |
| T1 | 100 nominal frames | no events |
| T2 | seq 0–49, then 150–199 after 10 s silence (dropout, on-board counter keeps running) | one `GAP from_seq=50 lost=100`, `lost_by_time` within ±1 |
| T3 | frame 4 sent twice (`duplicate`) | one `DUP_OR_LATE`, stored once (DB), no GAP |
| T4 | after T2 is committed, redeliver frames 145–150 | only `DUP_OR_LATE`, no second GAP (ING-07 + OPS-4) |
| T5 | seq 9 → 1010 with 0.1 s step (`seq_jump`) | `SEQ_JUMP lost=1000, lost_by_time=0`, not GAP |
| T6 | 31 frames, restart, seq 0.. 1.5 s later | `SEQ_RESET`, new epoch = t of new seq 0; no DUP (RTP window would fail this) |
| T7 | 500 frames, restart, first seen seq = 3 | `SEQ_RESET` + `GAP(new epoch, from 0, lost 3)` |
| T8 | seq 2^32−2, 2^32−1, 0, 1 | no events; 2^32−2, 2^32−1, 2 (0.3 s step) → `GAP from 0 lost 2` |
| T9 | same frame list run twice from empty state | identical events (replay / reclaim determinism) |

Integration (real Postgres): insert the same 100 frames twice → `count(*) = 100`; kill worker between COMMIT and XACK → after reclaim, frames = N, GAP events = 1, alarm transitions unchanged.

**Rejected**
- `(vehicle, seq)` as key: after a reset, new seq 0..k collide with the old boot's rows and are silently dropped by `ON CONFLICT` → data loss on every simulator restart. Also not allowed as a unique index on a hypertable (no time column).
- `(vehicle, epoch, seq)` as the *storage* key: needs `t_onboard` added anyway for Timescale, and a misdetected epoch would then drop or double frames. Epoch stays an event attribute, not part of frame identity.
- Worker-local epoch counter (1, 2, 3 …): not deterministic; a restarted worker or a replay numbers boots differently, so event keys differ and GAPs double.
- RTP-style "accept a big jump only after 2 consecutive frames": adds buffering and 1 frame delay; `t_onboard` already tells reset from duplicate. Revisit if real hardware clocks reset on reboot.
- COPY for ingest: no conflict handling, needs a staging table; executemany in pipeline mode is enough at our rates.
- Dedup in Redis/in memory (seen-set): lost on crash, and the DB constraint is needed anyway.

## Risks / what would make us switch

- **On-board clock resets on reboot** (real hardware may start at epoch 0 / 1970): rows 5–7 rely on `t_onboard` moving forward. Symptom: a reset classified `DUP_OR_LATE` while the insert returns 1 row. Mitigation: treat "DUP_OR_LATE but inserted" as `SEQ_RESET` with a `CLOCK_ANOMALY` flag; switch to a real `boot_id`/reset counter when the vehicle provides one.
- **Simulator `duplicate` re-stamps `t_onboard`**: then the storage key sees two rows, and the tracker may even call it a reset if seq ≤ 50. We own the simulator: define `duplicate` = byte-identical resend (assumption).
- **Simulator `dropout` stops the counter** instead of dropping frames: seq gap would be 0 and only `lost_by_time` sees the loss. Define dropout as "frames generated and discarded", which models the bench link loss in OPS-3 (assumption).
- **Backward `seq_jump`** is indistinguishable from a reset (row 7 catches only seq > 50).
- **Out-of-order across workers** (R4): LATE frames are stored but skipped by the alarm engine; if R4 cannot guarantee per-vehicle order, persistence/rate checks need a reorder buffer.
- **Two GAPs instead of one** if frames arrive during dropout (partial link): correct behaviour, but ING-04 expects "one GAP" for a clean dropout; test fixture must be a clean silence.
- **Alarm engine state** (persistence counters, hysteresis) is lost on a worker crash unless stored with `seq_state` or rebuilt by re-feeding the last N stored frames; otherwise an alarm can be delayed by up to N frames after reclaim (R8/R4).

## Open questions → assumptions.md

1. A-R5-1: `duplicate` fault resends the byte-identical frame (same `seq`, same `t_onboard`).
2. A-R5-2: `dropout N` keeps the on-board seq counter running and drops the frames (link loss), so lost = N × rate exactly.
3. A-R5-3: simulator restart sets `seq = 0` and keeps `t_onboard` monotonic (wall-clock UTC).
4. A-R5-4: expected outcome of `seq_jump` is not stated in ATP-05. Proposal: `SEQ_JUMP` event with both counts, not a GAP, no frame lost. Confirm with "customer".
5. A-R5-5: frame format stays as §3 (no `boot_id`); proposal for a reset counter recorded as a future interface change.
6. Report (DAT-02, Q5): "lost frames" in a session = sum of `GAP.lost` over all epochs in the session; `SEQ_JUMP` listed separately. Does the conductor want SEQ_RESET listed as a gap of unknown size (CCSDS note 2: completeness "cannot be determined")?
7. Replay (R7): replayed frames get the same deterministic keys, so replay output must go to a separate table/namespace or be in-memory only.
