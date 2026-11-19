# R21 – Restart catch-up vs poison isolation: is the one-at-a-time drain the bottleneck?

**Drives:** D-ING-03, D-ING-04, D-ING-24, Gate 3a ruling "Redelivery is read one entry at a time" (`docs/superpowers/plans/2026-11-14-gate3a-ingest-alarms.md:601`), D-PERF-09 (`docs/perf/scaling.md`)   **Asked by:** coordinator, 2026-11-19

Evidence comes from:
- a probe of Redis 8.10.3 in a throwaway container;
- a simulation of the drain policies with those Redis semantics;
- the existing measurements (NCR-01 stall trials, `scaling.md`);
- the vendor documentation cited below (fetched 2026-11-19);
- PaperLab workspace **Nyx ground segment design**. Bronson et al. 2021 is full text. Dorfman 1943 and Hwang 1972 are metadata only (no free PDF), so they are cited for the method, not for page claims.

## Question

The drain claims one entry per pass (`XAUTOCLAIM … COUNT 1`), because every claim adds 1 to `times_delivered` and a count above 3 sends the entry to the DLQ. Claiming in batches would push the healthy neighbours of a frame that kills the worker over the limit. At 10 vehicles × 100 Hz a restarted worker doesn't catch up. How do stream and queue systems drain fast without misclassifying healthy frames as poison, and what does each option cost?

## Findings

### F1 – The drain isn't what limits catch-up; capacity is (evidence, high)

- **The drain only covers the pending list (PEL), and the PEL is small.** A worker reads `>` with `COUNT = 100` and acks a batch after its commit (`worker.py:575`). So the PEL holds at most the one in-flight batch per vehicle per consumer, ≤ 100 entries, plus the same for a dead (ghost) consumer. **The backlog that builds while a worker is down is never-delivered entries, read through `>` in batches of 100**, not through the drain.
- **The measured recovery matches a pure capacity model.** NCR-01 froze the stack for 10 s at 2 × 100 Hz: 2,000 frames of backlog against 200 frames/s of load and 882 frames/s of capacity. The model's catch-up time is backlog / (capacity − load) = 2,000 / 682 = **2.9 s**. Measured: **2.5–3.5 s** over 3 trials (`docs/perf/stall-2026-11-18/summary.md`). That run didn't involve the drain.
- **The 10 × 100 Hz case is a capacity problem.** When one worker carries all 10 vehicles, its load is 1,000 frames/s against a capacity of 882 frames/s, so the backlog grows without bound. Bronson et al. (HotOS 2021, full text) call this the vulnerable state in which a trigger tips the system into a metastable failure, p. 1–2. `scaling.md` already says so. No drain policy changes that: only capacity, or less load per worker, does.
- **What the drain does cost:** at most about 100 passes per vehicle after each crash and each Redis reconnect (D-ING-03, D-ING-08). Each pass costs:
  - a heartbeat `SET`, the fence `SELECT 1`, `XAUTOCLAIM`, the pending-ACK query and `XPENDING`;
  - a one-frame transaction and its commit, `XACK`, and a publish.

  The per-pass time hasn't been measured. **Measure it before deciding** (Evaluation, below).

### F2 – The options, with Redis semantics checked

**Probe, Redis 8.10.3**, five entries in one group:

| Command | `times_delivered` after |
| --- | --- |
| `XREADGROUP >` | 1 |
| `XPENDING` (read-only) | 1 (unchanged) |
| `XAUTOCLAIM … COUNT 5 JUSTID` | 1 (unchanged, owner moved) |
| `XAUTOCLAIM … COUNT 5` | 2 (all five) |
| `XAUTOCLAIM … COUNT 2` | 3, 3, 2, 2, 2 |
| `XREADGROUP … 0` (own PEL) | +1 on every entry it returns |

The Redis docs agree: claiming "increments the attempted deliveries count for that message, unless the JUSTID option has been specified"; JUSTID "do[es] not increment their retry counter" (redis.io, XAUTOCLAIM).

**Simulation** ([`scripts/r21_drain_sim.py`](scripts/r21_drain_sim.py); easy to port as a unit test; the probe is [`scripts/r21_redis_count_probe.sh`](scripts/r21_redis_count_probe.sh)). The model:
- 500 entries per case, `COUNT = 100`, killers (frames that kill the process) at fixed positions plus 120 random sets of 2, 3 and 5;
- one transaction per batch, so a killer aborts the whole batch;
- the D-ING-04 rule (> 3 → DLQ), and a restart then drains the PEL first.

| Policy | Worst crashes | Worst healthy frames DLQ'd | Killers missed | Drain passes, clean 100-entry PEL |
| --- | --- | --- | --- | --- |
| Single, current (`COUNT 1`) | 15 (3 per killer) | **0** | 0 | 100 |
| Batch claim (`COUNT 100`) | 15 | **495** | 0 | 1 |
| **Adaptive** (below) | 15 | **0** | 0 | **1** |

Options compared:

