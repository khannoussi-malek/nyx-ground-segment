from uuid import UUID

import pytest
from pydantic import ValidationError

from gs.api.bodies import AckRequest, CommandRequest, SessionStart

RID = "12345678-1234-5678-1234-567812345678"


def test_ack_request_parses_the_json_uuid() -> None:
    # FastAPI validates a decoded JSON body in python mode: a strict UUID would reject the str
    assert AckRequest.model_validate({"request_id": RID, "note": "seen"}).request_id == UUID(RID)


@pytest.mark.parametrize(
    "body",
    [
        {"vehicle": "NYX-TA1", "name": "x", "args": {"valve": "A"}, "extra": 1},
        {"vehicle": "nyx", "name": "x", "args": {}},
        {"vehicle": "NYX-TA1", "name": "x", "args": {"duration_s": "5"}, "reason": 1},
    ],
)
def test_command_request_is_strict(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CommandRequest.model_validate(body)


def test_command_request_keeps_arg_types() -> None:
    body = {"vehicle": "NYX-TA1", "name": "vent_cabin", "args": {"duration_s": 5, "f": 1.5}}
    assert CommandRequest.model_validate(body).args == {"duration_s": 5, "f": 1.5}


@pytest.mark.parametrize("name", ["", "x" * 201])
def test_session_name_length(name: str) -> None:
    with pytest.raises(ValidationError):
        SessionStart.model_validate({"vehicle": "NYX-TA1", "name": name})
