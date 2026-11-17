"""The compose archiver's /healthz turns degraded on an unresolved ARCHIVE_CORRUPT (it needs the
running archiver container: the CI test job has none, so this lives with the system tests)."""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from gs.archiver.hours import hour_of
from gs.archiver.job import corrupt_key
from tests.support.compose import compose
from tests.support.env import vid
from tests.support.pg import admin_dsn


def test_archiver_healthz_degraded_on_archive_corrupt() -> None:
    def read() -> dict[str, Any]:
        out = compose(
            "exec", "-T", "archiver", "python", "-c",
            "import urllib.request; print(urllib.request.urlopen("
            "'http://127.0.0.1:8081/healthz').read().decode())",
            check=False,
        ).stdout  # fmt: skip
        return dict(json.loads(out)) if out.strip() else {}

    def corrupt_count(body: dict[str, Any]) -> int:
        scrub = str(body.get("checks", {}).get("scrub", "ok"))
        return 0 if scrub == "ok" else int(scrub.split()[0])

    before = corrupt_count(read())
    v = vid()
    h = hour_of(datetime.now(UTC)) - timedelta(hours=40)
    with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
        c.execute(
            "insert into archive_object (vehicle, hour, sha256, rows, t_min, t_max, verified_at)"
            " values (%s, %s, 'abc', 1, %s, %s, now())", (v, h, h, h),
        )  # fmt: skip
        c.execute(
            "insert into events (key, vehicle, kind, details) values (%s, %s, 'ARCHIVE_CORRUPT',"
            " %s::jsonb) on conflict (key) do nothing",
            (corrupt_key(v, h, 0), v, json.dumps({"hour": h.isoformat(), "expected": "abc"})),
        )
    try:
        body: dict[str, Any] = {}
        for _ in range(20):
            body = read()
            if corrupt_count(body) == before + 1:
                break
            time.sleep(1)
        assert body["status"] == "degraded"
        assert corrupt_count(body) == before + 1
        assert body["checks"]["scrub"] == f"{before + 1} corrupt"
    finally:
        with psycopg.connect(admin_dsn("gs"), autocommit=True) as c:
            for t in ("events", "archive_object"):
                c.execute(f"delete from {t} where vehicle = %s", (v,))  # noqa: S608
