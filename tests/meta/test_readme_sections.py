"""ENG-08 and the spec's documents: README sections, changelog, ATP procedures, no drift."""

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from gs.simulator.backfill import parse_fault
from gs.simulator.faults import FaultClear, FaultRequest
from tests.support.env import REPO

README_HEADINGS = [
    "What it is",
    "Run",
    "What to try first",
    "Architecture",
    "Decisions and why",
    "Limitations",
    "Next steps",
]
ATPS = ["ATP-01", "ATP-07", "ATP-08"]
# Things a doc names that other Gate 4 tasks (19-21) and Gate 5 add, per doc: "make <target>" or a
# path. test_landing_entries_are_removed_when_they_land fails once an entry exists.
LANDING = {
    "README.md": {"docs/perf/scaling.md"},
    "ATP-01.md": {"make soak", "docs/atp-report.md"},
    "ATP-07.md": {"docs/atp-report.md"},
    "ATP-08.md": {"make perf-p", "docs/perf/scaling.md", "docs/atp-report.md"},
    "dev-guide.md": {"make perf-p", "make soak"},
}
DOCS = [REPO / "README.md", REPO / "CHANGELOG.md", *sorted((REPO / "docs/atp").glob("*.md"))]
DOCS += [REPO / "docs/operator-guide.md", REPO / "docs/dev-guide.md"]


def _h2(path: Path) -> set[str]:
    """Exact `## ` headings (a `### ` or `#` line does not count)."""
    return {ln[3:].strip() for ln in path.read_text().splitlines() if ln.startswith("## ")}


def test_readme_sections() -> None:
    missing = [h for h in README_HEADINGS if h not in _h2(REPO / "README.md")]
    assert missing == []


def test_readme_run_block_is_the_three_commands() -> None:
    text = (REPO / "README.md").read_text()
    run = text.split("## Run", 1)[1].split("## ", 1)[0]
    blocks = re.findall(r"```\n(.*?)```", run, re.S)
    assert len(blocks) == 1
    cmds = blocks[0].strip().splitlines()
    assert len(cmds) == 3
    assert cmds[0].startswith("git clone ") and cmds[1] == "cd nyx" and cmds[2] == "make up"