| Option | What it is | Precedent | Verdict for Nyx |
| --- | --- | --- | --- |
| A. Batch claim | `XAUTOCLAIM COUNT 100`, one transaction | – | **Rejected:** up to 495 of 500 healthy frames DLQ'd in the simulation |
| B. `JUSTID` + `XRANGE` | Claim without raising the count, then fetch the bodies | – | **Unsafe alone.** The count never grows during the drain, so a frame that kills the worker crash-loops it forever. The count is the only thing that detects such a frame (D-ING-04) |
| C. Batch bisection | Split a failed batch in halves until the bad record is isolated | AWS Lambda, Kinesis/DynamoDB streams: `BisectBatchOnFunctionError` "splits a failed batch into two smaller batches, isolating bad records … **Splitting batches doesn't consume the retry quota**" | The quota is kept **outside the record**. With Redis counts, each split level adds a delivery to every entry in the half that keeps failing. Isolating one killer in 100 takes about log₂ 100 ≈ 7 levels, more than the 3-delivery budget, so it needs option D. Group-testing theory puts adaptive splitting at about d·log₂(n/d) tests for d bad items (Hwang 1972; Dorfman 1943, metadata). **Here every test is a process crash and restart.** |
| D. External attempt counter | Per-entry attempts in Postgres or a Redis hash, written before processing | AWS SQS + Lambda partial batch response `ReportBatchItemFailures`: "only the failed messages visible again … reduce the number of unnecessary retries" | Exact, but it's the second source of truth that D-ING-04 rejected (R15). It also adds a write per batch |
| E. Per-record counter in the broker | – | Kafka share groups (KIP-932): `group.share.delivery.count.limit`, "maximum number of delivery attempts for a record … Default 5, minimum 2, maximum 10" | The same model as Redis `times_delivered`. It confirms the per-record counter design and gives no new mechanism |
| **F. Adaptive drain (new)** | Before claiming, read the next window's counts with `XPENDING` (read-only). Batch-claim the window while every entry has `times_delivered ≤ 1`; otherwise claim one entry | – | **0 collateral, the same 3 crashes per killer, and 1 pass instead of 100 when no killer is present** (OOM kill, host restart, every Redis reconnect). It keeps the one counter |

Why F is safe: after the first crash every entry of the in-flight batch, the killer included, has count 1. The batch claim makes it 2. If the killer crashes the worker again, every entry now shows count 2, and the drain falls back to one at a time. The killer is DLQ'd at its 4th delivery, as today. The healthy entries behind it reach count 3 at most, and the rule only fires above 3. Crash evidence still drives isolation. F only skips the slow path when there is no evidence of a crash.

### F3 – Retry budgets don't address this

The SRE "retry budget" idea caps the load that retries add. It doesn't isolate a poison record, so it doesn't fit this question. The restart policy and the D-ING-24 controlled exit already bound the crash-loop rate.

## Evaluation (how to decide D-ING-03 with data)

1. **Measure the drain:** one system test that leaves 100 pending entries on each of 5 vehicles under a ghost consumer, starts a worker, and records the time until the PELs are empty (`wait_drained`), plus the time per pass. Compare it with the catch-up time of the backlog through `>`, `backlog / (capacity − load)`.
   - If the drain is under about 5 % of the catch-up time, **D-ING-03 doesn't need to change for catch-up**. Recommendation: keep it, and spend the effort on capacity (`scaling.md` §First bottleneck).
2. **If F is adopted:**
   - port the simulation as `unit/test_drain_policy.py`: 0 collateral, every killer DLQ'd after 3 crashes, over random killer sets;
   - keep `test_poison_neighbours_not_dlqd` green;
   - add `test_clean_pel_drains_in_one_pass` (100 pending entries, count 1, no killer, so one claim).
   - The code change is one `XPENDING` range call and one condition in the drain branch of `_own` (`worker.py:565`).
3. **Report catch-up as a number in the ATP:** time to lag < 1 s after a given outage, with load and capacity stated. That makes the metastable boundary visible (Bronson 2021) rather than discovered.

## Decision candidate

- **Chosen:**
  - **Keep D-ING-03 for now.** The drain is bounded at ~100 entries per vehicle, and catch-up is bound by capacity, as NCR-01's data fits.
  - **Measure the drain** (Evaluation 1).
  - If it turns out to matter, or as a cheap improvement for Redis reconnects, **adopt F**. It needs no new state and no second counter.
- **Rejected:**
  - Batch claim (collateral DLQ).
  - JUSTID drain (infinite crash loop).
  - Bisection with Redis counts (exceeds the 3-delivery budget).
  - An external counter (second source of truth, D-ING-04).
- **For the owner:** the 10 × 100 Hz goal needs more capacity per worker or more workers, whatever the drain does.

## Sources

- Redis, XAUTOCLAIM command reference: https://redis.io/docs/latest/commands/xautoclaim/
- AWS Lambda, Kinesis error handling and `BisectBatchOnFunctionError`: https://docs.aws.amazon.com/lambda/latest/dg/kinesis-on-failure-destination.html
- AWS Lambda, SQS partial batch response: https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html
- Apache Kafka KIP-932, Queues for Kafka: https://cwiki.apache.org/confluence/display/KAFKA/KIP-932%3A+Queues+for+Kafka
- Bronson, Aghayev, Charapko & Zhu 2021, Metastable failures in distributed systems, HotOS, 10.1145/3458336.3465286 (full text)
- Huang et al. 2022, Metastable Failures in the Wild (OSDI; already in the workspace)
- Dorfman 1943, The Detection of Defective Members of Large Populations, 10.1214/aoms/1177731363 (metadata)
- Hwang 1972, A Method for Detecting all Defective Members in a Population by Group Testing, JASA, 10.1080/01621459.1972.10481257 (metadata)
- Nyx: `docs/perf/scaling.md`, `docs/perf/stall-2026-11-18/summary.md`, `src/gs/ingest/worker.py`

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
