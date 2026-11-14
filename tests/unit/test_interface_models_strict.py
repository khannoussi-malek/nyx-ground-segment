from datetime import UTC, datetime

from gs.common.interfaces import INTERFACE_MODELS, DlqRecord, FrameHeader, StrictModel, Tm


def test_every_interface_model_is_strict() -> None:
    for m in INTERFACE_MODELS:
        assert issubclass(m, StrictModel)
        cfg = m.model_config
        assert (cfg["strict"], cfg["allow_inf_nan"], cfg["frozen"]) == (True, False, True)


def test_extra_policy() -> None:  # EX-07
    assert FrameHeader.model_config["extra"] == "allow"
    others = {m.model_config["extra"] for m in INTERFACE_MODELS if m is not FrameHeader}
    assert others == {"forbid"}


def test_tm_whole_second_keeps_six_digits() -> None:
    tm = Tm(
        vehicle="NYX-TA1",
        seq=1,
        t_onboard=datetime(2026, 11, 14, 10, tzinfo=UTC),
        mode="TEST",
        last_cmd_id=None,
        values={"cabin_pressure_kpa": 101.3},
    )
    assert '"t_onboard":"2026-11-14T10:00:00.000000Z"' in tm.model_dump_json()
    assert tm.kind == "tm"
    assert tm.age_ms is None


def test_dlq_record_rejects_extra_field() -> None:
    import pytest
    from pydantic import ValidationError

    ok = {
        "reason": "schema",
        "detail": "d",
        "raw": "{}",
        "stream": "tm:NYX-TA1",
        "entry_id": "1-0",
        "deliveries": 1,
        "first_seen": "2026-11-14T10:00:00Z",
        "dlq_at": "2026-11-14T10:00:00Z",
    }
    assert DlqRecord.model_validate_json(__import__("json").dumps(ok)).deliveries == 1
    with pytest.raises(ValidationError) as ei:
        DlqRecord.model_validate_json(__import__("json").dumps({**ok, "x": 1}))
    assert ei.value.errors()[0]["type"] == "extra_forbidden"
