# R4 – Per-vehicle ordering with ≥ 2 workers in one consumer group

**Drives:** GS-ING-04, GS-ING-06, GS-ING-07, GS-ING-08, GS-ALM-02, GS-ALM-03, GS-ALM-04 (and GS-ENG-02 failure analysis)   **Answers review Q:** 8, 9, 11 (and 7 for the Kafka comparison)

## Question

A Redis consumer group gives each entry to whichever consumer asks first. If two workers read the same stream, frames of one vehicle can be processed out of order, and after a crash XAUTOCLAIM moves pending entries to another worker. Gap detection, hysteresis, N-frame persistence and rate-of-change all need frames in `seq` order per vehicle. Which design keeps that order with ≥ 2 workers, through a worker crash and reclaim and through a Redis restart, and can be built and proven in the 25 h budget?

## Findings

**The broker: what Redis does and does not promise**
- A consumer group gives each entry to exactly one consumer: "Each message is served to a different consumer so that it is not possible that the same message will be delivered to multiple consumers." — [Redis Streams docs](https://redis.io/docs/latest/develop/data-types/streams/)
- The Redis docs name the ordering problem and the fix: "If you use 1 stream -> N consumers, you are load balancing to N consumers, however in that case, messages about the same logical item may be consumed out of order"; "If you use N streams with N consumers, so that only a given consumer hits a subset of the N streams, you can scale the above model of 1 stream -> 1 consumer"; "Kafka partitions are more similar to using N different Redis keys". — [Redis Streams, "Differences with Kafka partitions"](https://redis.io/docs/latest/develop/data-types/streams/#differences-with-kafka-tm-partitions)
- The spec already uses one stream per vehicle (`tm:<vehicle>`, §3). So the key is fixed. The open question is only who reads each stream.
- Reclaim is explicit. Without XCLAIM/XAUTOCLAIM, "the server will leave the messages pending forever and assigned to the old consumer". — [Redis Streams, "Recovering from permanent failures"](https://redis.io/docs/latest/develop/data-types/streams/#recovering-from-permanent-failures)
- XAUTOCLAIM scans the PEL from `start` in ID order and takes up to `COUNT` entries (100 by default). It claims only entries idle longer than `min-idle-time`, adds 1 to their delivery count, and drops PEL entries whose stream entry was trimmed. — [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/)
- On restart, a consumer reads its own PEL first ("start by consuming the history") and then switches to `>`. Processing is at-least-once: "we might process a message multiple times". — [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/)
- Consumer-group state (including the PEL) goes to AOF/RDB and is restored after a restart. "AOF must be used with a strong fsync policy if persistence of messages is important." — [Redis Streams, persistence](https://redis.io/docs/latest/develop/data-types/streams/#persistence-replication-and-message-safety). R3 covers the details.
- XACK "removes one or multiple messages from the Pending Entries List (PEL) of a stream consumer group". The docs never say it checks which consumer owns the entry. So a zombie could ack an entry that another worker has claimed (**VERIFIED** by test, redis 8.2, 2026-11-14: `XACK` takes no consumer; after B claimed an entry, an `XACK` issued from A's connection returned 1 and removed it from the PEL. The design's fence is the DB: a stolen owner's commit fails, so it never reaches XACK). — [XACK](https://redis.io/docs/latest/commands/xack/)

**Leases and fencing (prior art)**
- A lease holder can pause (GC, SIGSTOP, swapping) past its TTL and keep writing while it still believes it holds the lock. Fencing tokens solve this: the token goes up on every acquire, and the storage "remembers that it has already processed a write with a higher token number" and rejects lower ones. This needs "the storage server to take an active role in checking tokens." — [Kleppmann, How to do distributed locking (2016)](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html); rebuttal: [antirez, Is Redlock safe?](http://antirez.com/news/101)
- Redis's own lock page agrees. Single-instance `SET key val NX PX ttl` plus compare-and-delete release is fine "where a race condition from time to time is acceptable". For correctness, "You should implement fencing tokens… don't assume that a lock is retained as long as the process that had acquired it is alive". Redis TTL expiry does not use a monotonic clock. With fsync every second, the lock key "is missing" after a crash restart. — [Distributed Locks with Redis](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)
- Leases as a mechanism come from Gray & Cheriton (SOSP 1989), [Leases: an efficient fault-tolerant mechanism for distributed file cache consistency](http://dl.acm.org/citation.cfm?id=74870). The Redis lock page links it.

**Postgres can be the lease and the fence at the same time**
- "Once acquired at session level, an advisory lock is held until explicitly released or the session ends." — [PG explicit locking, advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
- Postgres calls `pg_advisory_unlock_all()` "implicitly … at session end, even if the client disconnects ungracefully"; `pg_try_advisory_lock` returns false right away and never waits. — [PG admin functions, advisory locks](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)
- `pg_locks` shows advisory locks and the pid that holds them. — [pg_locks](https://www.postgresql.org/docs/current/view-pg-locks.html). `pg_terminate_backend(pid)` is "allowed if the calling role is a member of the role whose backend is being terminated", so workers that share one DB role can kill each other's sessions without superuser. — [PG server signaling functions](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADMIN-SIGNAL)
- When the client vanishes without a FIN/RST (host crash, network partition), the server notices only through keepalive or at the next I/O. `tcp_keepalives_idle`/`_interval`/`_count` and `tcp_user_timeout` bound that time. — [PG connection settings](https://www.postgresql.org/docs/current/runtime-config-connection.html). After a SIGKILL the kernel closes the socket, so the session should end within milliseconds (**VERIFIED** for an idle connection: advisory lock gone ≤ 0.07 s after `docker kill -s KILL`, postgres 17, 2026-11-14. Caveat: a client killed **mid-statement** is not noticed until the statement ends; with `pg_sleep(600)` the lock was still held after 3 min. Covered by the steal path, D-ING-12).
- Consequence (my reasoning, checked by T2/T3): if the lock and all of a vehicle's writes use **the same DB session**, no write from the old owner can commit after the lock is released. Session end aborts any open transaction and releases the lock in the same backend exit. The new owner's state load then sees every committed write of the old owner. Kleppmann's fence ends up inside the storage, and no token has to be carried around.

**Kafka (design d)**
- "Kafka only provides a total order over records within a partition… Per-partition ordering combined with the ability to partition data by key is sufficient for most applications." Each consumer instance "is the exclusive consumer of a 'fair share' of partitions"; when an instance dies "its partitions will be distributed to the remaining instances"; "there cannot be more consumer instances in a consumer group than partitions." — [Kafka 2.3 introduction](https://kafka.apache.org/23/getting-started/introduction/)
- Zombies are fenced with an epoch: the broker "increments an epoch associated with the transactional.id", and producers with an older epoch "are fenced off". — [Confluent, Transactions in Apache Kafka](https://www.confluent.io/blog/transactions-apache-kafka/)
- Mapping: partition = `tm:<vehicle>`; group coordinator assignment = our lock + rebalance rule; epoch/generation = our session-bound lock; committed offset = XACK; rebalance redelivery = XAUTOCLAIM. Kafka ships design (a) built in. In both systems, parallelism is capped by the number of keys (partitions or vehicles).

### Design comparison

| | (a1) stream/vehicle + **Redis lease** (SET NX PX + renew + fencing token) | (a2) stream/vehicle + **PG session advisory lock** | (b) group load-shares storage; separate single-owner stateful stage | (c) stateless workers + state in Redis/DB with CAS on `last_seq` | (d) Kafka, key = vehicle |
|---|---|---|---|---|---|
| Order argument | one reader per stream while the lease holds; a zombie is stopped only if every DB write checks the token | one reader per stream; zombie writes cannot commit (same session) | storage is order-free (idempotent insert); the stateful stage still needs single ownership, so (a) comes back | no order: a CAS on `seq-1` fails when frames arrive out of order, so the worker spins or waits | partition = one consumer; generation/epoch fencing |
| Worker crash + reclaim | TTL expiry (e.g. 5 s), new holder takes `INCR` token, XAUTOCLAIMs PEL | session dies → lock free → peer acquires on next poll (~1 s) → XAUTOCLAIM PEL | two takeover paths (storage PEL + stage ownership) | while the crashed worker's frame n waits 30 s for reclaim, frame n+1 cannot commit (CAS fails), so gap and alarm output stall and blow the 300 ms ALM-05 budget | coordinator rebalance |
| Redis restart (ENG-02) | **lease keys can be lost** (fsync every second) or all expire during a 10 s outage → re-race; a token from `INCR` can go **backwards** → the fence must live in the DB anyway | ownership is not in Redis, so nothing changes; workers re-read their own PEL from `0`, then `>` | same as the parts it uses | state in Redis can be lost; state in the DB is the same as (a2) without the order | n/a |
| Postgres restart | writes fail; tokens in the DB survive | all locks dropped; workers reconnect and re-race; each new owner runs the takeover protocol | as (a2) | as (a2) | n/a |
| Hung owner (SIGSTOP/deadlock) | lease expires → takeover; the zombie is stopped by the DB token check | session stays alive → **no automatic takeover**; ING-08 steal path: entry idle > 30 s → `pg_terminate_backend(holder_pid)` | as (a) | n/a | session timeout → rebalance |
| Build cost (25 h) | renew task, TTL tuning, token column + check in every write, release script: ~150 LOC (**STILL UNVERIFIED** estimate) | try-lock poll + rebalance rule + steal: ~60–80 LOC (**STILL UNVERIFIED** estimate) | two pipelines, a second stream or notify channel: high | small code, wrong under reorder: rejected on correctness | new broker; spec fixes Redis Streams in §3/ING-01 |

## Decision candidate

**Chosen: (a2). One stream per vehicle (`tm:<vehicle>`, as in the spec). One consumer group named `ingest`, created on every vehicle stream. Each vehicle is owned by at most one worker at a time through a Postgres session advisory lock `pg_try_advisory_lock(hashtext('tm:'||vehicle))`, taken on the same dedicated connection that writes that vehicle's frames, alarms and gap events.**

Worker loop (the README text for "document how"):
1. Every ~1 s, a worker tries to lock each vehicle it does not own. It stops at its fair share `ceil(V / live_workers)`. `live_workers` = count of `pg_stat_activity` rows with `application_name='ingest'`, so liveness and the lock live in the same place. A worker above its share releases one vehicle after acking its current batch. This rebalance is what makes "2 workers share load" hold when one worker started first.
2. **Takeover protocol (order invariant: pending before new).** (i) Lock acquired. (ii) Load vehicle state from the DB: last stored `seq` for the current boot, open alarms, and the last K frames (K = max(persistence N, ROC window)). Warm the checks by feeding those frames through them; this is the same code path as replay (DAT-06). (iii) Run `XAUTOCLAIM tm:<v> ingest <me> 0 0-0` in a loop until the cursor is `0-0`. min-idle is 0 because the lock already proves the old owner is gone. Process those entries in ID order. Any entry whose delivery count is ≥ 3 goes to the DLQ (ING-08, R3/R5). (iv) Read the own PEL from `0`, then `>`.
3. Per batch, in one DB transaction on the locked connection: idempotent frame insert, gap/alarm events, then COMMIT, **then** XACK (ING-07). Entries with `seq ≤ last_seq` are skipped as already processed (dedup key and seq reset: R5).
4. Any DB connection error means the worker drops all ownership and never writes on a new connection before it locks again.
5. Steal path for ING-08 "idle > 30 s": a worker that sees an entry on a vehicle it does not own idle > 30 s (XPENDING summary) looks up the holder pid in `pg_locks`, calls `pg_terminate_backend`, locks the vehicle and runs the takeover protocol. This covers SIGSTOP and deadlocked owners.

Why: order is the Redis-documented "1 stream → 1 consumer" model. The lease and the fence are one primitive in the system we already run, so there is no TTL to tune, no renew task, and no token to thread through every write. A Redis restart cannot break ownership. The parallelism limit (≤ 1 worker per vehicle) is the same one Kafka has with partitions, and it is honest to state.

Rejected:
- (a1) Redis lease. A Redis restart is a required test (ENG-02), and it can lose or expire every lease and reset an `INCR` token. A correct version needs a DB-side token check anyway, which is (a2) plus a second lock system.
- (b) Split storage/stateful stage. It needs single ownership anyway (for the stateful stage), adds a pipeline, and storage load at 10–100 Hz × 2 vehicles is not the bottleneck (R6).
- (c) Stateless workers + CAS. A worker cannot tell a lost frame from a frame in flight at another worker without waiting, so gap detection needs a timeout and alarms stall whenever frames reorder.
- (d) Kafka. The spec fixes Redis Streams. See the switch triggers below.
- (a0) Static assignment (worker-i owns vehicle-i, `restart: always`): **fallback if time runs out.** Order is trivial, and a killed worker restarts and re-reads its own PEL (ING-07 OK). But no other worker ever reclaims (ING-08 weak), and a dead host halts that vehicle. Same code as (a2) with the try-lock replaced by config.

```mermaid
sequenceDiagram
    participant S as Simulator
    participant R as Redis tm:TA1 (group ingest)
    participant W1 as Worker 1 (owner)
    participant PG as Postgres
    participant W2 as Worker 2
    W1->>PG: pg_try_advisory_lock(TA1) on conn C1 → true
    S->>R: XADD seq 41, 42, 43
    W1->>R: XREADGROUP > → 41, 42
    W1->>PG: BEGIN; insert 41,42; alarms; COMMIT (on C1)
    Note over W1: SIGKILL before XACK
    PG-->>PG: C1 closed → session ends → lock released
    W2->>PG: pg_try_advisory_lock(TA1) → true (next 1 s poll)
    W2->>PG: load last_seq=42, open alarms, last K frames
    W2->>R: XAUTOCLAIM TA1 ingest W2 0 0-0 → 41, 42
    W2->>PG: seq ≤ 42 → skip (already stored)
    W2->>R: XACK 41, 42
    W2->>R: XREADGROUP > → 43 …
    W2->>PG: insert 43 (in order, no false GAP)
```

**Proof by test (integration, real Redis + Postgres; ING-06/07 evidence):**
- T1 `test_two_workers_share_and_keep_order`: 2 workers, 2 vehicles at 100 Hz for 60 s. Assert that each worker owned one vehicle, and that for each vehicle `seq` rises strictly with `ingest_order` (a `bigserial` column; DB rows give the commit order).
- T2 `test_kill_owner_mid_stream` (parametrized crash point: `before_commit`, `after_commit_before_ack`, via a test-only env hook, plus a random `docker kill`). Assert: distinct `seq` count == sent (0 lost), no duplicate rows, 0 `GAP` events, the alarm list equals a no-kill control run of the same scripted leak, `XPENDING` count 0 at the end, and takeover < 3 s (record the number).
- T3 `test_hung_owner_is_fenced`: SIGSTOP the owner, wait > 30 s, expect a steal through `pg_terminate_backend`, then SIGCONT. Assert no rows or alarms written by the old owner after the takeover time, and that its next write fails.
- Negative control: `ORDERING=naive` (both workers XREADGROUP the same stream, no lock) must make T1 **fail**. That shows the test can see reordering, and it is a ready answer to review Q16.

## Risks / what would make us switch

- **Hung-but-connected owner** keeps its lock. Only the steal path (30 s) recovers, so alarm latency during that window misses ALM-05. Record this as a known limitation.
- **Silent client loss** (host crash/partition, not a container kill) is detected only by TCP keepalive. Set `tcp_keepalives_idle=5`, `interval=2`, `count=3` (≈ 11 s; **STILL UNVERIFIED** under Docker networking: not tested) or `tcp_user_timeout`.
- **Zombie side effects outside Postgres**: a frozen old owner that wakes up could XACK, or publish a live UI update for an already-committed frame. XACK after commit is harmless. The UI must ignore updates older than what it shows (handoff R9).
- **Connection pooling** must not be used for the owned connection (PgBouncer in transaction mode breaks session locks: **VERIFIED**, pgbouncer.org feature map "Session-level advisory locks | Yes | Never").
- **Takeover warm-up** depends on checks being pure functions of (initial state, ordered frames). If R8 makes them stateful in other ways, the warm-up must persist that state in the same transaction instead.
- **Parallelism ceiling** = number of vehicles. One hot vehicle cannot use two workers (same as Kafka partitions). Answer for Q11: 10 vehicles × 100 Hz is about 1000 frames/s spread over ≤ 10 workers; the first thing to break is probably DB insert rate, not ordering (R6/R10 to measure).
- **Switch to Kafka (or Redpanda)** when: vehicles ≫ workers and the hand-written rebalance grows beyond one page; several consumer groups need broker-side retention and replay; one Redis cannot hold the rate (Redis docs: a single stream "is not automatically partitioned to multiple instances"); or exactly-once transactions are required. Kafka gives the same per-key order and fencing as built-ins ([Kafka intro](https://kafka.apache.org/23/getting-started/introduction/), [Confluent transactions](https://www.confluent.io/blog/transactions-apache-kafka/)).

## Open questions → assumptions.md

1. Does "one consumer group" (GS-ING-06) accept one group *name* created on each per-vehicle stream? Assume yes, because the spec itself puts frames on `tm:<vehicle>`.
2. Does "2 workers share load" accept vehicle-level sharing (each worker owns ≥ 1 vehicle), as opposed to frame-level sharing on one stream? Assume yes, and say so in the README.
3. Is a takeover gap of ~1–3 s (latency spike, no loss) acceptable during OPS-4? Assume yes, and exclude the takeover window from the ALM-05/ING-09 p95 with a note.
4. Can workers share one DB role so the steal path works without superuser? Assume yes.
5. Handoffs: XACK ownership semantics, delivery count, AOF fsync policy → **R3**. Dedup key, seq reset/boot epoch, "no alarm duplicated" key → **R5**. `ingest_order` column cost on a hypertable → **R6**. Warm-up state for hysteresis/persistence/ROC → **R8**. UI ignoring stale late updates → **R9**.


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| A zombie can XACK an entry another consumer claimed | VERIFIED | redis 8.2 test | D-ING-02, D-ING-12 (fenced by DB) |
| Session ends within ms after client SIGKILL | VERIFIED (idle) / caveat mid-statement | postgres 17 test | D-ING-10, D-ING-12 |
| LOC estimates (lease option) | STILL UNVERIFIED | estimate | none |
| LOC estimates (lock option) | STILL UNVERIFIED | estimate | none |
| Keepalive ≈ 11 s under Docker networking | STILL UNVERIFIED | not tested | D-ING-10 |
| PgBouncer transaction mode breaks session locks | VERIFIED | pgbouncer.org features | D-ING-09 |

CONTRADICTED: none.
