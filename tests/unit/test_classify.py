import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gs.common.interfaces import DlqRecord, FrameHeader, build_frame_model
from gs.common.missiondb import load_dir
from gs.ingest.validate import FieldStats, classify, entry_time

PARAMS = load_dir(Path("mission")).param_names
Frame = build_frame_model(PARAMS)
NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
EID = "1794650400000-0"
STREAM = "tm:NYX-TA1"


def frame_json(**over: object) -> bytes:
    base = {
        "vehicle": "NYX-TA1",
        "seq": 5,
        "t_onboard": "2026-11-14T10:00:00Z",
        "mode": "TEST",
        "last_cmd_id": None,
        "cabin_pressure_kpa": 101.3,
    }
    return json.dumps({**base, **over}).encode()


GOOD = frame_json()


def rec(data: bytes, stream: str = STREAM) -> DlqRecord:
    r = classify({b"data": data}, stream, EID, 1, Frame, NOW)
    assert isinstance(r, DlqRecord)
    return r


def test_valid_frame() -> None:
    f = classify({b"data": GOOD}, STREAM, EID, 1, Frame, NOW)
    assert isinstance(f, FrameHeader) and f.seq == 5


def test_missing_data_field_is_json_invalid() -> None:
    r = classify({b"seq": b"5"}, STREAM, EID, 1, Frame, NOW)
    assert isinstance(r, DlqRecord)
    assert (r.reason, json.loads(r.raw)) == ("json_invalid", {"seq": "5"})


def test_broken_json_keeps_raw() -> None:
    r = rec(b'{"vehicle":')
    assert (r.reason, r.raw) == ("json_invalid", '{"vehicle":')


def test_non_utf8_raw_is_backslash_escaped() -> None:
    r = rec(b'{"a": "\xff')
    assert (r.reason, r.raw) == ("json_invalid", '{"a": "\\xff')


def test_schema_detail_is_bounded_json() -> None:
    r = rec(frame_json(seq="5"))
    assert r.reason == "schema"
    detail = json.loads(r.detail)
    assert detail[0]["type"] == "int_type" and len(r.detail) <= 2048
    assert set(detail[0]) == {"type", "loc", "msg"}  # no input/url/ctx


def test_detail_truncated_to_2048() -> None:
    names = [f"p{i}" for i in range(100)]
    bad = json.loads(GOOD) | {n: "x" for n in names}
    r = classify({b"data": json.dumps(bad).encode()}, STREAM, EID, 1, build_frame_model(names), NOW)
    assert isinstance(r, DlqRecord) and r.reason == "schema" and len(r.detail) == 2048


def test_vehicle_mismatch() -> None:
    assert rec(GOOD, "tm:NYX-TA2").reason == "vehicle_mismatch"


def test_t_onboard_range() -> None:
    old = (NOW - timedelta(days=8)).isoformat()
    assert rec(frame_json(t_onboard=old)).reason == "t_onboard_range"


def test_precedence_vehicle_over_schema() -> None:
    assert rec(frame_json(seq="5"), "tm:NYX-TA2").reason == "vehicle_mismatch"


def test_precedence_vehicle_over_t_onboard() -> None:
    old = (NOW - timedelta(days=8)).isoformat()
    assert rec(frame_json(t_onboard=old), "tm:NYX-TA2").reason == "vehicle_mismatch"


def test_record_fields() -> None:
    r = classify({b"data": b"x"}, STREAM, EID, 2, Frame, NOW)
    assert isinstance(r, DlqRecord)
    assert (r.stream, r.entry_id, r.deliveries, r.first_seen, r.dlq_at) == (
        STREAM,
        EID,
        2,
        NOW,
        NOW,
    )


def test_entry_time_uses_ms_part() -> None:
    assert entry_time("1794650400123-7") == datetime(2026, 11, 14, 10, 0, 0, 123000, tzinfo=UTC)


def test_unknown_field_counted_warned_once(caplog: pytest.LogCaptureFixture) -> None:
    stats = FieldStats()
    f = classify({b"data": frame_json(new_param=1.5)}, STREAM, EID, 1, Frame, NOW)
    assert isinstance(f, FrameHeader)
    with caplog.at_level(logging.WARNING):
        stats.observe(f, PARAMS)
        stats.observe(f, PARAMS)
    assert stats.unknown["new_param"] == 2
    assert len([r for r in caplog.records if "new_param" in r.getMessage()]) == 1


def test_missing_param_counted(caplog: pytest.LogCaptureFixture) -> None:
    stats = FieldStats()
    f = classify({b"data": GOOD}, STREAM, EID, 1, Frame, NOW)
    assert isinstance(f, FrameHeader)
    with caplog.at_level(logging.WARNING):
        stats.observe(f, PARAMS)
        stats.observe(f, PARAMS)
    assert stats.missing["tank_pressure_bar"] == 2
    assert "cabin_pressure_kpa" not in stats.missing and not stats.unknown
    assert len([r for r in caplog.records if "tank_pressure_bar" in r.getMessage()]) == 1