def test_readme_screenshot_is_a_real_png() -> None:
    assert "docs/img/console.png" in (REPO / "README.md").read_text()
    png = (REPO / "docs/img/console.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000


def test_readme_limitations_name_the_single_minio_copy() -> None:
    text = (REPO / "README.md").read_text().split("## Limitations", 1)[1].split("## ", 1)[0]
    assert "single copy" in text and "nothing repairs it" in text and "scrub" in text


def test_changelog_has_unreleased_and_limitations() -> None:
    text = (REPO / "CHANGELOG.md").read_text()
    assert "[Unreleased]" in _h2(REPO / "CHANGELOG.md")
    assert "### What's in" in text and "### Known limitations" in text


@pytest.mark.parametrize("atp", ATPS)
def test_atp_procedures_exist(atp: str) -> None:
    text = (REPO / "docs/atp" / f"{atp}.md").read_text()
    assert text.startswith(f"# {atp}") and len(text.splitlines()) >= 6


def _atp(n: str) -> str:
    return (REPO / "docs/atp" / f"{n}.md").read_text()


def test_atp07_has_the_manual_steps() -> None:
    text = _atp("ATP-07")
    for needle in ("gs-sim backfill", "NYX-TA9", "sha256sum -c", "mc cp", "ARCHIVER_CRASH_AT"):
        assert needle in text, needle


def test_atp07_stops_the_archiver_before_the_backfill_it_crashes_on() -> None:
    step = _atp("ATP-07").split("**Kill the archiver", 1)[1].split("\n5. ", 1)[0]
    assert step.index("docker compose stop archiver") < step.index("gs-sim backfill")
    assert step.index("gs-sim backfill") < step.index("ARCHIVER_CRASH_AT")


def test_atp07_retains_a_recent_hour_and_counts_frames_with_the_dropout() -> None:
    text = _atp("ATP-07")
    assert "HOT_RETENTION_H" in text and "last 24 h" in text
    specs = re.findall(r"--fault (\S+)", text)
    parsed = [parse_fault(f.strip("'"), "NYX-TA9") for f in specs]
    assert {f.request.fault for f in parsed} == {"leak", "dropout"}
    window = timedelta(minutes=20).total_seconds()  # the replayed window starts at the first hour
    assert all(f.offset_s < window for f in parsed), "a fault lies outside the replayed window"
    lost = sum(
        float(str(f.request.params["duration_s"])) for f in parsed if f.request.fault == "dropout"
    )
    assert f"sent {2 * 3600 * 10 - round(lost * 10)} frames" in text  # 71900 for 10 s of dropout


def test_atp01_soak_owns_the_sessions_and_states_the_quality_pass_rule() -> None:
    text = _atp("ATP-01")
    assert "make soak" in text
    assert "starts a session on NYX-TA1 and one on NYX-TA2" in text
    assert "Start session" not in text  # a second start on a vehicle is refused (D-DB-07)
    assert "0 false raises" in text and "no chattering" in text


def test_atp08_has_the_scaling_command_and_no_placeholder() -> None:
    text = _atp("ATP-08")
    assert "make perf-p" in text and "python -m perf.scaling docs/perf/run-p-" in text
    assert not re.search(r"\bTask \d|in progress|placeholder", text, re.I)


@pytest.mark.parametrize("doc", DOCS, ids=lambda d: d.name)
def test_no_unfinished_markers(doc: Path) -> None:
    assert not re.search(r"in progress\)|\bTask \d|placeholder|TODO", doc.read_text(), re.I)


def test_readme_curl_bodies_are_valid_simulator_requests() -> None:
    """Every documented fault call parses with the simulator's own models; DELETE needs only
    vehicle and fault."""
    text = (REPO / "README.md").read_text()
    calls = re.findall(r"curl (?:-X (POST|DELETE) )?http\S+/sim/fault .*?-d '(\{.*?\})'", text)
    verbs = []
    for verb, body in calls:
        verbs.append(verb)
        if verb == "DELETE":
            FaultClear.model_validate_json(body)
        else:
            FaultRequest.model_validate_json(body)
    assert "DELETE" in verbs and verbs.count("") + verbs.count("POST") >= 2
    dropout = [b for v, b in calls if '"dropout"' in b]
    assert dropout and json.loads(dropout[0])["params"] == {"duration_s": 10}


def test_every_make_target_in_the_docs_exists_or_is_landing() -> None:
    """`make x` in a code span must be a Makefile target (or one Tasks 19-21 are adding)."""
    targets = set(re.findall(r"^([a-z][\w-]*):", (REPO / "Makefile").read_text(), re.M))
    checked, bad = 0, []
    for doc in DOCS:
        for n, line in enumerate(doc.read_text().splitlines(), 1):
            for t in re.findall(r"`make ([a-z][\w-]*)", line):
                checked += 1
                if t not in targets and f"make {t}" not in LANDING.get(doc.name, set()):
                    bad.append(f"{doc.name}:{n}: make {t}")
    assert checked >= 8  # the check must not pass by finding nothing
    assert bad == []


def test_documented_paths_exist_or_are_landing() -> None:
    """A `docs/...`, `scripts/...`, `src/...` or `perf/...` path in a code span must exist."""
    bad, checked = [], 0
    for doc in DOCS:
        for n, line in enumerate(doc.read_text().splitlines(), 1):
            for p in re.findall(r"`((?:docs|scripts|src|mission|perf)/[\w./-]+?)`", line):
                if "*" in p or "<" in p:
                    continue
                checked += 1
                if not (REPO / p).exists() and p not in LANDING.get(doc.name, set()):
                    bad.append(f"{doc.name}:{n}: {p}")
    assert checked >= 8
    assert bad == []


def test_readme_changelog_and_atps_do_not_mention_aos() -> None:
    """The guides legitimately name the AOS event; the other documents must not use the word,
    which was the name of a different project."""
    docs = [REPO / "README.md", REPO / "CHANGELOG.md", *(REPO / "docs/atp").glob("*.md")]
    for doc in docs:
        text = doc.read_text().replace("acquisition of signal", "")
        assert not re.search(r"\baos\b", text, re.I), doc


def test_landing_entries_are_removed_when_they_land() -> None:
    targets = set(re.findall(r"^([a-z][\w-]*):", (REPO / "Makefile").read_text(), re.M))
    landed = [
        f"{doc}: {e}"
        for doc, entries in LANDING.items()
        for e in entries
        if (e.startswith("make ") and e[5:] in targets)
        or (not e.startswith("make ") and (REPO / e).exists())
    ]
    assert landed == [], "remove these from LANDING, they exist now"
    assert set(LANDING) <= {d.name for d in DOCS}


def test_unbuilt_work_is_worded_as_landing_in_this_gate() -> None:
    text = (REPO / "CHANGELOG.md").read_text() + (REPO / "README.md").read_text()
    assert "landing in this gate" in text


def test_atp07_counts_hot_rows_before_the_archiver_starts_and_names_the_crash_hour() -> None:
    step = _atp("ATP-07").split("**Kill the archiver", 1)[1].split("\n5. ", 1)[0]
    first_count = step.index("select count(*)")
    start = step.index("GS_TEST_HOOKS=1 ARCHIVER_CRASH_AT")
    assert step.index("gs-sim backfill") < first_count < start
    assert step.count("select count(*)") >= 1 and "107900" in step  # 71900 + one full hour
    assert "logs archiver" in step and "NYX-TA9" in step.split("logs archiver", 1)[1]
    assert step.index("docker compose up -d --force-recreate archiver") > start


def test_atp07_waits_for_ingest_before_the_archiver_pass_in_step_1() -> None:
    text = _atp("ATP-07")
    step1 = text.split("1. **Backfill", 1)[1].split("\n2. ", 1)[0]
    assert "71900" in step1 and "select count(*)" in step1
