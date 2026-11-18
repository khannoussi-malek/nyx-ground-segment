"""ENG-10: Python backend, React + TypeScript UI."""

import json
import tomllib

from tests.support.env import REPO


def test_python_package_and_react_typescript_ui() -> None:
    py = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert py["project"]["requires-python"].startswith("==3.")
    ui = json.loads((REPO / "ui/package.json").read_text())
    deps = {**ui.get("dependencies", {}), **ui.get("devDependencies", {})}
    assert "react" in deps and "typescript" in deps
    assert list((REPO / "src").rglob("*.py")) and list((REPO / "ui/src").rglob("*.tsx"))
