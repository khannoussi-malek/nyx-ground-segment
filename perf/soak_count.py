"""ATP-01 soak (ING-01, D-ALM-15): sessions on NYX-TA1 and NYX-TA2 for `--hours`, then lost frames
(sent = the tm:<vehicle> stream, stored = telemetry, per epoch and seq), GAP events, simulator
overflow and alarm quality over the window, written to docs/perf/soak-<UTC date>/summary.md.
Nothing is injected, so every RAISE is a false raise. Frames the simulator backfills more than
60 s after a session's ended_at are not counted.
`make soak` = `uv run python -m perf.soak_count --hours 2`."""

import argparse
import http.cookiejar
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

import psycopg
import redis

from gs.alarms.quality import QEvent, Quality, quality
from perf.run_n import REPO, VEHICLES, read_env

log = logging.getLogger(__name__)
FREEZE_WAIT_S = 180
STORED_SQL = (
    "SELECT t_onboard, seq FROM telemetry WHERE vehicle = %s AND t_onboard >= %s AND t_onboard < %s"
)
GAP_SQL = (
    "SELECT count(*) FROM events WHERE vehicle = %s AND kind = 'GAP'"
    " AND t_onboard >= %s AND t_onboard < %s"
)
RESET_SQL = GAP_SQL.replace("'GAP'", "'SEQ_RESET'")
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
    epochs: int = 1  # seq runs the simulator sent in the window (a restart starts a new one)
    resets: int = 0  # SEQ_RESET events in the window


Frame = tuple[datetime, int]  # (t_onboard, seq): one frame as the simulator sent it
STREAM_MARGIN_MS, STREAM_PAGE = 60_000, 10_000


def lost_by_epoch(rows: Sequence[tuple[datetime, int, int, int]]) -> int:
    """Per (epoch, first sent seq, last sent seq, stored): sent frames the table does not hold,
    head and tail of an epoch and across a reset included (the GAP column is not consulted)."""
    return sum(last - first + 1 - count for _, first, last, count in rows)


def epoch_rows(
    sent: Iterable[Frame], stored: Collection[Frame]
) -> list[tuple[datetime, int, int, int]]:
    """(epoch, first sent seq, last sent seq, stored) per sent epoch: a run of rising seq, a drop
    starts the next (the tracker's SEQ_RESET / SEQ_ANOMALY). `sent` is in send order (the stream's);
    never re-sorted by t_onboard, which can step back with the clock. A sender retry is one
    frame."""
    runs: list[list[Frame]] = []
    for f in dict.fromkeys(sent):
        if runs and f[1] > runs[-1][-1][1]:
            runs[-1].append(f)
        else:
            runs.append([f])
    return [(r[0][0], r[0][1], r[-1][1], sum(f in stored for f in r)) for r in runs]


def incomplete(counts: Sequence[VehicleCount]) -> bool:
    """G4-20: the simulator restarted during the soak (its overflow counter restarted too), so
    the run proves nothing either way (ATP-01 step 4)."""
    return any(c.epochs > 1 or c.resets > 0 for c in counts)


def passed(counts: Sequence[VehicleCount], q: Quality, overflow: int = 0) -> bool:
    return (
        all(c.lost == 0 for c in counts)
        and overflow == 0  # the simulator dropped a frame before it reached the stream
        and q.false_raises == 0  # None (unjudged) is not a pass
        and not q.chattering
    )


