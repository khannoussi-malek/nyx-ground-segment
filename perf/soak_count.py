"""ATP-01 soak (ING-01, D-ALM-15): sessions on NYX-TA1 and NYX-TA2 for `--hours`, then lost frames
per (epoch, seq), GAP events, simulator overflow and alarm quality over the window, written to
docs/perf/soak-<UTC date>/summary.md. Nothing is injected, so every RAISE is a false raise.
`make soak` = `uv run python -m perf.soak_count --hours 2`."""

import argparse
import http.cookiejar
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, NamedTuple

import psycopg

from gs.alarms.quality import QEvent, Quality, quality
from perf.run_n import REPO, VEHICLES, read_env

log = logging.getLogger(__name__)
FREEZE_WAIT_S = 180
EPOCH_SQL = (
    "WITH ep AS (SELECT %(start)s::timestamptz AS e UNION"
    " SELECT (details->>'epoch')::timestamptz FROM events WHERE vehicle = %(v)s"
    " AND kind = 'SEQ_RESET' AND t_onboard >= %(start)s AND t_onboard < %(end)s)"
    " SELECT e, min(seq), max(seq), count(*) FROM (SELECT seq,"
    " (SELECT max(e) FROM ep WHERE e <= t_onboard) AS e FROM telemetry WHERE vehicle = %(v)s"
    " AND t_onboard >= %(start)s AND t_onboard < %(end)s) t GROUP BY e"
)  # the epoch of a stored row = the latest SEQ_RESET epoch before it (the tracker's rule)
GAP_SQL = (
    "SELECT count(*) FROM events WHERE vehicle = %s AND kind = 'GAP'"
    " AND t_onboard >= %s AND t_onboard < %s"
)
ALARM_SQL = (
    "SELECT vehicle, param, kind, coalesce(trigger_t_onboard, processed_at), alarm_id"
    " FROM alarm_events WHERE alarm_id IS NOT NULL AND vehicle = ANY(%s)"
    " AND processed_at >= %s AND processed_at < %s ORDER BY id"
)


class VehicleCount(NamedTuple):
    vehicle: str
    session_id: int
    frames: int  # stored in the window
    lost: int  # by (epoch, seq)
    gaps: int  # GAP events


def lost_by_epoch(rows: Sequence[tuple[datetime, int, int, int]]) -> int:
    """Per (epoch, first_seq, last_seq, count): frames the sequence range holds but the table
    does not. Frames lost before an epoch's first stored frame show up as GAP events instead."""
    return sum(last - first + 1 - count for _, first, last, count in rows)


def passed(counts: Sequence[VehicleCount], q: Quality) -> bool:
    return (
        all(c.lost == 0 for c in counts)
        and q.false_raises == 0  # None (unjudged) is not a pass
        and not q.chattering
    )


def render(day: datetime, hours: float, counts: Sequence[VehicleCount], overflow: int,
           q: Quality) -> str:  # fmt: skip
    rows = "".join(
        f"| {c.vehicle} | {c.session_id} | {c.frames} | {c.lost} | {c.gaps} |\n" for c in counts
    )
    return (
        f"# ATP-01 soak {day:%Y-%m-%d} (ING-01, D-ALM-15)\n\n"
        f"{hours:g} h, sessions on {', '.join(c.vehicle for c in counts)}, nothing injected "
        "(every RAISE is a false raise). Reports: `report-<vehicle>.csv`.\n\n"
        "| Vehicle | Session | Frames stored | Lost by (epoch, seq) | GAP events |\n"
        f"|---|---|---|---|---|\n{rows}\n"
        f"- Simulator overflow: {overflow}\n"
        f"- RAISEs: {q.raises}, false raises: {q.false_raises}\n"
        f"- Chattering (psi >= 0.05 or alarm < 20 s): {', '.join(q.chattering) or 'none'}\n"
        f"- Peak new alarms in 10 min: {dict(q.peak_10min)}, time in flood: "
        f"{q.time_in_flood_s:.0f} s\n\n"
        f"Verdict (0 lost, 0 false raises, no chattering): "
        f"{'**PASS**' if passed(counts, q) else '**FAIL**'}\n"
    )


