"""A fresh clone has no e2e/node_modules: the targets that run Playwright install it (Gate 5)."""

import re
from pathlib import Path

MAKEFILE = (Path(__file__).parents[2] / "Makefile").read_text()


def _prereqs(target: str) -> list[str]:
    m = re.search(rf"^{re.escape(target)}:([^\n=]*)$", MAKEFILE, re.M)
    assert m, f"no {target} target"
    return m.group(1).split()


def test_playwright_targets_install_the_e2e_packages() -> None:
    for target in ("e2e", "perf"):
        assert "e2e/node_modules" in _prereqs(target), target
    recipe = MAKEFILE.split("e2e/node_modules:", 1)[1].split("\n\n", 1)[0]
    assert "npm ci" in recipe and "playwright install chromium" in recipe
