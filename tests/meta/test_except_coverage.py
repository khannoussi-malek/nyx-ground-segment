"""Every `except` block under src/gs/{ingest,archiver,api} is executed by the tests, or is listed
with a reason in except_allowlist.txt (D-ENG-21, A-R14-7). The gate test runs after the coverage
run and is deselected by default (marker `after_coverage`); it is switched on in Task 24."""

import ast
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
ALLOWLIST = Path(__file__).parent / "except_allowlist.txt"
PACKAGES = ("ingest", "archiver", "api")


@dataclass(frozen=True)
class Handler:
    path: Path
    qualname: str  # enclosing function or Class.method, "<module>" at top level
    exc: str  # ast.unparse of the handler type, "bare" when there is none
    first_line: int  # first line of the handler body

    @property
    def key(self) -> str:
        return f"{self.path}::{self.qualname}::{self.exc}"


def handlers(path: Path) -> list[Handler]:
    found: list[Handler] = []

    def walk(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ExceptHandler):
                exc = "bare" if child.type is None else ast.unparse(child.type)
                found.append(
                    Handler(path, ".".join(scope) or "<module>", exc, child.body[0].lineno)
                )
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(child, (*scope, child.name))
            else:
                walk(child, scope)

    walk(ast.parse(path.read_text(), filename=str(path)), ())
    return found


def load_allowlist(text: str) -> set[str]:
    keys: set[str] = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, reason = line.partition("  # ")
        if not sep or not reason.strip():
            raise ValueError(f"allowlist line {n}: needs '<key>  # reason'")
        keys.add(key.strip())
    return keys


def unexecuted(src: Sequence[Path], executed: Mapping[str, set[int]], allow: set[str]) -> list[str]:
    """Handlers never executed and not allowlisted, plus allowlist keys matching no handler.

    Key = path::qualname::exception, so two handlers of the same exception type in one function
    share a key: one allowlist line covers both."""
    every = [h for p in src for h in handlers(p)]
    bad = [
        h.key
        for h in every
        if h.first_line not in executed.get(str(h.path), set()) and h.key not in allow
    ]
    stale = sorted(allow - {h.key for h in every})
    return [*bad, *(f"stale allowlist entry: {k}" for k in stale)]


# ---- the checker's own tests (negative control on a fixture module)
def lines_of_first_handler(path: Path) -> set[int]:
    """Test-local helper: every line of the file except the second handler's body."""
    second = handlers(path)[1].first_line
    return set(range(1, len(path.read_text().splitlines()) + 1)) - {second}


def test_detects_unexecuted_handler() -> None:
    fx = FIXTURES / "one_unexecuted.py"
    assert unexecuted([fx], {str(fx): lines_of_first_handler(fx)}, set()) == [
        f"{fx}::g::ValueError"
    ]


def test_executed_handlers_pass() -> None:
    fx = FIXTURES / "one_unexecuted.py"
    every = {h.first_line for h in handlers(fx)}
    assert unexecuted([fx], {str(fx): every}, set()) == []
    assert len(unexecuted([fx], {}, set())) == 2


def test_allowlisted_handler_passes() -> None:
    fx = FIXTURES / "one_unexecuted.py"
    allow = load_allowlist(f"# header\n\n{fx}::g::ValueError  # only a hostile client reaches it\n")
    assert unexecuted([fx], {str(fx): lines_of_first_handler(fx)}, allow) == []


def test_stale_allowlist_entry_fails() -> None:
    fx = FIXTURES / "one_unexecuted.py"
    every = {h.first_line for h in handlers(fx)}
    assert unexecuted([fx], {str(fx): every}, {"gone.py::x::E"}) == [
        "stale allowlist entry: gone.py::x::E"
    ]


@pytest.mark.parametrize("line", ["a.py::f::E", "a.py::f::E  #", "a.py::f::E  #   "])
def test_allowlist_needs_a_reason(line: str) -> None:
    with pytest.raises(ValueError, match="reason"):
        load_allowlist(line)


def test_handlers_names_scope_and_type(tmp_path: Path) -> None:
    p = tmp_path / "m.py"
    p.write_text(
        "try:\n    pass\nexcept:\n    pass\n"
        "class C:\n    async def m(self):\n        try:\n            pass\n"
        "        except (A, B) as e:\n            raise\n"
    )
    assert [(h.qualname, h.exc, h.first_line) for h in handlers(p)] == [
        ("<module>", "bare", 4),
        ("C.m", "(A, B)", 10),
    ]


# ---- the gate: after the coverage run, deselected until Task 24
@pytest.mark.after_coverage
def test_every_handler_executed() -> None:
    cov = ROOT / "coverage.json"
    if not cov.exists():
        pytest.fail("coverage.json missing: run pytest --cov=gs --cov-report=json first")
    data = json.loads(cov.read_text())["files"]
    executed = {k: set(v["executed_lines"]) for k, v in data.items()}
    src = sorted(
        p.relative_to(ROOT) for pkg in PACKAGES for p in (ROOT / "src/gs" / pkg).rglob("*.py")
    )
    assert src, "no source files found under src/gs/{ingest,archiver,api}: a package was renamed?"
    missing = unexecuted(src, executed, load_allowlist(ALLOWLIST.read_text()))
    assert not missing, (
        "handlers never executed (test them or allowlist with a reason):\n" + "\n".join(missing)
    )
