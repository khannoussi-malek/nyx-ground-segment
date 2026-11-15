"""Every service entry point logs JSON: main() calls setup_logging with its service name, and
nothing in src/gs falls back to plain-text logging (D-ENG-07, ENG-05)."""

import importlib
import re
from pathlib import Path

import pytest

from tests.support.env import REPO


class Stop(Exception):
    pass


@pytest.mark.parametrize(
    ("module", "service"),
    [
        ("gs.simulator.main", "gs-sim"),
        ("gs.ingest.main", "gs-ingest"),
        ("gs.common.migrate", "gs-migrate"),
        ("gs.api.server", "gs-api"),
    ],
)
def test_main_sets_up_json_logging(
    monkeypatch: pytest.MonkeyPatch, module: str, service: str
) -> None:
    mod = importlib.import_module(module)
    calls: list[str] = []

    def record(name: str) -> None:
        calls.append(name)
        raise Stop

    monkeypatch.setattr(mod, "setup_logging", record)
    for name in ("_main", "from_env"):  # a reverted main() must not start a real service
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
    with pytest.raises((Stop, RuntimeError)):
        mod.main()
    assert calls == [service]


def test_no_plain_text_logging_in_src() -> None:
    export = Path("gs/common/interfaces/export.py")  # build tool: prints the files it wrote
    bad = []
    for f in (REPO / "src/gs").rglob("*.py"):
        if f.relative_to(REPO / "src") == export:
            continue
        text = f.read_text()
        if re.search(r"(?<![\w.])print\(|logging\.basicConfig", text):
            bad.append(str(f.relative_to(REPO)))
    assert bad == []
