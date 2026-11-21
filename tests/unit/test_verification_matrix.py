"""scripts/verification_matrix.py: requirement -> named test -> as-run result, drift fails."""

import json
from pathlib import Path

from scripts import verification_matrix as vm
from tests.meta.test_requirements_trace import Ref

JUNIT = """<testsuites><testsuite name="pytest">
<testcase classname="tests.unit.test_a" name="test_ok"/>
<testcase classname="tests.unit.test_a" name="test_p[1]"/>
<testcase classname="tests.unit.test_a" name="test_p[2]"><failure message="boom"/></testcase>
<testcase classname="tests.unit.test_b.TestC" name="test_in_class"/>
<testcase classname="tests.unit.test_b" name="test_skip_why">
<skipped message="needs docker"/></testcase>
<testcase classname="tests.unit.test_b" name="test_skip_bare"><skipped message=""/></testcase>
</testsuite></testsuites>"""

PW = {
    "suites": [
        {
            "file": "x.spec.ts",
            "specs": [],
            "suites": [
                {
                    "file": "x.spec.ts",
                    "specs": [
                        {
                            "title": "shows alarm",
                            "file": "x.spec.ts",
                            "tests": [{"status": "expected", "results": [{"status": "passed"}]}],
                        },
                        {
                            "title": "broken",
                            "file": "x.spec.ts",
                            "tests": [{"status": "unexpected", "results": [{"status": "failed"}]}],
                        },
                        {
                            "title": "skipped one",
                            "file": "x.spec.ts",
                            "tests": [
                                {
                                    "status": "skipped",
                                    "annotations": [{"type": "skip", "description": "no gpu"}],
                                    "results": [],
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    ]
}

REQS = """| ID | One sentence | M | P | Decisions | Planned evidence | ATP | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AAA-01 | x | T | M | D | `unit/test_a.py::test_ok` | 01 | PASS |
| AAA-02 | x | T | M | D | `unit/test_a.py::test_p` | 01 | PASS |
| AAA-03 | x | T | M | D | `unit/test_b.py::test_in_class`, `::test_skip_why` | 01 | PASS |
| AAA-04 | x | T | M | D | `unit/test_b.py::test_skip_bare`, `unit/test_gone.py::test_x` | 01 | PASS |
| AAA-05 | x | T | M | D | `e2e/x.spec.ts::broken`; `perf/run_n.py` | 01 | PASS |
"""  # noqa: E501 requirement rows are one line each


def _cases(tmp: Path) -> list[vm.Case]:
    j = tmp / "j.xml"
    j.write_text(JUNIT)
    p = tmp / "p.json"
    p.write_text(json.dumps(PW))
    return vm.read_junit(j) + vm.read_playwright(p)


def test_statuses(tmp_path: Path) -> None:
    cases = _cases(tmp_path)
    assert vm.status(Ref("tests/unit/test_a.py", "test_ok"), cases) == ("pass", "")
    assert vm.status(Ref("tests/unit/test_a.py", "test_p"), cases)[0] == "fail"  # one param failed
    assert vm.status(Ref("tests/unit/test_b.py", "test_in_class"), cases)[0] == "pass"
    assert vm.status(Ref("tests/unit/test_b.py", "test_skip_why"), cases) == (
        "skipped",
        "needs docker",
    )
    assert vm.status(Ref("tests/unit/test_b.py", "test_skip_bare"), cases)[0] == "missing"
    assert vm.status(Ref("tests/unit/test_gone.py", "test_x"), cases)[0] == "missing"
    assert vm.status(Ref("e2e/x.spec.ts", "shows alarm"), cases)[0] == "pass"
    assert vm.status(Ref("e2e/x.spec.ts", "skipped one"), cases) == ("skipped", "no gpu")
    assert vm.status(Ref("perf/run_n.py", None), cases)[0] == "not in run"
    assert vm.status(Ref("tests/unit/test_a.py", None), cases)[0] == "fail"  # whole file


def test_render_and_drift(tmp_path: Path) -> None:
    md, drift = vm.render(REQS, _cases(tmp_path), run_id="42", commit="abc123")
    assert "run 42" in md and "abc123" in md
    assert "| AAA-01 | `tests/unit/test_a.py::test_ok` | pass |" in md
    assert "needs docker" in md  # the skip reason is listed
    assert sorted(drift) == [
        "AAA-04: tests/unit/test_b.py::test_skip_bare missing",
        "AAA-04: tests/unit/test_gone.py::test_x missing",
    ]


def test_main_exits_1_on_drift_and_writes_matrix(tmp_path: Path) -> None:
    (tmp_path / "r.md").write_text(REQS)
    (tmp_path / "j.xml").write_text(JUNIT)
    out = tmp_path / "m.md"
    argv = ["--requirements", str(tmp_path / "r.md"), "--junit", str(tmp_path / "j.xml")]
    assert vm.main([*argv, "--out", str(out)]) == 1
    assert "AAA-01" in out.read_text()
    (tmp_path / "ok.md").write_text("\n".join(REQS.splitlines()[:3]) + "\n")
    argv = ["--requirements", str(tmp_path / "ok.md"), "--junit", str(tmp_path / "j.xml")]
    assert vm.main([*argv, "--out", str(out)]) == 0
