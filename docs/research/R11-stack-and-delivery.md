# R11 – Stack, delivery tooling, frame validation and sessions (incl. R13)

**Drives:** GS-ENG-01..10, GS-CMD-04 (identity), GS-ING-02, GS-DAT-01   **Answers review Q:** 5, 14, 18

## Question

R11: which Python layout, versions and tools let one engineer ship four Python services and a React console in about 25 h? How do we get `make up` from a fresh clone (ENG-01), 3 test levels (ENG-03), one green CI workflow (ENG-04), `/healthz` and JSON logs (ENG-05), env-only config (ENG-09), and a simple login that puts a user on every command and ack (CMD-04)?
R13: what exact schema does ING-02 enforce, what does a DLQ entry look like, and what is a test session (DAT-01)?
Aligned with, not re-decided: R3 (Redis 8.10.3, redis-py 8.x, one `tm:dlq`), R4 (PG advisory locks, workers share one role), R6 (timescaledb 2.32.0-pg17, psycopg 3, `gs_owner`/`gs_app`, mypy strict).

## Findings

Versions checked 2026-11-13 against the PyPI JSON API, the npm registry, the Docker Hub, mcr.microsoft.com and ghcr.io APIs, and GitHub releases. "Probe" means I ran it myself: pydantic 2.14.3 via `uv run`, and Docker 29.1.2 / Compose 2.40.3 on macOS.

### 1. Python layout and tooling

