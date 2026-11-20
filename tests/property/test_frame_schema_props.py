"""P7 (design 11.5, D-EXT-14, R17 F3): frames generated from the exported per-mission frame schema
(D-EXT-19) are accepted by the validator; the same frames broken against the schema go to the DLQ
with a reason; the validator never raises out of the frame path (D-ING-05, D-ING-18)."""

import json
import warnings
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from hypothesis import assume, given
from hypothesis import strategies as st
from hypothesis_jsonschema import from_schema
from jsonschema import Draft202012Validator

from gs.common.interfaces import DlqRecord, FrameHeader, build_frame_model, frame_schema
from gs.common.interfaces.frame import T_ONBOARD_FUTURE, T_ONBOARD_PAST
from gs.common.missiondb import load_dir
from gs.common.time import iso_z
from gs.ingest.validate import classify
from tests.property import profiles  # noqa: F401  (registers and loads the profiles)
from tests.support.mission import MISSION_DIR

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
EID = "1794650400000-0"
# D-EXT-18: the validity window is a rule JSON Schema can't express, so `date-time` is drawn in it
IN_WINDOW = st.datetimes(
    min_value=(NOW - T_ONBOARD_PAST).replace(tzinfo=None),
    max_value=(NOW + T_ONBOARD_FUTURE).replace(tzinfo=None),
    timezones=st.just(UTC),
).map(iso_z)
UUIDS = st.uuids().map(str)


@lru_cache(maxsize=1)
def _model() -> type[FrameHeader]:
    return build_frame_model(load_dir(MISSION_DIR).param_names)


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return frame_schema(load_dir(MISSION_DIR).param_names)  # the function the route calls


def _frames() -> st.SearchStrategy[Any]:
    with warnings.catch_warnings():  # overriding the library's date-time is the point
        warnings.simplefilter("ignore")
        return from_schema(_schema(), custom_formats={"date-time": IN_WINDOW, "uuid": UUIDS})


def _classify(frame: object) -> FrameHeader | DlqRecord:
    """Raw entry as the stream holds it; the stream name carries the frame's own vehicle (a
    mismatch with the stream is a context rule, not a schema rule)."""
    vehicle = frame.get("vehicle") if isinstance(frame, dict) else None
    stream = f"tm:{vehicle if isinstance(vehicle, str) else 'NYX-TA1'}"
    raw = json.dumps(frame).encode()
    return classify({b"data": raw}, stream, EID, 1, _model(), NOW)


# each one violates the schema for any frame (a required key, a type, an enum, a range, a pattern)
_BREAKS: tuple[Callable[[dict[str, Any], str], dict[str, Any]], ...] = (
    lambda f, k: {a: b for a, b in f.items() if a != k},
    lambda f, k: {**f, k: "5"},
    lambda f, k: {**f, k: [f[k]]},
    lambda f, k: {**f, "seq": -1},
    lambda f, k: {**f, "seq": 2**32},
    lambda f, k: {**f, "mode": "BOGUS"},
    lambda f, k: {**f, "vehicle": "not a vehicle id"},
)
_REQUIRED = ("vehicle", "seq", "t_onboard", "mode", "last_cmd_id")


@given(_frames(), st.sampled_from(_BREAKS), st.sampled_from(_REQUIRED))
def test_p7_schema_frames_stored_or_dlqd(
    frame: dict[str, Any], brk: Callable[[dict[str, Any], str], dict[str, Any]], key: str
) -> None:
    got = _classify(frame)
    assert isinstance(got, FrameHeader), got  # every schema-valid frame is stored
    broken = brk(frame, key)
    assume(not Draft202012Validator(_schema()).is_valid(broken))
    dlq = _classify(broken)
    assert isinstance(dlq, DlqRecord), dlq  # the same frame broken reaches the DLQ with a reason
    assert dlq.reason and dlq.detail


_JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=8), c, max_size=4),
    max_leaves=12,
)


@given(st.one_of(_JSON, _frames(), st.binary()), st.booleans())
def test_p7_validator_never_raises(raw: object, as_entry: bool) -> None:
    """Whatever arrives in `data`, the frame path answers a frame or a DLQ record."""
    if isinstance(raw, bytes):
        got = classify({b"data": raw}, "tm:NYX-TA1", EID, 1, _model(), NOW)
    elif as_entry:
        got = classify({b"seq": b"1"}, "tm:NYX-TA1", EID, 1, _model(), NOW)
    else:
        got = _classify(raw)
    assert isinstance(got, FrameHeader | DlqRecord)
