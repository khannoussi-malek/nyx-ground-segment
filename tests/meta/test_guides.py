"""The two guides keep the sections the spec asks for, and every time in them is UTC (UI-08)."""

import re
from pathlib import Path

import pytest

from tests.support.env import REPO

GUIDES = {
    "operator-guide.md": [
        "Read the console",
        "Banners and tones",
        "Acknowledge an alarm",
        "Send a command",
        "When a command shows FAILED",
        "Run a test session",
    ],
    "dev-guide.md": [
        "Add a parameter",
        "Add a limit check type",
        "Add a command",
        "Change a stream interface",
        "Run the tests",
    ],
}
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?!\d)")


def _text(name: str) -> str:
    return (REPO / "docs" / name).read_text()


@pytest.mark.parametrize(("name", "headings"), GUIDES.items())
def test_guide_has_its_headings(name: str, headings: list[str]) -> None:
    found = {line.lstrip("#").strip() for line in _text(name).splitlines() if line.startswith("#")}
    assert not [h for h in headings if h not in found], f"{name}: missing {headings}"


@pytest.mark.parametrize("name", GUIDES)
def test_every_time_example_ends_in_z(name: str) -> None:
    bad = []
    for m in CLOCK.finditer(_text(name)):
        text = _text(name)
        if not text[m.end() : m.end() + 1] == "Z":
            bad.append(text[max(0, m.start() - 20) : m.end() + 5])
    assert not bad, f"{name}: times without Z: {bad}"


def test_operator_guide_is_one_page() -> None:
    assert len(_text("operator-guide.md").splitlines()) <= 120  # spec §6: a page, not a manual


def test_dev_guide_names_the_stream_upgrade_rule() -> None:
    text = _text("dev-guide.md")
    assert "restart all services together" in text and "make up" in text
    assert Path(REPO / "scripts" / "stream_schema_notice.py").name in text