def render(
    day: datetime, hours: float, counts: Sequence[VehicleCount], overflow: int, q: Quality
) -> str:
    rows = "".join(
        f"| {c.vehicle} | {c.session_id} | {c.frames} | {c.lost} | {c.gaps} "
        f"| {c.epochs} | {c.resets} |\n"
        for c in counts
    )
    verdict = (
        "**INCOMPLETE** (simulator restarted during the soak, rerun)" if incomplete(counts)
        else "**PASS**" if passed(counts, q, overflow) else "**FAIL**"
    )  # fmt: skip
    return (
        f"# ATP-01 soak {day:%Y-%m-%d} (ING-01, D-ALM-15)\n\n"
        f"{hours:g} h, sessions on {', '.join(c.vehicle for c in counts)}, nothing injected "
        "(every RAISE is a false raise). Reports: `report-<vehicle>.csv`.\n\n"
        "| Vehicle | Session | Frames stored | Lost (sent - stored) | GAP events | Epochs "
        "| SEQ_RESET |\n"
        f"|---|---|---|---|---|---|---|\n{rows}\n"
        f"- Simulator overflow (frames dropped during the soak): {overflow}\n"
        f"- RAISEs: {q.raises}, false raises: {q.false_raises}\n"
        f"- Chattering (psi >= 0.05 or alarm < 20 s): {', '.join(q.chattering) or 'none'}\n"
        f"- Peak new alarms in 10 min: {dict(q.peak_10min)}, time in flood: "
        f"{q.time_in_flood_s:.0f} s\n\n"
        f"Verdict (1 epoch, 0 lost, 0 overflow, 0 false raises, no chattering): {verdict}\n"
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


def stored_frames(db: psycopg.Connection, v: str, start: datetime, end: datetime) -> set[Frame]:
    return {(r[0], int(r[1])) for r in db.execute(STORED_SQL, (v, start, end))}


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def sent_frames(r: redis.Redis, v: str, start: datetime, end: datetime) -> list[Frame]:
    """Frames the simulator XADDed to tm:<v> with t_onboard in [start, end), read back from the
    stream (the broker record of what was sent, read as admin, ingest has no XRANGE;
    360k entries are kept, 10 h at 10 Hz), in stream order: entry IDs only rise, so it is the send
    order even when t_onboard steps back."""
    key = f"tm:{v}"
    info = r.xinfo_stream(key)
    first_ms = (
        int(str(info["first-entry"][0]).split("-")[0]) if info["first-entry"] else sys.maxsize
    )
    if info["entries-added"] > info["length"] and first_ms > _ms(start) - STREAM_MARGIN_MS:
        # entries were trimmed and the oldest left is newer than the window's start: sent would be
        # undercounted (a silent pass), so stop here
        raise RuntimeError(f"{key} was trimmed past the soak window start: head lost")
    out: dict[Frame, None] = {}  # ordered, a retry once
    lo = str(_ms(start) - STREAM_MARGIN_MS)
    hi = str(_ms(end) + STREAM_MARGIN_MS)
    while page := cast(list[Any], r.xrange(f"tm:{v}", lo, hi, count=STREAM_PAGE)):
        for _, fields in page:
            try:
                f = json.loads(fields["data"])
                t = datetime.fromisoformat(f["t_onboard"])
                seq = int(f["seq"])
            except KeyError, ValueError, TypeError:
                log.warning("tm:%s entry is not a frame, ignored", v)  # the ingest DLQs these
                continue
            if start <= t < end:
                out[(t, seq)] = None
        lo = "(" + str(page[-1][0])
    return list(out)


def _count(
    db: psycopg.Connection, r: redis.Redis, v: str, sid: int, start: datetime, end: datetime
) -> VehicleCount:
    stored = stored_frames(db, v, start, end)
    sent = sent_frames(r, v, start, end)
    lost = lost_by_epoch(epochs := epoch_rows(sent, stored))
    gaps, resets = (db.execute(sql, (v, start, end)).fetchone() for sql in (GAP_SQL, RESET_SQL))
    assert gaps is not None and resets is not None
    return VehicleCount(v, sid, len(stored), lost, int(gaps[0]), len(epochs), int(resets[0]))


def _start(api: Api, vehicle: str) -> int:
    body = {"vehicle": vehicle, "name": f"ATP-01 soak {vehicle}"}
    return int(json.loads(api.call("POST", "/api/sessions", body))["id"])


def run_sessions(
    api: Api, vehicles: Sequence[str], wait: Callable[[], None]
) -> tuple[dict[str, int], dict[str, Any]]:
    """Start a session per vehicle, wait, stop them all. A failed start or an interrupted wait
    still stops what was started (else the next run hits SESSION_ACTIVE); every stop is attempted
    and the failures are reported together."""
    ids: dict[str, int] = {}
    ended: dict[str, Any] = {}
    try:
        for v in vehicles:
            ids[v] = _start(api, v)
        wait()
    finally:
        errors = []
        for v, i in ids.items():
            try:
                ended[v] = json.loads(api.call("POST", f"/api/sessions/{i}/stop"))
            except Exception as e:  # noqa: BLE001 - collected, reported below
                log.exception("stopping session %s (%s) failed", i, v)
                errors.append(f"{v} session {i}: {type(e).__name__}: {e}")
        if errors:
            raise RuntimeError("could not stop: " + "; ".join(errors))
    return ids, ended


def sim_overflow(port: str) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
        return int(json.loads(r.read())["checks"]["overflow"])


def main(
    hours: float,
    *,
    api: Api | None = None,
    vehicles: Sequence[str] = VEHICLES,
    out: Path | None = None,
    wait: Callable[[], None] | None = None,
    overflow: Callable[[], int] | None = None,
) -> None:
    """The keyword arguments are the edges tests replace (HTTP, the soak's sleep, the simulator)."""
    env = read_env()
    api = api or Api(env["API_HOST_PORT"], env["DEMO_PW_CONDUCTOR"])
    day = datetime.now(UTC)
    read_overflow = overflow or (lambda: sim_overflow(env["SIM_HOST_PORT"]))
    ovf_start = read_overflow()  # the simulator's counter is since its start, not the soak's
    ids, ended = run_sessions(api, vehicles, wait or (lambda: time.sleep(hours * 3600)))
    ovf = read_overflow() - ovf_start
    out = out or REPO / "docs" / "perf" / f"soak-{day:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    for v, i in ids.items():
        (out / f"report-{v}.csv").write_text(api.csv_when_frozen(i))
    win = {v: (datetime.fromisoformat(s["started_at"]), datetime.fromisoformat(s["ended_at"]))
           for v, s in ended.items()}  # fmt: skip
    dsn = f"postgresql://gs_app:{env['GS_APP_PASSWORD']}@127.0.0.1:{env['DB_HOST_PORT']}/gs"
    r = redis.Redis(
        host="127.0.0.1", port=int(env["REDIS_HOST_PORT"]), username="admin",
        password=env["REDIS_ADMIN_PW"], decode_responses=True,
    )  # fmt: skip
    with psycopg.connect(dsn, autocommit=True) as db:
        counts = [_count(db, r, v, ids[v], *win[v]) for v in vehicles]
        start, end = min(w[0] for w in win.values()), max(w[1] for w in win.values())
        events = [QEvent(*row) for row in db.execute(ALARM_SQL, (list(vehicles), start, end))]
    q = quality(events, fault_windows=[])  # nothing injected: every RAISE is false
    text = render(day, hours, counts, ovf, q)
    (out / "summary.md").write_text(text)
    print(text)
    if incomplete(counts):
        sys.exit("soak: ATP-01 INCOMPLETE (simulator restart), rerun")
    if not passed(counts, q, ovf):
        sys.exit("soak: ATP-01 FAIL")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, required=True)
    main(ap.parse_args().hours)
