import importlib.util

import pytest

from tests.support.env import REPO

_spec = importlib.util.spec_from_file_location(
    "stream_schema_notice", REPO / "scripts/stream_schema_notice.py"
)
assert _spec and _spec.loader
n = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(n)


def test_changed_schema_named() -> None:
    assert n.changed({"tc": "a", "frame": "f"}, {"tc": "b", "frame": "f"}) == ["tc"]


def test_unchanged_is_silent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        n, "_base", lambda name: (n.REPO / "interfaces/schemas" / f"{name}.schema.json").read_text()
    )
    n.main()
    assert capsys.readouterr().out == ""


def test_changed_prints_notice_and_does_not_raise(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(n, "_base", lambda name: None)
    n.main()
    assert capsys.readouterr().out.startswith(
        "stream interface changed: restart all services together ("
    )


def test_missing_on_base_counts_as_changed() -> None:
    assert n.changed({}, {"tcack": "x"}) == ["tcack"]
