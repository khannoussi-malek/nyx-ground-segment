import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from gs.api.bodies import AlarmQuality, AlarmTransition, ParamStats, SessionReport, SessionView
from gs.api.report import report_csv
from gs.common.interfaces.sse import CommandView, EventView, TransitionView

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _report() -> SessionReport:
    s = SessionView(
        id=7, vehicle="NYX-TA1", name="pass 1", conductor="fc1",
        started_at=T0, ended_at=T0 + timedelta(seconds=10), ended_by="fc2",
    )  # fmt: skip
    cmd = CommandView(
        cmd_id=uuid4(), vehicle="NYX-TA1", name="isolate_valve", args={"valve": "A"},
        hazardous=False, reason=None, issued_by="fc1", state="VERIFIED", failure_reason=None,
        queued_at=T0 + timedelta(seconds=1), sent_at=None, received_at=None, verified_at=None,
        failed_at=None, late_verified_at=None, late_ack_at=None, late_ack_accepted=None,
        outcome=None,
    )  # fmt: skip
    return SessionReport(
        session=s, complete=False, note="incomplete: 50 of 100 frames", frozen_at=T0,
        duration_s=10.0, frames=50, expected=100,
        parameters={
            "p_tank": ParamStats(unit="kPa", min=1.0, max=3.0, avg=2.0, n=50),
            "p_none": ParamStats(unit="K", min=None, max=None, avg=None, n=0),
        },
        link_events=[
            EventView(
                key="GAP/x/1", vehicle="NYX-TA1", kind="GAP", t_onboard=T0,
                details={"lost": 100},
            ),
            EventView(key="LOS/x", vehicle="NYX-TA1", kind="LOS", t_onboard=None, details={}),
        ],
        alarms=[
            AlarmTransition(
                alarm_id=uuid4(), param="p_tank",
                transition=TransitionView(
                    key="k", kind="RAISE", t=T0, alert=True, by_user="system", note=None,
                    check_kind="limit", level="RED_HIGH", value=9.5,
                ),
            )
        ],
        commands=[cmd],
    )  # fmt: skip


def test_sections_and_z_times() -> None:
    rows = list(csv.DictReader(io.StringIO(report_csv(_report()))))
    sections = [r["section"] for r in rows]
    assert sections == [
        "summary", "parameter", "parameter", "link_event", "link_event", "alarm", "command",
    ]  # fmt: skip
    for r in rows:
        for col in ("time", "end_time"):
            assert r[col] == "" or r[col].endswith("Z"), (r["section"], col, r[col])
    summary = rows[0]
    assert (summary["time"], summary["end_time"]) == (
        "2026-11-15T12:00:00.000000Z", "2026-11-15T12:00:10.000000Z",
    )  # fmt: skip
    assert (summary["n"], summary["expected"], summary["note"]) == (
        "50", "100", "incomplete: 50 of 100 frames",
    )  # fmt: skip


def test_none_is_an_empty_field() -> None:
    rows = list(csv.DictReader(io.StringIO(report_csv(_report()))))
    empty = next(r for r in rows if r["key"] == "p_none")
    assert (empty["min"], empty["max"], empty["avg"], empty["n"]) == ("", "", "", "0")
    los = next(r for r in rows if r["key"] == "LOS/x")
    assert los["time"] == ""
    alarm = next(r for r in rows if r["section"] == "alarm")
    assert (alarm["name"], alarm["kind"], alarm["level"], alarm["value"]) == (
        "p_tank", "RAISE", "RED_HIGH", "9.5",
    )  # fmt: skip


def test_summary_has_frozen_at_and_duration() -> None:
    summary = next(csv.DictReader(io.StringIO(report_csv(_report()))))
    assert (summary["frozen_at"], summary["duration_s"]) == ("2026-11-15T12:00:00.000000Z", "10.0")


def test_formula_cells_are_quoted_but_numbers_are_not() -> None:
    r = _report()
    named = r.session.model_copy(update={"name": "=HYPERLINK(1)", "conductor": "+cmd"})
    neg = ParamStats(unit="-x", min=-5.0, max=-1.0, avg=-3.0, n=2)
    alarm = r.alarms[0].model_copy(
        update={
            "transition": r.alarms[0].transition.model_copy(update={"note": "@sum", "value": -9.5})
        }
    )
    rows = list(
        csv.DictReader(
            io.StringIO(
                report_csv(
                    r.model_copy(
                        update={"session": named, "parameters": {"p": neg}, "alarms": [alarm]}
                    )
                )
            )
        )
    )
    summary, param = rows[0], rows[1]
    assert (summary["name"], summary["by_user"]) == ("'=HYPERLINK(1)", "'+cmd")
    assert (param["min"], param["max"], param["avg"]) == ("-5.0", "-1.0", "-3.0")  # numbers stay
    assert param["unit"] == "'-x"
    al = next(x for x in rows if x["section"] == "alarm")
    assert (al["note"], al["value"]) == ("'@sum", "-9.5")


def test_alarm_quality_rows() -> None:
    r = _report().model_copy(
        update={
            "alarm_quality": AlarmQuality(
                raises=3,
                false_raises=None,
                psi={"NYX-TA1/p_tank": 0.25},
                chattering=["NYX-TA1/p_tank"],
                short_t1=2,
                short_t0=1,
                peak_10min={"all": 3, "NYX-TA1": 3},
                time_in_flood_s=0.0,
            )
        }
    )
    rows = list(csv.DictReader(io.StringIO(report_csv(r))))
    metrics = {x["key"]: x["value"] for x in rows if x["section"] == "alarm_quality"}
    assert metrics == {
        "raises": "3",
        "false_raises": "",
        "short_t1": "2",
        "short_t0": "1",
        "time_in_flood_s": "0.0",
        "peak_10min:all": "3",
        "peak_10min:NYX-TA1": "3",
        "psi:NYX-TA1/p_tank": "0.25",
    }