class Api:
    def __init__(self, port: str, password: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.call("POST", "/api/login", {"user": "conductor", "password": password})

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> bytes:
        req = urllib.request.Request(
            self.base + path, json.dumps(body).encode() if body is not None else None,
            {"content-type": "application/json", "X-GS-Client": "console"}, method=method,
        )  # fmt: skip
        with self.opener.open(req, timeout=30) as r:
            data: bytes = r.read()
        return data

    def csv_when_frozen(self, session_id: int) -> str:
        deadline = time.monotonic() + FREEZE_WAIT_S
        while True:
            try:
                return self.call("GET", f"/api/sessions/{session_id}/report.csv").decode()
            except urllib.error.HTTPError as e:
                if e.code != 409 or time.monotonic() > deadline:
                    raise RuntimeError(f"report {session_id} not frozen: HTTP {e.code}") from e
                time.sleep(5)  # 409: the freeze waits for the watermark or FREEZE_AFTER_S


def _count(
    db: psycopg.Connection, v: str, sid: int, start: datetime, end: datetime
) -> VehicleCount:
    args = {"v": v, "start": start, "end": end}
    rows = db.execute(EPOCH_SQL, args).fetchall()
    gaps = db.execute(GAP_SQL, (v, start, end)).fetchone()
    assert gaps is not None
    return VehicleCount(v, sid, sum(r[3] for r in rows), lost_by_epoch(rows), int(gaps[0]))


def _start(api: Api, vehicle: str) -> int:
    body = {"vehicle": vehicle, "name": f"ATP-01 soak {vehicle}"}
    return int(json.loads(api.call("POST", "/api/sessions", body))["id"])


def main(hours: float) -> None:
    env = read_env()
    api = Api(env["API_HOST_PORT"], env["DEMO_PW_CONDUCTOR"])
    day = datetime.now(UTC)
    ids = {v: _start(api, v) for v in VEHICLES}
    ended: dict[str, Any] = {}
    try:
        time.sleep(hours * 3600)
    finally:  # a soak that dies still ends its sessions, or the next start hits SESSION_ACTIVE
        for v, i in ids.items():
            try:
                ended[v] = json.loads(api.call("POST", f"/api/sessions/{i}/stop"))
            except Exception:
                log.exception("stopping session %s (%s) failed", i, v)
                raise
    out = REPO / "docs" / "perf" / f"soak-{day:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    for v, i in ids.items():
        (out / f"report-{v}.csv").write_text(api.csv_when_frozen(i))
    start = min(datetime.fromisoformat(s["started_at"]) for s in ended.values())
    end = max(datetime.fromisoformat(s["ended_at"]) for s in ended.values())
    dsn = f"postgresql://gs_app:{env['GS_APP_PASSWORD']}@127.0.0.1:{env['DB_HOST_PORT']}/gs"
    with psycopg.connect(dsn, autocommit=True) as db:
        counts = [
            _count(db, v, ids[v], datetime.fromisoformat(ended[v]["started_at"]),
                   datetime.fromisoformat(ended[v]["ended_at"]))
            for v in VEHICLES
        ]  # fmt: skip
        events = [QEvent(*r) for r in db.execute(ALARM_SQL, (list(VEHICLES), start, end))]
    health = json.loads(
        urllib.request.urlopen(f"http://127.0.0.1:{env['SIM_HOST_PORT']}/healthz", timeout=5).read()
    )
    overflow = int(health["checks"]["overflow"])
    q = quality(events, fault_windows=[])  # nothing injected: every RAISE is false
    text = render(day, hours, counts, overflow, q)
    (out / "summary.md").write_text(text)
    print(text)
    if not passed(counts, q):
        sys.exit("soak: ATP-01 FAIL")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, required=True)
    main(ap.parse_args().hours)
