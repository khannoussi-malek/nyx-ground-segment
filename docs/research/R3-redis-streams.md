# R3 – Redis Streams delivery semantics

**Drives:** GS-ING-01, GS-ING-02 (DLQ), GS-ING-06, GS-ING-07, GS-ING-08, GS-ING-10, GS-ENG-02   **Answers review Q:** 7, 8, 10, 11

Handoffs: per-vehicle ordering across workers → **R4**. Dedup key / idempotent insert → **R5**. Store choice → **R6**. Latency measurement → **R10**.

## Question

What exactly does a Redis Streams consumer group guarantee, and which commands, settings and worker-loop steps give us
"ack only after stored" (ING-07), "reclaim after 30 s idle, poison after 3 deliveries → DLQ" (ING-08) and
"stop Redis 10 s, everything recovers" (ENG-02)? Where can a frame be lost or processed twice, and when would Kafka or NATS JetStream be the better broker?

Versions checked (2026-11-13): Redis **8.10.3** is the latest stable release (8.12 is at rc3) ([releases](https://github.com/redis/redis/releases)). redis-py **8.1.0** ([docs](https://redis.readthedocs.io/en/stable/_modules/redis/asyncio/client.html)).
"Local probe" below means I ran the commands myself against `redis:8.10.3` in Docker with redis-py 8.1.0 on macOS. These are throwaway checks: the scripts are not in the repo.

## Findings

### 1. Consumer groups, PEL, ack, claim

- `XREADGROUP ... STREAMS key >` returns only entries never delivered to any consumer. Any other ID (e.g. `0`) returns **this consumer's own pending entries** (delivered, not acked), and `BLOCK`/`NOACK`/`CLAIM` are ignored in that mode — [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/)
- Crash recovery recipe from the docs: on start, read with ID `0` until the reply is empty, then switch to `>` — [XREADGROUP › usage example](https://redis.io/docs/latest/commands/xreadgroup/)
- Every delivery adds the entry to the group's Pending Entries List (PEL). `XACK` removes it. `NOACK` skips the PEL, so it is "equivalent to acknowledging the message when it is read" — [XREADGROUP](https://redis.io/docs/latest/commands/xreadgroup/)
- `XREADGROUP` is a write command, because it changes group state — [streams intro](https://redis.io/docs/latest/develop/data-types/streams/)
- Group state is kept per **consumer name**: "even after a disconnect, the stream consumer group retains all the state, since the client will claim again to be the same consumer". So consumer names must be stable across restarts — [streams intro](https://redis.io/docs/latest/develop/data-types/streams/)
- `XPENDING key group [IDLE ms] start end count [consumer]` returns, for each entry, its ID, owner, ms since last delivery and **delivery count**. The count goes up on `XCLAIM` and on re-reading the consumer's own history with `XREADGROUP` — [XPENDING](https://redis.io/docs/latest/commands/xpending/), [streams intro › delivery counter](https://redis.io/docs/latest/develop/data-types/streams/)
- `XAUTOCLAIM key group consumer min-idle-time start [COUNT n] [JUSTID]` (Redis 6.2+) works like XPENDING + XCLAIM with a cursor:
  - `COUNT` defaults to 100, and one call scans at most 10×COUNT PEL entries.
  - Claiming resets the idle time, so only one consumer can win a given entry.
  - It **increments the delivery count** unless `JUSTID` is used.
  - Since 7.0 it returns `[next_cursor, claimed_entries, deleted_ids]`. PEL entries whose stream entry was trimmed or `XDEL`'d are dropped from the PEL and listed in `deleted_ids`.
  - Cursor `0-0` means the scan is complete.

  Source: [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/)
- `XAUTOCLAIM` does **not** return the delivery count. To get it, read `XPENDING` for the claimed IDs. Local probe: after one claim, `times_delivered` = 2 — [XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/), local probe
- Since 7.0, `XCLAIM` also skips entries that were deleted from the stream and removes them from the PEL — [XCLAIM](https://redis.io/docs/latest/commands/xclaim/)
- Dead letters are a pattern, not a feature: "once the deliveries counter reaches a given large number that you chose, it is probably wiser to put such messages in another stream" — [streams intro › claiming and the delivery counter](https://redis.io/docs/latest/develop/data-types/streams/)
- If a PEL entry's stream data was deleted, reading the PEL with `0` returns `(id, nil)`. Trimming with the default `KEEPREF` does **not** protect pending entries — [XREADGROUP › pending message deleted](https://redis.io/docs/latest/commands/xreadgroup/)
- Newer helpers, available but not needed:
  - `XREADGROUP ... CLAIM min-idle-time` (8.4) claims idle entries inside the read and returns their delivery count with each entry — [XREADGROUP › CLAIM](https://redis.io/docs/latest/commands/xreadgroup/)
  - `XNACK` (8.8) gives pending entries back to the group — [streams intro › XNACK](https://redis.io/docs/latest/develop/data-types/streams/)
  - redis-py 8.1 exposes `claim_min_idle_time`, `xnack` and `xackdel` — [redis-py core.py v8.1.0](https://github.com/redis/redis-py/blob/v8.1.0/redis/commands/core.py)
- `XGROUP CREATE key group <id|$> MKSTREAM` returns `-BUSYGROUP` if the group already exists. Use ID `0` to also consume entries written before the group existed — [XGROUP CREATE](https://redis.io/docs/latest/commands/xgroup-create/). Local probe: `BUSYGROUP Consumer Group name already exists`.
- Within one stream, `1 stream → N consumers` load-balances but "messages about the same logical item may be consumed out of order". This is R4's problem — [streams intro › differences with Kafka partitions](https://redis.io/docs/latest/develop/data-types/streams/)

### 2. Trimming

- `MAXLEN n` keeps the newest n entries. `MINID id` drops entries with lower IDs; since IDs are ms timestamps, this is trimming by time.
  - `~` trims whole macro nodes only, so a few tens of extra entries can stay.
  - `LIMIT` defaults to 100 × entries-per-node.

  Source: [XTRIM](https://redis.io/docs/latest/commands/xtrim/), [XADD](https://redis.io/docs/latest/commands/xadd/)
- **Risk:** the default trim mode, `KEEPREF`, deletes entries "regardless of whether they are referenced by any consumer groups". The PEL then keeps IDs that no longer have data, so an unprocessed frame is silently lost. `DELREF` also cleans the PEL. **`ACKED` (8.2+) only removes entries "read and acknowledged by all consumer groups"** — [XADD](https://redis.io/docs/latest/commands/xadd/), [XTRIM](https://redis.io/docs/latest/commands/xtrim/)
- Local probe, `XADD ... ACKED MAXLEN = 10` on a stream of 1000 entries (500 delivered but un-acked, 500 never delivered):
  - Before any ack, the length stayed at **1001**.
  - After acking the 500 delivered entries, it went to **502**: the never-delivered entries were kept too.
  - Plain `MAXLEN = 10` (KEEPREF) cut it to **10**.

  `ACKED` therefore protects both un-acked and never-delivered entries. **VERIFIED** for `~` (redis 8.2, 2026-11-14): with 200 acked, 50 pending and 150 never-delivered entries, `XTRIM MAXLEN ~ 10 ACKED` trims exactly the 200 acked ones, same as `=`.
- `ACKED` checks **all** groups. A second group on `tm:<vehicle>` (e.g. a separate alarm group) would hold back trimming until that group acks too — [XTRIM](https://redis.io/docs/latest/commands/xtrim/)
- Size, local probe: 36 000 frames shaped like §3 cost about **110 B/entry** when each value is a separate stream field, and **311 B/entry** as one JSON blob field. Streams compress repeated field names.
  - At 10 Hz that is about 95 MB/day per vehicle (fields layout).
  - At 10 × 100 Hz it is about 9.5 GB/day, so the stream must be a **buffer, not a store**.
- With `maxmemory-policy noeviction`, writes fail with an error at the limit and reads keep working. The default `maxmemory` on 64-bit is 0, meaning no limit — [eviction](https://redis.io/docs/latest/develop/reference/eviction/)

### 3. Persistence and restart

- RDB snapshots periodically, so "you should be prepared to lose the latest minutes of data" — [persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- AOF fsync settings — [persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/):
  - `appendfsync always` fsyncs **before sending replies** and does group commit: one fsync per batch or pipeline.
  - `everysec` (the default) "may lose 1 second of data".
  - `no` leaves flushing to the OS, typically every 30 s.
- When both AOF and RDB are on, a restart loads the AOF — [persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)
- **Consumer-group state (PEL, last-delivered-id, delivery counts) is written to AOF and RDB and replicated.** "After a restart, the AOF will restore the consumer groups' state." Replication is async, and failover is only best-effort, even with `WAIT` — [streams intro › persistence, replication and message safety](https://redis.io/docs/latest/develop/data-types/streams/)
- `SIGTERM`/`SIGINT` (i.e. `docker stop`) runs the full SHUTDOWN sequence: save RDB if save points are set, flush the AOF, exit. "If persistence is enabled this commands makes sure that Redis is switched off without any data loss." With neither persistence mode on, nothing is dumped — [SHUTDOWN](https://redis.io/docs/latest/commands/shutdown/)
- Defaults in redis.conf: `appendonly no`, `appendfsync everysec`, `aof-use-rdb-preamble yes` — [redis.conf](https://github.com/redis/redis/blob/unstable/redis.conf). The image needs flags to get AOF.
- Local probe (AOF `always`): 5 entries pending, then `docker restart` → xlen 5, pending 5. Then `docker kill -s KILL` + start → xlen 5, pending 5.
- What a restart costs:
  - **Graceful stop (the ENG-02 test):** nothing.
  - **Hard kill with everysec:** up to about 1 s of writes. That covers XADDs the producer saw succeed, which means frame loss. It also covers XACKs, which means redelivery and duplicates (R5), and PEL updates, which means entries come back via `>`.
  - **Hard kill with always:** nothing that was acknowledged to a client.

### 4. Client resilience (redis-py asyncio)

- redis-py ≥ 8.0 defaults — [v8.0.0 release notes](https://github.com/redis/redis-py/releases/tag/v8.0.0), [_defaults.py v8.1.0](https://github.com/redis/redis-py/blob/v8.1.0/redis/_defaults.py):
  - `socket_timeout = socket_connect_timeout = 5 s`
  - TCP keepalive on
  - retry = `ExponentialWithJitterBackoff(base=0.01, cap=1)`, **10 retries**
- Default retried errors: `ConnectionError`, `TimeoutError` — [retry docs](https://redis.readthedocs.io/en/stable/retry.html)
- Before 8.0 the asyncio defaults were base 1 s, cap 10 s and 3 retries — [6.4.0 source](https://sources.debian.org/data/main/p/python-redis/6.4.0-1/redis/asyncio/client.py)
- Local probe, Redis stopped for 10 s under a `BLOCK 2000` loop: the built-in retry ran out after about 4–5 s per call and raised `ConnectionError` to the app (at 5.4 s and 10.3 s). The loop reconnected on its own at 14.8 s. **The worker loop must catch `ConnectionError`/`TimeoutError`, back off and continue.** No reconnect code is needed beyond that, because the pool reconnects itself.
- **Gotcha:** with `BLOCK` ≥ `socket_timeout`, the read times out on the client side — [v8.0.0 notes](https://github.com/redis/redis-py/releases/tag/v8.0.0), [#2807](https://github.com/redis/redis-py/issues/2807). Local probe: `BLOCK 7000` with the default 5 s timeout raised `TimeoutError` only after **62 s**, because each timeout was retried 10 times. Rule: **`BLOCK` (1000 ms) < `socket_timeout` (5 s).**
- At-least-once corner cases. These are reasoning from the docs above; I did not test them.
  - If a retried `XREADGROUP >` lost its first reply, the entries are already in this consumer's PEL but the app never saw them. They come back through the `0` re-read or the 30 s reclaim: late, not lost.
  - A retried `XADD` whose reply was lost writes the frame twice, and R5 dedup removes the copy. Redis 8.6+ `XADD IDMP producer iid` would dedup at the broker — [XADD](https://redis.io/docs/latest/commands/xadd/)
- `BUSYGROUP` on startup is expected and should be ignored: catch the `ResponseError` and check for the prefix — [XGROUP CREATE](https://redis.io/docs/latest/commands/xgroup-create/)

### 5. Redis Streams vs Kafka vs NATS JetStream

| | Redis Streams | Kafka | NATS JetStream |
|---|---|---|---|
| Delivery | at-least-once via PEL + XACK; claims are explicit ([intro](https://redis.io/docs/latest/develop/data-types/streams/)) | at-least-once by default; process, then commit offset ([design](https://kafka.apache.org/43/design/design/)) | at-least-once; AckWait (30 s default) redelivers ([ack docs](https://docs.nats.io/learn/jetstream/acknowledgment)) |
| Retry limit / DLQ | delivery count + your own DLQ stream | **CONTRADICTED** for 4.x **share groups**: `group.share.delivery.count.limit` (default 5, range 2–10; KIP-932 production-ready in 4.2) and a share-group DLQ since 4.4 (KIP-1191). Classic consumer groups still have neither | `MaxDeliver` + `BackOff`, then a max-deliveries advisory; **no built-in DLQ** ([ack docs](https://docs.nats.io/learn/jetstream/acknowledgment)) |
| Ordering | per stream; lost with N consumers on one stream | total order per partition, one consumer per partition per group ([design](https://kafka.apache.org/43/design/design/)) | redelivery arrives after later messages unless MaxAckPending=1 ([ack docs](https://docs.nats.io/learn/jetstream/acknowledgment)) |
| Storage | RAM + AOF/RDB; async replication | disk log; acks 0/1/all ([design](https://kafka.apache.org/43/design/design/)) | memory or disk ([JetStream](https://docs.nats.io/nats-concepts/jetstream)) |
| Producer dedup | `IDMP` (8.6+) | idempotent producer | `Nats-Msg-Id`, 2 min window ([deep dive](https://docs.nats.io/using-nats/developer/develop_jetstream/model_deep_dive)) |
| Ops cost for us | already in the stack (spec §3 says `tm:<vehicle>` streams) | new cluster + client lib | new server |

- Load at review Q11 (10 vehicles × 100 Hz): 1 000 XADD/s. Redis docs report 0.5–1 M XADD/s with pipelining "in an average machine" — [streams intro](https://redis.io/docs/latest/develop/data-types/streams/). The broker is not the first bottleneck. More likely the Python worker (parse + DB insert) or the Postgres insert rate (R6/R10), or AOF `always` fsync on Docker Desktop disks (**STILL UNVERIFIED**: to be measured in ATP-08).

## Decision candidate

**Chosen:** Redis 8.10 Streams, one stream per vehicle `tm:<vehicle>`, one group `ingest`, stable consumer names (`ingest-1`, `ingest-2` from env).
- **Ack only after the DB commit.** This gives at-least-once delivery; R5's idempotent insert makes it effectively once.
- Reclaim with `XAUTOCLAIM` at 30 s idle, with the delivery count read from `XPENDING`.
- Poison when `times_delivered > 3` → `tm:dlq` + `XACK` in one `MULTI`.
- Trim with `XADD ... ACKED MAXLEN ~`.
- AOF `appendfsync always`, plus RDB, `noeviction`.

**Because:**
- It meets ING-07/08 and ENG-02 with documented commands, all checked locally.
- The PEL survives restart.
- It is already in the stack, and the rate is about 1000× below Redis limits.

**Rejected:**
- Kafka — partitions would give per-vehicle order for free (R4), but it adds a JVM/KRaft service, has no built-in DLQ, and offers more durability than a 25 h single-node bench tool needs.
- NATS JetStream — MaxDeliver/BackOff come built in, but it is a new service and still has no DLQ.
- `XREADGROUP ... CLAIM` (8.4) — a nicer single call, but it is newer and its redis-py reply parsing is **STILL UNVERIFIED** (not used; upgrade only). Keep it as an upgrade.
- `appendfsync everysec` — loses up to 1 s on a hard kill, which breaks "0 frames lost".

**Commands**
```
XGROUP CREATE tm:NYX-TA1 ingest 0 MKSTREAM                       # ignore -BUSYGROUP
XADD tm:NYX-TA1 ACKED MAXLEN ~ 360000 * vehicle NYX-TA1 seq 42 ...  # producer; ~10 h @10 Hz, ~40 MB
XREADGROUP GROUP ingest ingest-1 COUNT 100 STREAMS tm:NYX-TA1 tm:NYX-TA2 0 0        # own PEL first
XREADGROUP GROUP ingest ingest-1 COUNT 100 BLOCK 1000 STREAMS tm:NYX-TA1 tm:NYX-TA2 > >
XACK tm:NYX-TA1 ingest <id> [<id> ...]                           # only after DB COMMIT
XAUTOCLAIM tm:NYX-TA1 ingest ingest-1 30000 <cursor> COUNT 100   # every ~5 s
XPENDING tm:NYX-TA1 ingest <min_id> <max_id> 100 ingest-1        # times_delivered
MULTI; XADD tm:dlq * src tm:NYX-TA1 src_id <id> reason poison deliveries 4 payload <...>; XACK tm:NYX-TA1 ingest <id>; EXEC
```

**Worker loop (pseudocode)**
```python
MAX_DELIVERIES, RECLAIM_IDLE_MS, BLOCK_MS = 3, 30_000, 1_000   # ponytail: BLOCK must stay < socket_timeout (5 s)
r = redis.asyncio.Redis(host=cfg.redis_host, socket_timeout=5)   # default retry: 10 tries, ~4 s

async def run(me: str) -> None:
    for s in STREAMS: await create_group_ignore_busygroup(s)
    backlog = True                                     # re-read own PEL after start AND after every reconnect
    while True:
        try:
            ids = {s: ("0" if backlog else ">") for s in STREAMS}
            batch = await r.xreadgroup(GROUP, me, ids, count=100, block=None if backlog else BLOCK_MS)
            if backlog and not any(entries for _, entries in batch): backlog = False; continue
            await process(batch, check_poison=backlog)  # own-PEL reads also bump times_delivered
            if reclaim_due(): await reclaim(me)
            backoff.reset()
        except (ConnectionError, TimeoutError):
            log.warning("redis unavailable"); health.degraded(); backlog = True
            await asyncio.sleep(backoff.next())         # 0.5 s doubling, cap 5 s

async def process(batch, check_poison):
    for stream, entries in batch:
        gone   = [i for i, f in entries if f is None]             # trimmed while pending: ack, log LOST
        poison = await over_limit(stream, entries) if check_poison else []
        bad    = [e for e in entries if invalid(e)]               # GS-ING-02 malformed → DLQ at once
        good   = [e for e in entries if e not in poison + bad and e[1] is not None]
        await db.insert_idempotent(good)                          # R5: ON CONFLICT DO NOTHING; COMMIT
        async with r.pipeline(transaction=True) as p:             # DLQ + ack atomically
            for e, why in [(e, "poison") for e in poison] + [(e, reason(e)) for e in bad]:
                p.xadd(DLQ, dlq_record(stream, e, why))
            p.xack(stream, GROUP, *[i for i, _ in entries])
            await p.execute()

async def reclaim(me):
    for s in STREAMS:
        cursor = "0-0"
        while True:
            cursor, claimed, _deleted = await r.xautoclaim(s, GROUP, me, RECLAIM_IDLE_MS, cursor, count=100)
            if claimed: await process([(s, claimed)], check_poison=True)
            if cursor == "0-0": break

async def over_limit(stream, entries):   # XPENDING range over these ids for me → times_delivered > MAX_DELIVERIES
    ...
```
How ING-07 is proven: a crash at any point before `XACK` leaves the entry in the PEL. It then comes back through the own-PEL `0` read (same consumer name) or through another worker's `XAUTOCLAIM` after 30 s. A crash after COMMIT but before XACK causes a re-insert, which the R5 unique key absorbs. The test: kill a worker mid-stream, count distinct `seq` in the DB, and expect 0 missing.

**docker compose (redis service)**
```yaml
redis:
  image: redis:8.10.3
  command: [redis-server, --appendonly, "yes", --appendfsync, always, --save, "300 10",
            --maxmemory, 512mb, --maxmemory-policy, noeviction]
  volumes: [redis-data:/data]          # AOF + RDB survive container recreate
  stop_grace_period: 30s               # let SHUTDOWN flush (**VERIFIED**: `docker stop` and Compose `stop_grace_period` default to 10 s)
  healthcheck: {test: [CMD, redis-cli, ping], interval: 2s, timeout: 2s, retries: 15}
```
**Producer (simulator / bench gateway):** it must buffer frames in order while Redis is down (a bounded deque, 10 s × 10 Hz × 2 = 200 frames), then flush on reconnect. Without that, ENG-02 creates real gaps, and ING-04 would correctly report them as a GAP.

## Risks / what would make us switch

- **Hard kill with `always` loses nothing, but on Docker Desktop fsync may cost latency.** If ATP-08 shows p95 ingest > 200 ms because of fsync, drop to `everysec` and write an NCR for the 1 s hard-crash window.
- **`ACKED` trimming + a stuck consumer means the stream grows until `maxmemory`.** XADD then errors and the producer buffers. Alert on `XINFO GROUPS` lag / `XPENDING` count. Any extra consumer group on `tm:*` must ack, or it pins memory.
- **Poison latency:** a poison frame takes about 3 × 30 s to reach the DLQ. If R4 chooses strict per-vehicle order, that vehicle stalls for that long. R4 must decide between head-of-line blocking and skipping.
- **Single Redis node = SPOF.** Async replication plus best-effort failover can lose acked writes ([intro](https://redis.io/docs/latest/develop/data-types/streams/)).
- **Switch to Kafka if any of these happen:**
  - We need the broker itself to keep days of raw frames that consumers can re-read (today the MinIO archive does that).
  - We need writes replicated with quorum acks (`acks=all`) and no single point of failure, i.e. flight ops rather than a test bench.
  - The backlog can exceed RAM.
  - Vehicle count × rate grows enough that R4's ownership scheme becomes the hard part, and broker partitions keyed by vehicle are simpler.
- **Switch to JetStream if:** we want disk-backed streams with built-in MaxDeliver/BackOff and a lightweight server, without Kafka's ops cost.
- **Not a trigger:** 10 vehicles × 100 Hz (1 000 msg/s, about 110 KB/s into Redis). Re-measure in ATP-08 before claiming it.

## Open questions → assumptions.md

1. Is the producer (simulator now, bench gateway later) allowed to buffer during a broker outage? Assumed **yes**. Otherwise ENG-02 + "no frame lost" cannot both hold.
2. Does "frame failing 3×" mean 3 deliveries (`times_delivered > 3` → DLQ on the 4th claim) or 3 failures including the first? Assumed **3 failed deliveries**.
3. Pin the minimum Redis version at **8.2** (`ACKED`)? Assumed yes, with the image pinned to 8.10.3.
4. One DLQ `tm:dlq` for both malformed (ING-02) and poison (ING-08), with a `reason` field? Assumed yes.
5. Stream retention target: `MAXLEN ~ 360000` per vehicle (about 10 h at 10 Hz, 1 h at 100 Hz)? It only needs to cover the longest worker outage we accept. The DB and archive are the record.
6. Does the live UI read from the stream (XREAD, no group) or from the DB/pub-sub? If it used a second consumer group, that would affect `ACKED` trimming (R9).


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| `XTRIM ~ … ACKED` protects unacked entries | VERIFIED | redis 8.2 test | D-ING (stream trimming) |
| Kafka 4.x has no retry limit / DLQ | CONTRADICTED (share groups) | Kafka source + 4.2/4.4 upgrade notes | D-ING-01 rationale ("no DLQ") |
| AOF `always` fsync on Docker Desktop disks as bottleneck | STILL UNVERIFIED | measurement pending | D-ING-07 |
| `XREADGROUP … CLAIM` in redis-py | STILL UNVERIFIED | not tested; not used | none |
| Docker default stop timeout 10 s | VERIFIED | Docker docs, Compose spec | D-ENG-03 |

CONTRADICTED: Kafka 4.x has no retry limit / DLQ.
