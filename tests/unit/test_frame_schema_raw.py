"""The served frame schema lists the frame fields (raw names), never computed columns."""

from types import SimpleNamespace
from typing import Any

import pytest

from gs.api import app
from tests.support.mission import files, ld


async def test_frame_schema_has_raw_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    p = files()["parameters"] + b"derived:\n  twice: {unit: degC, expr: cabin_temp_c * 2}\n"
    mdb = ld(files(parameters=p))
    monkeypatch.setattr(app.deps, "mission", lambda _r: mdb)
    schema = await app.frame_schema(SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    props: dict[str, Any] = schema["properties"]
    assert "cabin_temp_c" in props and "twice" not in props
