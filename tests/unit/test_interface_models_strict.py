import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import RootModel, ValidationError

from gs.common.interfaces import (
    INTERFACE_MODELS,
    DlqRecord,
    FrameHeader,
    Hb,
    Resync,
    Snapshot,
    StrictModel,
    Tc,
    TcAck,
    Tm,
    UiEvent,
)
from gs.common.interfaces.render import schema_sha12
from tests.support.env import REPO

# UiEvent is a RootModel (a union): it cannot be a StrictModel or set `extra`; its members are
# interface models themselves, so the checks below skip roots and cover the members.
MODELS = [m for m in INTERFACE_MODELS if not issubclass(m, RootModel)]


def test_every_interface_model_is_strict() -> None:
    for m in MODELS:
        assert issubclass(m, StrictModel)
        cfg = m.model_config
        assert (cfg["strict"], cfg["allow_inf_nan"], cfg["frozen"]) == (True, False, True)


def test_extra_policy() -> None:  # EX-07
    assert FrameHeader.model_config["extra"] == "allow"
    others = {m.model_config["extra"] for m in MODELS if m is not FrameHeader}
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


def test_tc_requires_issued_by_and_forbids_extra() -> None:
    ok = {
        "cmd_id": str(uuid4()),
        "name": "isolate_valve",
        "args": {"valve": "A"},
        "issued_by": "fc1",
        "issued_at": "2026-11-15T10:00:00.000000Z",
        "reason": None,
    }
    Tc.model_validate_json(json.dumps(ok))
    for bad in (
        {**ok, "issued_by": ""},
        {**ok, "extra": 1},
        {k: v for k, v in ok.items() if k != "cmd_id"},
    ):
        with pytest.raises(ValidationError):
            Tc.model_validate_json(json.dumps(bad))


def test_tcack_code_pattern() -> None:
    base = {
        "cmd_id": str(uuid4()),
        "accepted": True,
        "reason": None,
        "t_onboard": "2026-11-15T10:00:00Z",
    }
    for code in ("OK", "INVALID_ARGS", "BUSY"):
        TcAck.model_validate_json(json.dumps({**base, "code": code}))
    for code in ("ok", "", "A" * 33):
        with pytest.raises(ValidationError):
            TcAck.model_validate_json(json.dumps({**base, "code": code}))


def test_ui_event_discriminates() -> None:
    assert isinstance(
        UiEvent.model_validate_json('{"kind":"resync","vehicle":"NYX-TA1"}').root, Resync
    )
    with pytest.raises(ValidationError):
        UiEvent.model_validate_json('{"kind":"hb","server_ms":1}')


def test_new_defaults_keep_gate2_shapes() -> None:
    assert Hb(server_ms=1).processing == {}
    assert Snapshot(vehicles={}).alarms == []
    tm = Tm.model_validate_json(
        '{"kind":"tm","vehicle":"NYX-TA1","seq":1,"t_onboard":"2026-11-15T10:00:00Z",'
        '"mode":"TEST","last_cmd_id":null,"values":{}}'
    )
    assert tm.frozen is False


def test_schema_sha12_matches_committed_file() -> None:
    data = (REPO / "interfaces/schemas/tcack.schema.json").read_bytes()
    assert schema_sha12(TcAck) == hashlib.sha256(data).hexdigest()[:12]
