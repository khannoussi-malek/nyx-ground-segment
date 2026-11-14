import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir

Frame = build_frame_model(load_dir(Path("mission")).param_names)
NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)
CTX = {"stream_vehicle": "NYX-TA1", "now": NOW}
GOOD = (
    '{"vehicle": "NYX-TA1", "seq": 5, "t_onboard": "2026-11-14T10:00:00Z", "mode": "TEST",'
    ' "last_cmd_id": null, "cabin_pressure_kpa": 101.3}'
)


def patched(patch: str) -> str:
    key = patch.split(":")[0]
    d = {k: v for k, v in ((p.split(":", 1)[0].strip(), p) for p in _fields()) if k != key}
    return "{" + ", ".join([*d.values(), patch]) + "}"


def _fields() -> list[str]:
    return [
        '"vehicle": "NYX-TA1"',
        '"seq": 5',
        '"t_onboard": "2026-11-14T10:00:00Z"',
        '"mode": "TEST"',
        '"last_cmd_id": null',
        '"cabin_pressure_kpa": 101.3',
    ]


def types(text: str, **kw: object) -> set[str]:
    with pytest.raises(ValidationError) as ei:
        Frame.model_validate_json(text, **kw)  # type: ignore[arg-type]
    return {e["type"] for e in ei.value.errors()}


@pytest.mark.parametrize(
    ("patch", "err"),
    [
        ('"cabin_pressure_kpa": NaN', "finite_number"),
        ('"cabin_pressure_kpa": Infinity', "finite_number"),
        ('"cabin_pressure_kpa": 1e400', "finite_number"),
        ('"seq": "5"', "int_type"),
        ('"seq": 5.0', "int_type"),
        ('"seq": true', "int_type"),
        ('"seq": -1', "greater_than_equal"),
        ('"seq": 4294967296', "less_than_equal"),
        ('"t_onboard": "2026-11-14T10:00:00"', "timezone_aware"),
        ('"t_onboard": "2026-11-14T10:00:00+02:00"', "value_error"),
        ('"last_cmd_id": "abc"', "uuid_parsing"),
        ('"mode": "BOGUS"', "literal_error"),
    ],
)
def test_rejects(patch: str, err: str) -> None:
    assert err in types(patched(patch))


def test_good_frame() -> None:
    f = Frame.model_validate_json(GOOD)
    assert f.seq == 5 and f.last_cmd_id is None


def test_broken_json_is_json_invalid() -> None:
    assert types('{"vehicle": ') == {"json_invalid"}


def test_last_cmd_id_key_required() -> None:
    d = json.loads(GOOD)
    del d["last_cmd_id"]
    assert types(json.dumps(d)) == {"missing"}


def test_int_accepted_for_float_and_plus_zero_offset() -> None:
    f = Frame.model_validate_json(
        patched('"cabin_pressure_kpa": 101').replace("10:00:00Z", "10:00:00+00:00")
    )
    assert f.cabin_pressure_kpa == 101.0  # type: ignore[attr-defined]
    assert isinstance(f.cabin_pressure_kpa, float)  # type: ignore[attr-defined]


def test_unknown_field_kept_in_model_extra() -> None:
    f = Frame.model_validate_json(patched('"new_param": 3'))
    assert f.model_extra == {"new_param": 3}


def test_missing_param_is_none() -> None:
    f = Frame.model_validate_json(GOOD)
    assert f.tank_pressure_bar is None  # type: ignore[attr-defined]
    assert "tank_pressure_bar" not in f.model_fields_set


@pytest.mark.parametrize(
    ("t", "ok"),
    [
        (NOW - timedelta(days=7), True),
        (NOW - timedelta(days=7, microseconds=1), False),
        (NOW + timedelta(minutes=5), True),
        (NOW + timedelta(minutes=5, microseconds=1), False),
    ],
)
def test_t_onboard_window(t: datetime, ok: bool) -> None:
    text = patched(f'"t_onboard": "{t.isoformat()}"').replace("+00:00", "Z")
    if ok:
        Frame.model_validate_json(text, context=CTX)
    else:
        assert types(text, context=CTX) == {"t_onboard_range"}


def test_vehicle_mismatch() -> None:
    assert types(GOOD, context={**CTX, "stream_vehicle": "NYX-TA2"}) == {"vehicle_mismatch"}


def test_no_context_skips_runtime_checks() -> None:
    far = patched('"t_onboard": "1999-01-01T00:00:00Z"')
    assert Frame.model_validate_json(far).seq == 5
