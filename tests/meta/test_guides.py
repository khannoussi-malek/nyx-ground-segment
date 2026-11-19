"""The two guides keep the sections the spec asks for, and every time in them is UTC (UI-08)."""

import re

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
        "Run a replay",
    ],
    "dev-guide.md": [
        "Add a parameter",
        "Add a limit check type",
        "Add a command",
        "Change a stream interface",
        "Run the tests",
        "Archive, backfill and replay",
        "Performance and soak runs",
    ],
}
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?!\d)")


def _text(name: str) -> str:
    return (REPO / "docs" / name).read_text()


@pytest.mark.parametrize(("name", "headings"), GUIDES.items())
def test_guide_has_its_headings(name: str, headings: list[str]) -> None:
    found = {line[3:].strip() for line in _text(name).splitlines() if line.startswith("## ")}
    assert not [h for h in headings if h not in found], f"{name}: missing {headings}"


@pytest.mark.parametrize("name", GUIDES)
def test_every_time_example_ends_in_z(name: str) -> None:
    bad = []
    text = _text(name)
    for m in CLOCK.finditer(text):
        if text[m.end() : m.end() + 1] != "Z":
            bad.append(text[max(0, m.start() - 20) : m.end() + 5])
    assert not bad, f"{name}: times without Z: {bad}"


def test_operator_guide_is_one_page() -> None:
    assert len(_text("operator-guide.md").splitlines()) <= 120  # spec §6: a page, not a manual


def test_dev_guide_names_the_stream_upgrade_rule() -> None:
    text = _text("dev-guide.md")
    assert "restart all services together" in text and "make up" in text
    assert "scripts/stream_schema_notice.py" in text
    assert (REPO / "scripts" / "stream_schema_notice.py").is_file()


def test_dev_guide_mypy_scope_matches_ci() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    scope = re.search(r"mypy --strict ([\w /]+)", ci)
    assert scope, "ci.yml has no mypy --strict line"
    assert f"mypy --strict {scope.group(1).strip()}`" in _text("dev-guide.md")


UI_SRC = REPO / "ui" / "src"


@pytest.mark.skipif(
    not (UI_SRC / "tools" / "commandText.ts").exists(),
    reason="the console UI (Track C) is not in this tree",
)
def test_bold_labels_in_the_operator_guide_are_console_strings() -> None:
    """The guide quotes the console in bold; a label the UI does not contain is drift."""
    ui = "\n".join(
        p.read_text()
        for p in UI_SRC.rglob("*.ts*")
        if ".test." not in p.name and "schema.d.ts" not in p.name
    )
    labels = re.findall(r"\*\*(.+?)\*\*", _text("operator-guide.md"))
    assert len(labels) >= 15  # the check must not pass by finding nothing
    missing = [label for label in labels if label not in ui]
    assert not missing, f"not in ui/src: {missing}"


def test_operator_guide_explains_the_replay_screen() -> None:
    text = _text("operator-guide.md").split("## Run a replay", 1)[1].split("\n## ", 1)[0]
    for needle in ("UTC", "10×", "purple", "LIVE:", "409", "hash check", "NYX-TA9"):
        assert needle in text, needle


def test_dev_guide_documents_the_archiver_and_run_commands() -> None:
    text = _text("dev-guide.md")
    for needle in ("HOT_RETENTION_H", "ARCHIVE_LOOP_S", "NYX-TA9", "gs-sim backfill"):
        assert needle in text, needle
    assert "`make perf-p`" in text and "`make soak`" in text
    assert "docs/atp/ATP-07.md" in text
