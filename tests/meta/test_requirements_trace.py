"""Every M/S requirement in docs/requirements.md names evidence, and the named tests exist."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.env import REPO

PATH = (
    r"(?:(?:tests/)?(?:unit|integration|system|meta)/[\w/.-]+\.py|e2e/[\w/.-]+\.ts"
    r"|ui/src/[\w/.-]+\.tsx?|perf/[\w/.-]+\.(?:py|ts)|scripts/[\w/.-]+\.py)"
)
# a file with an optional ::name, or a bare ::name that continues the previous file
REF = re.compile(
    rf"(?<![\w/.-])(?P<path>{PATH})(?:::(?P<name>\w+)(?:\[[^\]]*\])?)?|(?<![\w])::(?P<bare>\w+)"
)
ROW = re.compile(r"^\| (?P<id>[A-Z]+-\d+) \|")


@dataclass(frozen=True)
class Ref:
    path: str  # repo-relative
    name: str | None = None


@dataclass(frozen=True)
class Row:
    id: str
    method: str
    evidence: str
    refs: list[Ref]


def _rows(md: str) -> list[Row]:
    out = []
    for line in md.splitlines():
        if not (m := ROW.match(line)):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or cells[3] not in ("M", "S"):
            continue
        refs: list[Ref] = []
        # a bare ::name continues the previous file, but "planned, ...:" starts a new clause and
        # must not borrow the file named before it (that ref then names no file and is skipped)
        for clause in re.split(r"(?i)planned[^`]*:", cells[5]):
            last = ""
            for span in re.findall(r"`([^`]*)`", clause):
                for r in REF.finditer(span):
                    if r["path"]:
                        last = (
                            r["path"]
                            if r["path"].startswith(("tests/", "e2e", "ui", "perf", "scripts"))
                            else "tests/" + r["path"]
                        )
                        refs.append(Ref(last, r["name"]))
                    elif last:
                        refs.append(Ref(last, r["bare"]))
        out.append(Row(m["id"], cells[2], cells[5], refs))
    return out


def evidence(md: str) -> dict[str, list[Ref]]:
    return {r.id: r.refs for r in _rows(md)}


def _defines(text: str, name: str) -> bool:
    return bool(
        re.search(rf"\bdef {re.escape(name)}\b", text)
        or re.search(rf"\b(?:test|it)\(\s*[\"'`]{re.escape(name)}[\"'`]", text)
    )


def missing(refs: list[Ref], root: Path = REPO) -> list[str]:
    out: list[str] = []
    for r in dict.fromkeys(refs):
        f = root / r.path
        if not f.is_file():
            if (line := f"{r.path}: file absent") not in out:
                out.append(line)
        elif r.name and not _defines(f.read_text(), r.name):
            out.append(f"{r.path}::{r.name}: not defined")
    return out


FIXTURE = """\
## 4.1
| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A-01 | x | T | M | D | `unit/test_a.py::test_one[p1, p2]`, `::test_two`; `perf/run.py`;\
 `e2e/a.spec.ts::flow` | 1 | |
| A-02 | x | I | S | D | Inspection of the README | I | |
| A-03 | x | T | E | D | `unit/test_skip.py::test_x` | E | |
| A-04 | x | T | M | D | `integration/test_b.py::test_b` and `::test_c` | 1 | |
| A-05 | x | T | M | D | `unit/test_a.py::test_one`; planned, with Task 9: `::test_late` | 1 | |
"""


def test_trace_parser_on_a_fixture_table() -> None:
    ev = evidence(FIXTURE)
    assert sorted(ev) == ["A-01", "A-02", "A-04", "A-05"]  # E rows are not part of the 47
    assert [(r.path, r.name) for r in ev["A-01"]] == [
        ("tests/unit/test_a.py", "test_one"),
        ("tests/unit/test_a.py", "test_two"),
        ("perf/run.py", None),
        ("e2e/a.spec.ts", "flow"),
    ]
    assert ev["A-02"] == []
    assert [(r.path, r.name) for r in ev["A-04"]] == [
        ("tests/integration/test_b.py", "test_b"),
        ("tests/integration/test_b.py", "test_c"),
    ]


def test_a_planned_marker_does_not_borrow_the_previous_file() -> None:
    assert [(r.path, r.name) for r in evidence(FIXTURE)["A-05"]] == [
        ("tests/unit/test_a.py", "test_one")
    ]


def test_missing_lists_an_absent_file_once(tmp_path: Path) -> None:
    refs = [Ref("tests/unit/nope.py", "a"), Ref("tests/unit/nope.py", "b")]
    assert missing(refs, tmp_path) == ["tests/unit/nope.py: file absent"]


def test_missing_finds_absent_file_and_absent_name(tmp_path: Path) -> None:
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/unit/test_a.py").write_text(
        "def test_one(): ...\nasync def test_two(): ...\n"
    )
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e/a.spec.ts").write_text("test('flow', async () => {});\n")
    ok = [Ref("tests/unit/test_a.py", "test_one"), Ref("tests/unit/test_a.py", "test_two")]
    ok += [Ref("e2e/a.spec.ts", "flow"), Ref("e2e/a.spec.ts", None)]
    assert missing(ok, tmp_path) == []
    bad = [Ref("tests/unit/test_a.py", "test_three"), Ref("tests/unit/nope.py", None)]
    assert len(missing(bad, tmp_path)) == 2


def test_the_real_table_has_47_rows() -> None:
    assert len(evidence((REPO / "docs/requirements.md").read_text())) == 47


def test_every_requirement_has_evidence() -> None:
    text = (REPO / "docs/requirements.md").read_text()
    bad = [
        r.id
        for r in _rows(text)
        if not r.refs
        and not ("I" in r.method.replace(" ", "").split(",") and "nspection" in r.evidence)
    ]
    assert bad == []


@pytest.mark.xfail(strict=True, reason="Task 25 writes the remaining named tests and removes this")
def test_every_named_test_exists() -> None:
    refs = [r for rs in evidence((REPO / "docs/requirements.md").read_text()).values() for r in rs]
    assert missing(refs) == []
