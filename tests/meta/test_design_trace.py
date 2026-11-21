"""R25 F3: the code -> D-ID index is generated, fresh, and cites only decisions that exist."""

import re
from pathlib import Path

from scripts.design_trace import REPO, cited, defined, render, uncited
from tests.meta.test_requirements_trace import REF, Ref, missing

DESIGN = REPO / "docs/design.md"
INDEX = REPO / "docs/assurance/design-trace.md"
HAZARDS = REPO / "docs/assurance/hazards.md"


def _src(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_cited_inverts_ids_to_sorted_files(tmp_path: Path) -> None:
    src = _src(
        tmp_path,
        {"b.py": "# D-ALM-03, D-ALM-04\n", "a/c.py": "x = 1  # D-ALM-03\n", "n.py": "pass\n"},
    )
    assert cited(src) == {"D-ALM-03": ["a/c.py", "b.py"], "D-ALM-04": ["b.py"]}


def test_defined_reads_table_row_ids(tmp_path: Path) -> None:
    d = tmp_path / "design.md"
    d.write_text("| D-ING-01 | x |\nprose mentions D-ING-99 only\n| D-CMD-02 | y |\n")
    assert defined(d) == {"D-ING-01", "D-CMD-02"}


def test_render_is_deterministic(tmp_path: Path) -> None:
    src = _src(tmp_path, {"a.py": "# D-X-01\n"})
    assert render(src) == render(src)
    assert "| D-X-01 | `a.py` |" in render(src)


def test_uncited_is_defined_minus_cited(tmp_path: Path) -> None:
    src = _src(tmp_path, {"a.py": "# D-X-01\n"})
    d = tmp_path / "design.md"
    d.write_text("| D-X-01 | a |\n| D-X-02 | b |\n| D-X-03 | c |\n")
    assert uncited(src, d) == ["D-X-02", "D-X-03"]
    out = render(src, "", d)
    assert "D-X-02, D-X-03" in out and "src/` only" in out


def test_committed_index_is_fresh() -> None:  # drift
    assert INDEX.read_text() == render(REPO / "src", "src/", DESIGN), (
        "run: uv run python -m scripts.design_trace"
    )


def test_every_cited_decision_exists_in_design() -> None:
    missing = sorted(set(cited(REPO / "src")) - defined(REPO / "docs/design.md"))
    assert missing == [], f"D-IDs cited in src/ but not defined in docs/design.md: {missing}"


def test_hazard_table_names_real_requirements_and_decisions() -> None:
    text = HAZARDS.read_text()
    reqs = set(re.findall(r"^\| ([A-Z]+-\d+) ", (REPO / "docs/requirements.md").read_text(), re.M))
    named = set(re.findall(r"(?<!D-)\b(?:ING|ALM|CMD|UI|DAT|ENG|EXT)-\d\d\b", text))
    assert named and named <= reqs, sorted(named - reqs)
    assert set(re.findall(r"\bD-[A-Z]+-\d+\b", text)) <= defined(REPO / "docs/design.md")


def test_every_hazard_cites_a_real_test() -> None:
    """R25 review: each hazard names at least one test file (and test, when given) that exists."""
    sections = re.split(r"^### (?=H-\d+ )", HAZARDS.read_text(), flags=re.M)[1:]
    assert len(sections) >= 11
    for sec in sections:
        title = sec.splitlines()[0]
        refs = [Ref(r["path"], r["name"]) for r in REF.finditer(sec) if r["path"]]
        assert refs, f"{title}: no test cited"
        assert not (bad := missing(refs)), f"{title}: {bad}"
