# S1 – Gate 2 spikes (throwaway, measured 2026-11-13)

Checks the "Proves early" column of design §5.3 Gate 2 before implementation starts. Scratch code was in the session scratchpad (`spikes/`) and has been deleted. All containers, networks and volumes (compose project `nyxspike`) and the spike image were removed afterwards.

**Machine:** Apple M4 Pro, macOS (Darwin 27.2), Docker Desktop 4.54.0, Engine 29.1.2 (client 29.2.1), Compose v2.40.3-desktop.1, VM kernel 6.12.54-linuxkit, 12 CPU, 8 GB. Host load average 3–4 from other jobs. The app image was `python:3.14-slim` (3.14.10) with minio 7.2.20, redis-py 8.1.0, fastapi 0.162.7 and uvicorn 0.54.0. Images: `redis:8.10.3` (`sha256:1f88291b…dd53`), `bitnamilegacy/minio:2025.5.24` (index `sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c`), `timescale/timescaledb:2.32.0-pg17` (pulled, not exercised).

## Summary

| Spike | Verdict | Key number | Design change needed |
| --- | --- | --- | --- |
| S1 MinIO image | **PASS** | `mc` and `curl` are both in the image; a bad `x-amz-checksum-sha256` is rejected with `XAmzContentChecksumMismatch`; data survives re-creating the container | Pin the index digest `sha256:451fe685…a349c` (amd64 and arm64). Data path is `/bitnami/minio/data`. Give `minio` a curl healthcheck. Use the **same image** for `minio-init`, gated on `service_healthy`, which replaces R11's `pgsty/mc` retry loop |
| S2 `up --wait` + one-shots | **PASS with a rule** | Full `up -d --wait`: rc=0 in 6.1 s. One-shot without a waiting dependent in the up set: **rc=1** `container … exited (0)`. `--wait migrate` alone returns **while migrate is still running** | Every one-shot needs a dependent with `service_completed_successfully`. **Never list a one-shot in `--wait`.** CI: `up -d --wait redis db && docker compose run --rm migrate` (replaces R11's `up -d --wait redis db migrate`) |
| S3 Redis ACL | **PASS** | All 4 claims hold. `redis-cli` exits **0** on WRONGPASS / NOAUTH / NOPERM | Healthcheck must be `… ping \| grep -q PONG`. Use the ACL file below (selectors, sha256 hashes generated from `.env`). Add `+client\|setinfo`. Add an `admin` user for tests and ops |
| S4 `appendfsync always` | **PASS** | Single XADD p50 0.30 ms / p99 0.56–0.68 ms (everysec: 0.022 / 0.05 ms). Serial throughput 3.2–3.3 k/s; batch 100 gives 57–62 k/s. Paced 2 × 100 Hz: p50 1.2 ms, p99 4.7 ms, max 13–21 ms | None (keep D-ING-07). 200/s is about 6 % of one serial connection. Caveat: whether fsync inside the Docker Desktop VM reaches the Mac SSD is **STILL UNVERIFIED** (2016 HyperKit: F_FULLFSYNC per flush; no current doc) |
| S5 Clock | **PASS** | Container↔container median +0.21 ms (inside RTT/2 ≈ 0.9 ms). Redis `TIME` vs container median −0.005 ms. Host↔VM median −0.5 ms (range −1.0…+2.4 ms) with no trend over 3 min | None (D-PERF-01 holds). Host sleep/wake was not tested |
| S6 SSE reconnect | **PASS with a change** | Restart command to first new event: plain uvicorn **11.1 s** (stalls until SIGKILL at a 10 s grace period). With `timeout_graceful_shutdown=1`: **2.30–2.35 s**. Generators ended on SIGTERM: **1.14–1.25 s**. 0 gaps and 0 duplicates in all runs | Set `stop_grace_period` explicitly: this engine defaults new containers to **StopTimeout=1**, which hides the stall. End SSE generators from a `uvicorn.Server.handle_exit` hook as well as setting the 1 s timeout. 1.0 s of the 1.2 s is `retry: 1000` |

## Design changes needed (consolidated)

1. **D-ARC-09 / R11 compose table:** `minio` and `minio-init` both use `bitnamilegacy/minio:2025.5.24@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c`. Volume at `/bitnami/minio/data`; env `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`. Healthcheck `curl -fsS http://localhost:9000/minio/health/live`. `minio-init`: `entrypoint: bash -c`, `mc alias set … && mc mb --ignore-existing s3/<bucket>`, `depends_on: minio: service_healthy`, `MC_CONFIG_DIR=/tmp/mc`. That env var was set as a precaution because the image runs as uid 1001 with `HOME=/`; the spike did not test without it. Drop `pgsty/mc` and the `until … sleep` loop.
2. **D-ENG-03 / R11 CI:** `make up` = `docker compose up -d --build --wait` with **no service list**. Each one-shot (`migrate`, `minio-init`) must have at least one long-running dependent with `service_completed_successfully` (true today: archiver, api, ingest). CI integration step = `docker compose up -d --wait redis db && docker compose run --rm migrate` (rc is migrate's exit code). Remove the R11 "fallback: poll /healthz" item.
3. **D-ENG-17:** Use the ACL file from S3. `make up` writes it (gitignored) from `.env`, storing passwords as `#<sha256>`. Healthcheck `redis-cli --no-auth-warning --user health --pass "$$REDIS_HEALTH_PW" ping | grep -q PONG`. Add an `admin` user (`~* &* +@all`, password in `.env`) for integration-test setup and ops. With `default off` nothing else can `FLUSHALL` or read `ACL LOG`. Key-permission facts: `XADD` needs only `%W`, `XREADGROUP` needs only `%R`, and `XACK` needs write. If the simulator reads `tc:*` through a group it needs `~tc:*` read-write, so the command list (a selector) is what blocks `XADD tc:*`, not the key pattern.
4. **D-UI-02:** Set `stop_grace_period: 5s` on every app service, explicitly. The documented Docker default is 10 s, but this machine creates containers with `StopTimeout=1`, so a test here would hide a stall that appears elsewhere. Keep `timeout_graceful_shutdown=1`, and also set a shutdown `asyncio.Event` from an overridden `uvicorn.Server.handle_exit` that SSE generators check, which gives 1.2 s instead of 2.3 s. Optional: `retry: 500` saves about 0.5 s. D-UI-02's earlier "0.87 s" result may have been helped by the 1 s StopTimeout (**STILL UNVERIFIED**).
5. **D-ING-07 (README limitation, no change to the choice):** `appendfsync always` was measured against ext4 on the Docker Desktop VM disk (`/dev/vda1`). Durability against a container or VM crash is what the R3 test showed. Durability against host power loss depends on Docker Desktop's disk flush behaviour, which is **STILL UNVERIFIED** (see above).

---

## S1 – MinIO image `bitnamilegacy/minio:2025.5.24`

**Goal.** Does it pull? Does it start with a persistent volume? Does it enforce `x-amz-checksum-sha256`? Does it contain `mc` and/or `curl`?

**Commands.**
```sh
docker pull bitnamilegacy/minio:2025.5.24
docker run --rm --entrypoint sh bitnamilegacy/minio:2025.5.24 -c 'id; for b in mc curl wget minio nc bash; do command -v $b || echo MISSING; done; mc --version; minio --version'
docker buildx imagetools inspect bitnamilegacy/minio:2025.5.24
docker compose up -d --wait minio minio-init     # compose below
docker run --rm --network nyxspike_default app python s1_checksum.py put   # minio-py 7.2.20
docker compose rm -sf minio && docker compose up -d --wait minio            # new container, same named volume
docker run --rm --network nyxspike_default app python s1_checksum.py get
```

**Raw output (excerpt).**
```
uid=1001 gid=0(root) groups=0(root)
mc: /opt/bitnami/minio-client/bin/mc
curl: /usr/bin/curl
wget: MISSING
minio: /opt/bitnami/minio/bin/minio
nc: MISSING
mc version DEVELOPMENT.2025-05-21T01-59-54Z (commit-id=f71ad84bcf0fd4369691952af5d925347837dcec)
minio version DEVELOPMENT.2025-05-24T17-08-30Z (commit-id=ecde75f9112f8410cb6cacb4b76193f1475b587e)
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
Entrypoint ["/opt/bitnami/scripts/minio/entrypoint.sh"]  User "1001"  Volumes {"/bitnami/minio/data":{},"/certs":{}}

Digest:    sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
  Platform:  linux/amd64  (…@sha256:b3d51900e846…)
  Platform:  linux/arm64  (…@sha256:f9a60dbd45b2…)
Created 2025-06-06T03:12:33Z

minio-init-1  | Added `s3` successfully.
minio-init-1  | Bucket created successfully `s3/archive`.

python make_bucket pyb OK
good PUT etag ec78dbd963d2fc01e51176ed4dec299e md5 ec78dbd963d2fc01e51176ed4dec299e
bad PUT rejected: XAmzContentChecksumMismatch
bad.bin stat: NoSuchKey
stored checksum G5d+n4Txsmtu1/aLBJj67iOF6kElvSmtzkp9kQa6MTQ= == ours: True
 Container nyxspike-minio-1  Healthy            # after rm + recreate on the same volume
after recreate: good.bin sha ok: True buckets ['archive', 'pyb']
```

**Working compose snippet.**
```yaml
  minio:
    image: bitnamilegacy/minio:2025.5.24@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
    environment: {MINIO_ROOT_USER: "${S3_ACCESS_KEY}", MINIO_ROOT_PASSWORD: "${S3_SECRET_KEY}"}
    volumes: [miniodata:/bitnami/minio/data]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9000/minio/health/live"]
      interval: 2s
      timeout: 3s
      retries: 30
      start_period: 5s
  minio-init:
    image: bitnamilegacy/minio:2025.5.24@sha256:451fe6858cb770cc9d0e77ba811ce287420f781c7c1b806a386f6896471a349c
    depends_on: {minio: {condition: service_healthy}}
    entrypoint: ["/bin/bash", "-c"]
    command: ["mc alias set s3 http://minio:9000 \"$$S3_ACCESS_KEY\" \"$$S3_SECRET_KEY\" && mc mb --ignore-existing s3/$$S3_BUCKET"]
    environment: {MC_CONFIG_DIR: /tmp/mc, S3_ACCESS_KEY: "${S3_ACCESS_KEY}", S3_SECRET_KEY: "${S3_SECRET_KEY}", S3_BUCKET: "${S3_BUCKET}"}
    restart: "no"
```
(The spike used literal credentials. The `$$VAR` env form shown here is the standard compose escape and was not run as written.)

**Verdict: PASS.** No fallback to `pgsty/minio` was needed. Creating the bucket from Python (`make_bucket`) also works, so a `minio-init` container is optional. Keeping it keeps the archiver free of admin work.

**Consequence.** Design change 1. The image is frozen (built 2025-06-06) and the binaries report `DEVELOPMENT.*` versions; list this under README limitations as already planned.

---

## S2 – `docker compose up -d --wait` with one-shot services

**Goal.** Does `--wait` succeed, hang or fail when `migrate` and `minio-init` exit 0? Find the working pattern.

**Test compose (shape of the real one).**
```yaml
services:
  db:          # long-running, healthcheck
  migrate:     # one-shot: sleep $MIGRATE_SLEEP; exit $MIGRATE_RC ; depends_on db: service_healthy ; restart: "no"
  minio-init:  # one-shot: exit 0 ; restart: "no"
  api:         # long-running, healthcheck
    depends_on:
      migrate:    {condition: service_completed_successfully}
      minio-init: {condition: service_completed_successfully}
```

**Raw output.** For each case: rc, then the final state of each service.
```
### docker compose up -d --wait                         rc=0  api=running db=running migrate=exited(0) minio-init=exited(0)   (6.14 s)
### docker compose up -d --wait api                     rc=0  (same; one-shots started as dependencies)
### docker compose up -d --wait db api                  rc=0
### MIGRATE_RC=3 docker compose up -d --wait api        service "migrate" didn't complete successfully: exit 3
                                                        rc=1  api=created db=running migrate=exited(3)
### MIGRATE_RC=3 docker compose up -d --wait            rc=1  (same)
### docker compose up -d --wait migrate minio-init      container nyxspike-minio-init-1 exited (0)
                                                        rc=1  migrate=running minio-init=exited(0)
### docker compose up -d --wait minio-init api          rc=0
### + orphan one-shot `seed` (nothing depends on it):
###   docker compose up -d --wait                       container nyxspike-seed-1 exited (0)   rc=1
###   docker compose up -d --wait api                   rc=0
### MIGRATE_SLEEP=0 docker compose up -d --wait db migrate   container nyxspike-migrate-1 exited (0)   rc=1
### MIGRATE_SLEEP=5 docker compose up -d --wait db migrate   rc=0  migrate=running   <- returned before migrate finished
### docker compose up -d --wait db && MIGRATE_SLEEP=0 docker compose run --rm migrate   "migrated" rc=0
### MIGRATE_RC=3 docker compose run --rm migrate             rc=3
```
S1 reproduced the same failure for real: `docker compose up -d --wait minio minio-init` → `container nyxspike-minio-init-1 exited (0)`, exit=1.

**Rule (observed on Compose v2.40.3).** `--wait` accepts an exited-0 one-shot only if a service **in the same up set** depends on it with `service_completed_successfully`. Otherwise it reports the exit as a failure. A one-shot that is still running counts as "running" and passes. Nothing hung, and a failing one-shot fails fast with rc=1, leaving its dependents in `created`.

**Verdict: PASS** (for `make up` as designed), with the pattern rule below.

**Consequence.** Design change 2. R11's CI step `docker compose up -d --wait redis db migrate` is racy: rc=1 if migrate finishes first, or rc=0 before migrations have run. Replace it with `up -d --wait redis db && docker compose run --rm migrate`.

---

## S3 – Redis ACL (D-ENG-17)

**Goal.** (a) The `health` user's `ping` works as a healthcheck. (b) `sim` cannot `XADD tc:*`. (c) `api` can. (d) `XREADGROUP`/`XAUTOCLAIM` work for `ingest`. Find the needed key patterns and commands.

**Working ACL file** (generated from `.env`; passwords stored as SHA-256 so the file holds no plaintext):
```
user default off resetkeys resetchannels -@all
user health on #<sha256> resetkeys resetchannels -@all +ping
user api on #<sha256> resetkeys resetchannels -@all +ping +client|setinfo +xadd %W~tc:* (~cmdlock:* +set +get +del) (%R~ui:events %R~hb:* +xread +xrange +xrevrange +get +mget)
user sim on #<sha256> resetkeys resetchannels -@all +ping +client|setinfo +xread +xrange %R~tc:* (~tc:* +xreadgroup +xack +xgroup|create) (~tm:* ~tcack:* +xadd)
user ingest on #<sha256> resetkeys resetchannels -@all +ping +client|setinfo (~tm:* ~tcack:* +xreadgroup +xack +xautoclaim +xclaim +xpending +xgroup|create +xinfo|stream +xinfo|groups) (~tm:dlq ~ui:events +xadd) (~hb:* +set +get)
user admin on #<sha256> ~* &* +@all
```
Generator (stands in for `make up`): `printf '%s' "$PW" | shasum -a 256 | cut -d' ' -f1`, prefixed with `#`.

**Compose.**
```yaml
  redis:
    image: redis:8.10.3
    command: ["redis-server", "--aclfile", "/etc/redis/users.acl", "--appendonly", "yes", "--appendfsync", "always", "--maxmemory-policy", "noeviction"]
    environment: {REDIS_HEALTH_PW: "${REDIS_HEALTH_PW}"}
    volumes: ["./redis/users.acl:/etc/redis/users.acl:ro", "redisdata:/data"]
    healthcheck:
      test: ["CMD-SHELL", "redis-cli --no-auth-warning --user health --pass \"$$REDIS_HEALTH_PW\" ping | grep -q PONG"]
      interval: 1s
      timeout: 3s
      retries: 30
```
`docker compose up -d --wait` → `Container nyxspike-redis-1 Healthy`, rc=0.

**Raw output: healthcheck variants** (inside the container).
```
redis-cli --user health --pass $PW ping           PONG                                    rc=0
redis-cli --user health --pass wrong ping         AUTH failed: WRONGPASS … NOAUTH …       rc=0   <- !
redis-cli ping                                    NOAUTH Authentication required.         rc=0   <- !
redis-cli --user health --pass $PW get x          NOPERM User health has no permissions…  rc=0   <- !
(without --no-auth-warning)                       Warning: Using a password with '-a' or '-u' option … (stderr)
```

**Raw output: permission matrix** (redis-py 8.1.0, RESP2; RESP3 `HELLO` also OK).
```
default user (no auth) -> AuthenticationError HELLO must be called with the client already authenticated …
health  PING                                             OK   True
health  GET x / INFO                                     DENY no permissions to run the 'get' / 'info' command
api     XADD tc:NYX-TA1                                  OK   1794595460359-0          (c)
api     SET cmdlock:NYX-TA1 NX PX / DEL                  OK
api     XREAD / XREVRANGE ui:events, GET hb:ingest-1     OK
api     XRANGE tc:NYX-TA1                                DENY No permissions to access a key
api     XADD tm:NYX-TA1 / XADD ui:events / SET tc:…      DENY No permissions to access a key
api     FLUSHALL                                         DENY no permissions to run the 'flushall' command
sim     XADD tc:NYX-TA1 (self-command)                   DENY No permissions to access a key   (b)
sim     XREAD tc / XGROUP CREATE / XREADGROUP / XACK     OK
sim     XADD tm:NYX-TA1 MAXLEN~ / XADD tcack:NYX-TA1     OK
sim     XADD tm:NYX-TA1 ACKED MAXLEN ~ 360000 (raw)      OK
sim     XADD ui:events / XREADGROUP tm:NYX-TA1           DENY No permissions to access a key
ingest  XREADGROUP tm 0 / tm > / tm+tcack in one call    OK                          (d)
ingest  XAUTOCLAIM tm:NYX-TA1 min-idle 0                 OK   0-0                    (d)
ingest  XPENDING / XACK / XGROUP CREATE MKSTREAM / XINFO STREAM   OK
ingest  XADD ui:events MAXLEN~3000 / XADD tm:dlq / SET hb:ingest-1 PX   OK
ingest  XADD tm:NYX-TA1 (forge) / XADD tc:NYX-TA1        DENY No permissions to access a key
ingest  XRANGE tm:NYX-TA1                                DENY no permissions to run the 'xrange' command
ACL LOG: ingest key tm:NYX-TA1 / ingest command xrange / ingest key tc:NYX-TA1 / sim key tm:NYX-TA1 …
```
Extra probes (temporary users):
```
XREADGROUP with %R~tc:* only: []              -> XREADGROUP needs read permission only
XACK with %R~tc:* only: No permissions to access a key  -> XACK needs write
redis-py 8.1.0 connect+PING without +client|setinfo: True, but ACL LOG gets ('t2','command','client|setinfo') on every connect
```

**Verdict: PASS** for (a)–(d).

**Consequence.** Design change 3. Notes:
- `tm:dlq` matches `tm:*`, so `sim` can also `XADD tm:dlq`. Rename it to `dlq:tm` if that matters.
- `hb:*` is written only by `ingest` here. If the simulator or API also heartbeat, add `(~hb:* +set)` for them.
- Lua `EVAL` for compare-and-delete on `cmdlock:*` was not tested. Add `+eval +evalsha` to the cmdlock selector if used.

---

## S4 – cost of `appendfsync always`

**Goal.** Measure XADD latency and throughput, batch 1 vs 100, with `always` vs `everysec`, on a Docker Desktop named volume. Is 2 × 100 Hz comfortable?

**Setup.** Redis from S3 (AOF on, named volume `redisdata` = ext4 on `/dev/vda1`, the Docker Desktop VM disk). `CONFIG SET appendfsync <mode>`. The client was redis-py 8.1.0 in a container on the compose network. Each entry is one field `data` holding a ~300-byte JSON frame. Batch 1 = 5000 serial XADD calls. Batch 100 = 200 non-transactional pipelines of 100 XADD. Paced = 2 threads × 100 Hz × 20 s of single XADD calls (the simulator's shape). Each mode was run twice.

**Raw output.**
```
always   batch=  1 calls=5000 p50=0.309ms p95=0.475ms p99=0.680ms max=2.15ms throughput=3,172 XADD/s
always   batch=100 calls=200 p50=1.711ms p95=2.045ms p99=4.012ms max=5.49ms throughput=57,019 XADD/s
always   paced 2x100Hz 20s n=4000 p50=1.234ms p95=2.610ms p99=4.686ms max=21.15ms  over5ms=36 over10ms=10
everysec batch=  1 calls=5000 p50=0.022ms p95=0.036ms p99=0.047ms max=0.76ms throughput=42,000 XADD/s
everysec batch=100 calls=200 p50=0.546ms p95=0.661ms p99=0.873ms max=0.95ms throughput=179,380 XADD/s
everysec paced 2x100Hz 20s n=4000 p50=0.336ms p95=0.688ms p99=1.086ms max=9.03ms  over5ms=1 over10ms=0
always   batch=  1 calls=5000 p50=0.298ms p95=0.438ms p99=0.559ms max=5.33ms throughput=3,330 XADD/s
always   batch=100 calls=200 p50=1.548ms p95=2.081ms p99=2.367ms max=2.41ms throughput=62,095 XADD/s
always   paced 2x100Hz 20s n=4000 p50=1.276ms p95=2.691ms p99=4.705ms max=13.62ms  over5ms=35 over10ms=3
everysec batch=  1 calls=5000 p50=0.022ms p95=0.034ms p99=0.050ms max=0.50ms throughput=42,339 XADD/s
everysec batch=100 calls=200 p50=0.487ms p95=0.572ms p99=0.694ms max=0.78ms throughput=202,187 XADD/s
everysec paced 2x100Hz 20s n=4000 p50=0.342ms p95=0.738ms p99=1.306ms max=2.57ms  over5ms=0 over10ms=0
aof_delayed_fsync=0 in all runs
```

**Reading.** `always` adds about 0.28 ms per round trip (one fsync per event-loop pass). A pipeline of 100 shares a single fsync, so it costs about 1.6 ms, or 16 µs per entry. Paced latency is higher than serial (p50 1.2 ms vs 0.3 ms), which is consistent with idle wake-ups in the VM. Even so, p99 is 4.7 ms and the worst case 21 ms, against a 10 ms send period. At 2 × 100 Hz, the sim's 200 XADD/s plus ingest's per-batch `XADD ui:events` + `XACK` (pipelined) are far below the measured 3.2 k/s serial capacity on one connection.

**Verdict: PASS.** 2 × 100 Hz is comfortable with `always`.

**Consequence.** No change to D-ING-07. The simulator should not block its tick on XADD latency: at about 0.4 % of sends above 10 ms, the next send drifts by one tick. Record the README caveat (design change 5). The durability of an fsync inside the Docker Desktop VM against host power loss was not tested.

---

## S5 – clock comparability (D-PERF-01)

**Goal.** Are `time.time_ns()` stamps from two containers comparable? What is the offset against the macOS host clock? Is there drift over about 3 minutes?

**Method.**
- Container A sends `t1` through Redis (`RPUSH`/`BLPOP`) to container B, which stamps `t2`. A stamps `t3` on reply. Offset = `t2 − (t1+t3)/2`, bounded by ±RTT/2.
- A also compares Redis `TIME` and the ms part of an `XADD` entry ID with its own clock.
- The host (macOS Python 3.9, stdlib) calls an HTTP endpoint in a container through the published port and keeps the minimum-RTT sample of 10 per second (NTP-style).
- All runs were 180 s at 1 Hz. VM clocksource: `arch_sys_counter`.

**Raw output.**
```
A ab_off           n=185 min=-0.458 median=+0.213 max=+1.224 ms
A rtt              n=185 min=+0.437 median=+1.796 max=+4.779 ms
A redis_off        n=185 min=-0.513 median=-0.005 max=+0.385 ms
A eid_minus_local  n=185 min=-1.463 median=-0.699 max=-0.074 ms
A ab_off drift first10 median +0.335 -> last10 +0.447 ms over 185s

HOST (single sample/s) offset n=179 min=-2.604 median=+0.336 max=+3.992 ms; rtt median=3.150 max=9.057 ms
HOST(min-RTT of 10) offset n=130 min=-1.041 median=-0.497 max=+2.429 ms; best-rtt median=1.787 ms
  t=   0-  41s median off=-0.751ms median rtt=2.830ms
  t=  42-  83s median off=-0.232ms median rtt=1.439ms
  t=  84- 124s median off=-0.508ms median rtt=1.589ms
  t= 126- 166s median off=-0.416ms median rtt=2.186ms
  t= 168- 180s median off=-0.503ms median rtt=1.280ms
current_clocksource: arch_sys_counter
```
The min-RTT run overlapped with S6, so it ran under extra load.

**Reading.**
- Containers share the VM kernel clock. Every measured offset (container↔container, container↔Redis `TIME`) is inside the measurement error (RTT/2).
- The entry-ID ms is on average 0.7 ms before "XADD returned" on the client, as expected: the server stamps it, then the reply travels, and ms truncation accounts for up to 1 ms.
- Host vs VM: about −0.5 ms with no trend over 3 minutes. The single-sample run's "+0.4 → +2.9 ms" swing is RTT noise (port-proxy RTT up to 9 ms).

**Verdict: PASS.** D-PERF-01 ("one clock") holds. UI-01 (browser on the host vs VM stamps) has about 1 ms of offset error at this resolution.

**Consequence.** No design change. Not tested: drift after host sleep/wake (Docker Desktop resyncs the VM clock then). Keep an NCR note for long soak runs across a laptop sleep.

---

## S6 – SSE reconnect through `docker compose restart api` (D-UI-02, D-UI-04)

**Goal.** Measure the time from `docker compose restart api` to the first new event in a real EventSource. Check `Last-Event-ID` resume and whether the server stalls on shutdown.

**Setup.**
- `redis:8.10.3`, plus a producer container that `XADD`s to `ui:events` (`MAXLEN ~ 3000`) every 100 ms with a `seq` counter.
- An API (FastAPI 0.162.7 / uvicorn 0.54.0, started through `uvicorn.Server(...).run()` as PID 1) with `/events`. It sends `retry: 1000`, then `XREAD BLOCK 1000` from `Last-Event-ID` (or `$`), and emits `id: <entry id>` or a `: hb` heartbeat.
- The client is headless Chromium (Playwright `playwright-core` 1.60 alpha with the installed `chromium_headless_shell-1243`) running the page's own `EventSource('/events')` against `127.0.0.1:18000`. The Node driver records `Date.now()`, runs `docker compose restart api`, waits 8 s, then reads `window.log`.
- Shutdown modes:
  - `none`: plain uvicorn.
  - `timeout`: `timeout_graceful_shutdown=1`.
  - `event`: `timeout_graceful_shutdown=1`, plus a `Server.handle_exit` override that sets an `asyncio.Event` checked by the SSE generator loop.

**Finding 0: the engine's default stop timeout here is 1 s.**
```
docker inspect … nyxspike-api-1              StopTimeout=1   (no stop_grace_period in compose)
docker run -d alpine:3.20 sleep 100          plain docker run StopTimeout=1
docker run -d --stop-timeout 10 …            explicit --stop-timeout 10 -> 10
docker compose stop api (MODE=none, curl stream open)   stop took 1.31s   ExitCode=137
```
The images do not set StopTimeout, and `~/.docker/daemon.json` has no timeout. The source of the 1 s default is **STILL UNVERIFIED**: the documented defaults are 10 s (`docker stop`; Compose `stop_grace_period`), and no 1 s default is in the docs or Compose source (a Docker Desktop 4.54 / Engine 29 default or setting). On this machine every app container is SIGKILLed after 1 s, which **hides** the uvicorn stall. The first run (StopTimeout=1) gave `none` 2.12 s, `timeout` 2.14 s and `event` 1.21 s to the first new event, all with the old process killed (no "exited" log line for `none`/`timeout`).

**Main run with `stop_grace_period: 10s`** (Docker's documented default):
```
{"MODE":"none","restart_cmd_ms":10411,"last_msg_before_error_ms":10055,"error_event_ms":10118,"reopen_ms":11135,"first_new_event_ms":11138,"max_silence_ms":1083,"last_old_seq":168,"first_new_seq":169,"seq_gaps":0,"seq_dups":0,"events_total":216,"errors":1}
    api signal 15 at …990.957 ; next start at …001.442  (10.5 s, killed: no "generator closed"/"exited" line)
    api connect Last-Event-ID = 1794596000906-0
{"MODE":"timeout","restart_cmd_ms":1638,"last_msg_before_error_ms":1291,"error_event_ms":1302,"reopen_ms":2326,"first_new_event_ms":2330,"max_silence_ms":1039,"last_old_seq":81,"first_new_seq":82,"seq_gaps":0,"seq_dups":0,…}
    api signal 15 …019.981 ; generator closed, stopping = False …021.180 ; exited …021.189
{"MODE":"event","restart_cmd_ms":671,"last_msg_before_error_ms":226,"error_event_ms":226,"reopen_ms":1244,"first_new_event_ms":1247,"max_silence_ms":1021,"last_old_seq":71,"first_new_seq":72,"seq_gaps":0,"seq_dups":0,…}
    api signal 15 …040.311 ; generator closed, stopping = True …040.413 ; exited …040.493
```
Repeats (same 10 s grace period, times in ms from the restart command):
```
timeout restart_cmd 1662 error 1321 first_new 2347 gaps 0 dups 0
timeout restart_cmd 1582 error 1279 first_new 2302 gaps 0 dups 0
timeout restart_cmd 1658 error 1300 first_new 2327 gaps 0 dups 0
event   restart_cmd  583 error  152 first_new 1169 gaps 0 dups 0
event   restart_cmd  533 error  186 first_new 1205 gaps 0 dups 0
event   restart_cmd  593 error  114 first_new 1141 gaps 0 dups 0
```

**Reading.**
- Plain uvicorn waits for open SSE responses forever (`_wait_tasks_to_complete` loops while connections exist; `timeout_graceful_shutdown` defaults to `None` in 0.54.0). The stream keeps flowing until the container is SIGKILLed at the grace period. That costs 11.1 s with a 10 s grace period, which fails UI-07's 5 s budget.
- `timeout_graceful_shutdown=1` brings it to 2.3 s.
- Ending the generators from the signal hook brings it to 1.2 s. Of that, about 1.0 s is the browser's `retry: 1000` wait between `error` and reopen.
- The browser sent `Last-Event-ID` on every reconnect, and resume via `XREAD <id>` gave **0 gaps and 0 duplicates in all 9 runs**. The EventSource `error` event fired every time, 0.1–1.3 s after the command in `timeout`/`event` modes, so D-UI-04's "NO LIVE CONNECTION from the first error" banner shows even on the fast path.
- Each run had one heartbeat-sized silence (max 1.02–1.14 s between messages, apart from the restart gap).

**Verdict: PASS** with the change below: `none` FAILS UI-07 at a 10 s grace period; `timeout` and `event` pass.

**Consequence.** Design change 4.
- In compose: `stop_grace_period: 5s` on app services, set explicitly, so behaviour does not depend on the engine default (1 s here, 10 s documented).
- In the API: `timeout_graceful_shutdown=1` **and** a shutdown event set from an overridden `uvicorn.Server.handle_exit`, checked by every SSE generator loop. Measured 1.14–1.25 s to the first new event.
- Optional: `retry: 500`.
- The ATP-level UI-07 test should set the grace period explicitly too, or it measures the wrong thing on Docker Desktop.


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| VM fsync reaches the Mac SSD | STILL UNVERIFIED | only 2016 maintainer comment | D-ING-07 |
| 0.87 s helped by 1 s StopTimeout | STILL UNVERIFIED | not retested | D-UI-02 |
| (same, power-loss durability) | STILL UNVERIFIED | as above | D-ING-07 |
| Source of the observed 1 s stop timeout | STILL UNVERIFIED | documented default is 10 s | D-ENG-03 (explicit stop_grace_period) |

CONTRADICTED: none.

Sources with local PDFs: see [SOURCES.md](SOURCES.md).