- A uv workspace has "a single lockfile" and "a single `requires-python`", and it is meant for several packages that are managed together. It is "not suited for cases in which members have conflicting requirements" — [uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/). All four of our services share their dependencies (pydantic, redis, psycopg), so a workspace would only add N `pyproject.toml` files for no gain.
- A single package with `[project.scripts]` entry points gives one lock, one image and one mypy run, and each service is just a different compose `command` — [uv projects/config](https://docs.astral.sh/uv/concepts/projects/config/).
- **Python 3.14** (3.14.10). 3.15.2 is out, but 3.15 was released on 2026-10-09 and is only a month old — [endoflife.date/python](https://endoflife.date/python). Every native dependency ships cp314 wheels: psycopg-binary 3.3.9, pyarrow 26.0.1, pydantic-core 2.50.4, uvloop 0.23.0, httptools 0.9.0 (PyPI file lists).
- **mypy, not pyright.** The spec says "mypy clean" (ENG-09), and psycopg is "checked using … Mypy in `--strict` mode" (R6). pydantic has a mypy plugin — [pydantic mypy](https://docs.pydantic.dev/latest/integrations/mypy/).
- `uv` in Docker: `COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/` — [uv Docker guide](https://docs.astral.sh/uv/guides/integration/docker/). Tag `0.13.8` checked on ghcr.io (HTTP 200).

### 2. Compose

- `depends_on` conditions: `service_healthy` waits for a dependency that "is expected to be healthy, which is defined with `healthcheck`". `service_completed_successfully` waits until it "run[s] to successful completion". The Postgres example uses `pg_isready -U … -d …` — [startup order](https://docs.docker.com/compose/how-tos/startup-order/).
- `docker compose up --wait` will "Wait for services to be running|healthy. Implies detached mode" — [compose up](https://docs.docker.com/reference/cli/docker/compose/up/). How it handles one-shot services that exit 0 (`migrate`, `minio-init`) was unverified; now **VERIFIED** (S2 and a retest 2026-11-14, Compose 2.40.3): `--wait` fails with `container … exited (0)` when a one-shot has no dependent with `service_completed_successfully`, and passes when it has one (compose `dependency_wait.go`, `start.go` getDependencyCondition). This is D-ENG-03's rule. Fallback: poll `/healthz` from the Makefile.
- Restart policy `unless-stopped` is "similar to `always`, except that when the container is stopped (manually or otherwise), it isn't restarted". Also: "If you manually stop a container, the restart policy is ignored" — [restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/).
- **Probe:** `docker kill` on an `unless-stopped` container → `exited restarts=0`, so it was **not** restarted. A process that crashes inside the container → `running restarts=2`. Consequence for ATP-05: "kill a worker" with `docker compose kill ingest-1` tests **R4 takeover by ingest-2**. To test **self-restart**, use `docker compose exec ingest-1 kill -9 1`, which makes the main process exit so the policy restarts it.
- **MinIO images are gone.** `hub.docker.com/v2/repositories/minio/minio` and `…/minio/mc` return 404, and anonymous access to the quay.io API returns 401 (both probed). MinIO CE is now "source-only" — [glukhov.org](https://glukhov.org/data-infrastructure/object-storage/minio-dead/), [madewithlove](https://madewithlove.com/blog/thanks-for-all-the-buckets/) (third-party blogs; no official MinIO notice found). The community fork `pgsty/minio` has tags up to `RELEASE.2026-08-04T00-00-00Z` (4.2 M pulls). Development has moved on as `pgsty/silo` (AGPL-3.0; it bundles the client as `mcli`; there is "no `minio` server binary alias"). `pgsty/mc:RELEASE.2026-11-12T00-00-00Z` is current — [pgsty/minio](https://github.com/pgsty/minio), Docker Hub API. The `pgsty/minio` image has both: **VERIFIED** (`Dockerfile.goreleaser` copies `mcli`, linked as `mc`, and `curl`; the distroless variant has neither). The repo is now `pgsty/silo` (renamed 2026-08-06, last MinIO-branded release 20260804), from the Pigsty project: my probe was blocked by the sandbox. For that reason the design below does not rely on either.
- Healthchecks: Redis uses `redis-cli ping` (R3). Postgres uses `pg_isready`. MinIO gets no healthcheck. Instead, an `mc` init container retries `mc alias set` until the server answers, then runs `mc mb --ignore-existing`, and the archiver waits for it with `service_completed_successfully`.

### 3. Tests at three levels

- **Unit:** pytest 9.2.0 on pure logic (limits, hysteresis, gap/reset, the Frame model, the command state machine). pytest-asyncio 1.5.0 with `asyncio_mode = "auto"`.
- **Integration:** real Redis and TimescaleDB. testcontainers 4.15.0 would start containers from inside pytest. However, that duplicates the image pins already in compose and adds a dependency plus the Ryuk reaper. **Reuse compose:** `docker compose up -d --wait redis db migrate`, then point the tests at `127.0.0.1` through env. The same command works locally and in CI. Isolation: each test uses a unique vehicle name (`TEST-<uuid8>`) for streams and rows.
- **E2E:** Playwright. The UI is already TypeScript, and `@playwright/test` brings its own runner, traces and retries. Every example on the Playwright CI page is `@playwright/test` with `npx playwright install --with-deps`, and "Caching browser binaries is not recommended" — [Playwright CI](https://playwright.dev/docs/ci). The Python port (`pytest-playwright` 0.10.0) would also be one toolchain. TS wins because tsc type-checks the specs and the UI selectors stay next to the UI code.

### 4. GitHub Actions

- Service containers need a Linux runner and accept `image`, `env`, `ports`, `credentials`, `command` and `entrypoint` — [GH service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services/use-docker-service-containers). The timescaledb image would work as a service (it is a postgres image with `POSTGRES_*` env), but compose in CI is simpler: there is one source of truth for images, flags and healthchecks, and the Redis AOF flags from R3 come along.
- Actions (latest releases): `actions/checkout@v7`, `astral-sh/setup-uv@v10` (with `enable-cache: true`), `actions/setup-node@v7` (with `cache: npm`), `actions/upload-artifact@v7` — GitHub releases API.
- **TypeScript 7.0.2 is `latest` on npm, but `typescript-eslint` 8.72.2 has the peer dependency `typescript >=4.8.4 <6.1.0`** (npm registry). Pin **TS 6.0.3**. That keeps eslint possible without a downgrade later. ENG-04 asks for "lint, mypy, tsc". Lint means ruff (Python) plus `tsc --noEmit` (strict). eslint is optional.

### 5. Health and logs

- The API exposes `/healthz`, which checks Redis `PING` and DB `SELECT 1` and returns 200 or 503.
- Workers have no HTTP stack. Each one runs a ~15-line `asyncio.start_server` on `HEALTH_PORT` that answers `GET /healthz` with 200 when the main loop iterated less than 10 s ago (body: `{status, loop_age_s, owned_vehicles}`). The compose healthcheck runs `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/healthz')"`, so curl is not needed in `python:3.14-slim`. This satisfies "`/healthz` per service" literally, which a heartbeat file would not.
- Logs: stdlib `logging` with a ~15-line JSON `Formatter` (one line per record: `ts` in UTC with `Z`, `level`, `service`, `msg`, and `vehicle`/`seq`/`entry_id` from `extra=`). This pattern is in the [logging cookbook › structured logging](https://docs.python.org/3/howto/logging-cookbook.html#implementing-structured-logging). structlog 26.1.0 and python-json-logger 4.2.0 would each add a dependency for the same few lines.

### 6. Login and identity

- Starlette `SessionMiddleware` (starlette 1.10.0, inspected) signs the cookie with `itsdangerous.TimestampSigner`. Its defaults are `max_age=1209600`, `same_site='lax'`, `https_only=False` and `httponly`. itsdangerous is only in starlette's `full` extra, so it must be added explicitly.
- Passwords: OWASP puts "Argon2id … 19 MiB … iteration count of 2" first, then "scrypt with a minimum CPU/memory cost parameter of (2^17), a minimum block size of 8 … parallelization parameter of 1" — [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). `hashlib.scrypt` is in the stdlib, so we need no argon2-cffi.
- CSRF: "SameSite is useful as a defense-in-depth control but it does not replace a proper CSRF defense". Requests with custom headers are "to be preflighted", so requiring one blocks cross-site forms. APIs that accept `text/plain` are CSRF-vulnerable — [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).
- JWT (PyJWT 2.15.1) is rejected. It needs a storage choice (a cookie brings back CSRF, while localStorage is readable by XSS), it has no logout without a denylist, and it is an extra dependency. A signed cookie is built in, and the browser sends it on the WebSocket handshake too.

### 7. Conventional commits (ENG-07)

`<type>: <description>` per [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/). This is already the user's git rule. Nothing to research.

## Decision candidate (R11)

**Chosen:**
- One Python package `gs` (`src/gs/{common,simulator,ingest,archiver,api}`) with `[project.scripts]` entries `gs-sim`, `gs-ingest`, `gs-archiver`, `gs-api` and `gs-migrate`.
- One Dockerfile with two stages: Node builds `ui/dist`, then a `python:3.14-slim` + uv stage copies it in. FastAPI serves the built UI with `StaticFiles`, so there is one origin, no CORS and no nginx.
- Compose with `restart: unless-stopped`, healthchecks and `depends_on` conditions.
- Integration tests and CI reuse the same compose file.
- Playwright E2E in TypeScript.
- Stdlib JSON logging and asyncio `/healthz`.
- Login: fixed users in `users.yaml` with scrypt hashes, a Starlette signed session cookie (`same_site="strict"`, `max_age=12h`), and CSRF middleware that rejects unsafe methods without the header `X-GS-Client: console` or with a non-JSON body. The WebSocket handshake checks `Origin`. The user always comes from the session and never from the request body (`issued_by`, `acked_by`, `conductor`).
- Config: env only, read into a frozen dataclass (`os.environ[...]`, which fails fast). `.env.example` is committed. `make up` creates `.env` with a random `SESSION_SECRET` if it is missing.

**Because:** this is the fewest moving parts that meets ENG-01..10. One image, one lock, one compose file serve local, CI and E2E alike.

**Rejected:**
- uv workspace: no conflicting requirements, so it is N× the config for nothing.
- testcontainers and GH `services:`: duplicate image pins.
- pyright: the spec names mypy.
- structlog: a dependency for 15 lines.
- JWT: see §6.
- nginx container: FastAPI serves the static files.
- pydantic-settings: about 10 lines of `os.environ`.
- TS 7: breaks typescript-eslint.
- Python 3.15: too fresh.

### Version pins

| Item | Pin | Note |
| --- | --- | --- |
| Python / image | 3.14 / `python:3.14-slim` | 3.15.2 skipped (1 month old) |
| uv / ruff / mypy | 0.13.8 / 0.17.5 / 2.4.1 | `mypy --strict`, pydantic plugin |
| pytest / pytest-asyncio | 9.2.0 / 1.5.0 | |
| fastapi / starlette / uvicorn | 0.162.6 / 1.10.0 (transitive) / 0.54.0 | FastAPI requires Python ≥ 3.11 |
| pydantic | 2.14.3 | probed |
| redis (redis-py) | 8.1.0 | = R3 |
| psycopg[binary] / psycopg-pool | 3.3.9 / 3.3.3 | = R6 |
| itsdangerous / pyyaml / httpx | 2.2.0 / 6.0.3 / 0.28.1 | httpx for TestClient |
| pyarrow / boto3 or minio | 26.0.1 / 1.44.2 or 7.2.20 | R7 decides the S3 client |
| Node / image | 24 LTS (24.22.3) / `node:24-alpine` | EOL 2028-04-30; Node 26 LTS only since 2026-10-28 |
| react / vite / @vitejs/plugin-react | 19.3.1 / 8.3.8 / 6.3.0 | |
| typescript | **6.0.3** (not 7.0.2) | typescript-eslint peer `<6.1.0` |
| @playwright/test | 1.65.0 | browsers via `npx playwright install --with-deps` |
| Redis / TimescaleDB | `redis:8.10.3` / `timescale/timescaledb:2.32.0-pg17` | = R3 / R6 |
| MinIO / mc | `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` / `pgsty/mc:RELEASE.2026-11-12T00-00-00Z` | upstream images removed |
| Actions | checkout@v7, setup-uv@v10, setup-node@v7, upload-artifact@v7 | |

### Draft compose service list

| Service | Image / command | depends_on | restart | healthcheck | host port |
| --- | --- | --- | --- | --- | --- |
| redis | `redis:8.10.3` + R3 AOF flags | – | unless-stopped | `redis-cli ping` | 127.0.0.1:6379 |
| db | `timescale/timescaledb:2.32.0-pg17` | – | unless-stopped | `pg_isready -U $POSTGRES_USER -d $POSTGRES_DB` | 127.0.0.1:5432 |
| minio | `pgsty/minio:…` `server /data --console-address :9001` | – | unless-stopped | none (see minio-init) | 9001 (console) |
| minio-init | `pgsty/mc:…` `until mc alias set s3 $S3_ENDPOINT …; do sleep 1; done; mc mb --ignore-existing s3/$S3_BUCKET` | minio started | no | – | – |
| migrate | app image `gs-migrate` (as `gs_owner`; creates `gs_app`) | db healthy | no | – | – |
| simulator | app `gs-sim` | redis healthy | unless-stopped | `/healthz` :8081 | – |
| ingest-1, ingest-2 | app `gs-ingest`, `CONSUMER_NAME=ingest-N` | redis healthy, migrate completed | unless-stopped | `/healthz` :8081 | – |
| archiver | app `gs-archiver` | db healthy, migrate + minio-init completed | unless-stopped | `/healthz` :8081 | – |
| api (+UI) | app `gs-api` | redis healthy, migrate completed | unless-stopped | `/healthz` :8000 | 8000 |

All app services share an `x-app: &app` anchor (`build: .`, `env_file: .env`). Hosts come only from env (`REDIS_URL=redis://redis:6379/0`, `DATABASE_URL=postgresql://gs_app:…@db:5432/gs`, `S3_ENDPOINT=http://minio:9000`). `make up` = `test -f .env || (cp .env.example .env && echo SESSION_SECRET=$$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))') >> .env); docker compose up -d --build --wait`.

### CI job graph (one workflow `ci.yml`, on `push` (branches + tags `v*`) and `pull_request`)

```
lint ─────────────┐   ruff check · ruff format --check · mypy --strict src tests · npm ci · tsc --noEmit (ui + e2e)
test ─────────────┤   compose up -d --wait redis db migrate · uv run pytest (unit + -m integration)
                  └─► e2e (needs: lint, test)
                       docker compose up -d --build --wait · npx playwright install --with-deps chromium
                       npx playwright test (leak→yellow→red→ack→clear, …) · on failure: compose logs + upload-artifact playwright-report
```
`lint` and `test` run in parallel, and `e2e` gates on both. The release evidence (§6) is this same workflow, green on the `v1.0.0` tag push.

## R13 – Frame validation (ING-02) and sessions (DAT-01)

### Findings

- **Probe** (pydantic 2.14.3, `model_validate_json`, `strict=True`, `allow_inf_nan=False`):
  - **Rejected:** `NaN`, `Infinity`, `1e400` (`finite_number`); seq `-1` and `2^32` (bounds); seq `"5"`, `5.0`, `true` (`int_type`); a naive time (`timezone_aware`); `+02:00` (our validator); UUID `"abc"` (`uuid_parsing`); truncated JSON and `not json` (`json_invalid`); `[]` (`model_type`).
  - **Accepted:** `101` for a float field (stored as 101.0); both `…Z` and `…+00:00`.
  - With `extra="allow"`, unknown keys appear in `model_extra` (`{'new_param': 3}`), so they can be counted. `extra="ignore"` would drop them silently.
- Strict mode lets date/time types accept strings from JSON ("the date and time types allow strings even in strict mode") — [pydantic strict mode](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/). The probe shows the same for UUID.
- A Redis stream entry ID is `<ms-time>-<seq>`, so the time Redis first received the entry can be derived from it — [Redis streams intro](https://redis.io/docs/latest/develop/data-types/streams/).
- A partial unique index "enforces uniqueness among the rows that satisfy the index predicate, without constraining those that do not" — [PG partial indexes, Ex. 11.3](https://www.postgresql.org/docs/current/indexes-partial.html). The sessions table is a plain table, not a hypertable, so the R6 rule that a unique index must include the time column does not apply.

### Decision candidate (R13)

**Chosen, frame:**
- Each stream entry carries one field, `data`, holding the JSON frame. Types survive the trip and "unparsable JSON" means something.
- `FrameBase` has the fixed §3 header fields. Parameter fields are generated from the registry at startup with `create_model`, each a finite `float`. Adding a parameter therefore needs no code change (ENG-06).
- Extra checks:
  - `vehicle` must equal the stream suffix;
  - `t_onboard` must lie in [now − 7 d, now + 5 min] (configurable). This catches the 1970 clock from the R6 risks without sending a legitimate post-outage backlog to the DLQ.
- Unknown fields: kept in `model_extra`, counted per name, warned once, not stored (A-R6-3).
- Invalid frames go to the DLQ at once, without retry (R3 pseudocode `bad`).

```python
U32 = Annotated[int, Field(ge=0, le=2**32 - 1)]
def _utc(v: datetime) -> datetime:
    if v.utcoffset() != timedelta(0): raise ValueError("t_onboard must be UTC")
    return v

class FrameBase(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False, frozen=True)
    vehicle: Annotated[str, Field(pattern=r"^[A-Z0-9-]{1,32}$")]
    seq: U32
    t_onboard: Annotated[AwareDatetime, AfterValidator(_utc)]
    mode: Literal["SAFE", "STANDBY", "TEST", "FAULT"]
    last_cmd_id: UUID | None                       # required key, value may be null

def build_frame_model(params: Sequence[str]) -> type[FrameBase]:   # from the registry / limits.yaml
    return create_model("Frame", __base__=FrameBase, **{p: (float, ...) for p in params})
```

**DLQ entry (`XADD tm:dlq`)** extends the R3 record. R3's `src`/`src_id`/`payload` are renamed `stream`/`entry_id`/`raw`.
`reason` ∈ `json_invalid | schema | vehicle_mismatch | t_onboard_range | poison` · `detail` (pydantic `errors()` as JSON, ≤ 2 KB) · `raw` (the original `data` bytes as received; if `data` is missing, a JSON dump of the entry's field map) · `stream` · `entry_id` · `deliveries` · `first_seen` (from the entry ID's ms, ISO with `Z`) · `dlq_at`. One `MULTI` writes `XADD tm:dlq` and `XACK` together, as in R3.

**Chosen, sessions:** the conductor is the logged-in user. A start or stop also writes an `audit_log` row. Frames are always stored, inside a session or not. Session membership is a time-window join, with no FK on telemetry.

```sql
CREATE TABLE test_session (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  vehicle    text        NOT NULL,
  name       text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  conductor  text        NOT NULL,                 -- from login session, never from the request body
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at   timestamptz,
  ended_by   text,
  CHECK (ended_at IS NULL OR ended_at > started_at),
  CHECK ((ended_at IS NULL) = (ended_by IS NULL))
);
CREATE UNIQUE INDEX test_session_one_active ON test_session (vehicle) WHERE ended_at IS NULL;
-- start: INSERT … → unique_violation ⇒ 409 "NYX-TA1 already in session 'Pressure test #4' (malek, since 10:02Z)"
-- stop:  UPDATE test_session SET ended_at = now(), ended_by = %(user)s
--        WHERE id = %(id)s AND ended_at IS NULL RETURNING *;   -- 0 rows ⇒ 409 already stopped
-- frames in session: WHERE vehicle = s.vehicle AND t_onboard >= s.started_at AND t_onboard < coalesce(s.ended_at, now())
GRANT SELECT, INSERT, UPDATE (ended_at, ended_by) ON test_session TO gs_app;   -- no DELETE
```

**Rejected:**
- Lax pydantic, which would turn `"5"` into 5 and hide simulator bugs.
- `extra="forbid"`, which would DLQ every frame once the simulator gets ahead of the config.
- Flat stream fields (all strings, so strict mode cannot type them).
- A `btree_gist` exclusion constraint on `tstzrange`. Only the open session can conflict, and starting at `now()` after the previous `ended_at` cannot overlap unless the clock steps back. A FK from telemetry to session would also break storage of out-of-session frames.

## Risks / what would make us switch

- **MinIO fork drift.** `pgsty/minio` stopped at 2026-08-04. Its successor `pgsty/silo` has no `minio` binary alias. If the pinned tag disappears, switch to `pgsty/silo` (change `command`) or RustFS (`rustfs/rustfs:1.0.5`, Apache-2, 23 M pulls). The S3 API code stays the same. The spec's "MinIO / S3 API" wording allows either, and the switch goes in README limitations.
- **`compose up --wait` combined with one-shot services** is **VERIFIED** (see above): it fails unless each one-shot has a `service_completed_successfully` dependent.
- **Unhealthy is not restarted.** Docker restarts on exit, not on a failed healthcheck (**VERIFIED**: test 2026-11-14, unhealthy for 15 s with 0 restarts; Docker docs tie restarts to exit only; moby#28400 still open). A hung worker is handled by R4's steal path. Optionally, a watchdog calls `os._exit(1)` when `loop_age > 30 s`, and the restart policy brings it back.
- **Session clock.** `started_at` is ground time (`now()`), while the window filter uses `t_onboard` (vehicle time). Skew shifts the edges. That is fine on the bench; with real hardware we would use `t_received`.
- **The fixed users file** has demo hashes committed and no lockout or rate limit. This is listed under Q14 "not production-ready", and company SSO is the next step (§1 out of scope).
- **Parameters required in every frame.** If the registry gets ahead of the simulator, every frame goes to the DLQ. The ENG-06 guide must update both together.

## Open questions → assumptions.md

1. **A-R11-1** (ASSUMED): a stream entry has one field `data` = JSON frame. This changes R3's flat-field `XADD` example.
2. **A-R11-2** (ASSUMED): DLQ fields follow the R13 record above (supersedes R3 `src/src_id/payload`).
3. **A-R11-3** (OPEN): a registered parameter missing from a frame → DLQ (`schema`) or store as NULL and count? Assumed DLQ (strict ING-02).
4. **A-R11-4** (ASSUMED): `t_onboard` validity window is [now − 7 d, now + 5 min]. Both `Z` and `+00:00` are accepted on input. Output is always `Z` (UI-08).
5. **A-R11-5** (OPEN): may any logged-in user start/stop sessions and send hazardous commands, or do we need roles (conductor vs controller)? Assumed no roles. The identity is recorded.
6. **A-R11-6** (ASSUMED): ATP-05 "kill a worker" = `docker compose kill ingest-1`, which tests takeover. Self-restart is shown separately with `kill -9 1` inside the container.
7. **A-R11-7** (ASSUMED): MinIO runs from the community fork `pgsty/minio` pinned. Upstream images are gone.


## Verification pass (2026-11-14)

Claims that were marked UNVERIFIED, checked against primary sources, source code or by test. Marked in place above.

| Claim | Verdict | Evidence | Touches |
| --- | --- | --- | --- |
| `up --wait` with one-shot services | VERIFIED (fails without a completed-successfully dependent) | S2 + retest + compose source | D-ENG-03 |
| `pgsty/minio` has curl / mc | VERIFIED (renamed pgsty/silo) | Dockerfile in repo | D-ARC-09 (alternative image) |
| (same, risk list) | VERIFIED | as above | D-ENG-03 |
| Unhealthy containers are not restarted | VERIFIED | test + docs | D-ING-26 watchdog, D-ING-12 |

CONTRADICTED: none.
