"""Warn (never fail) when a stream interface schema changed against origin/main (D-ING-27)."""

import subprocess
from collections.abc import Mapping
from pathlib import Path

STREAMS = ("frame", "tc", "tcack", "dlq", "ui-event")
REPO = Path(__file__).resolve().parent.parent


def changed(base: Mapping[str, str], head: Mapping[str, str]) -> list[str]:
    return [n for n in STREAMS if n in head and base.get(n) != head[n]]


def _base(name: str) -> str | None:
    p = subprocess.run(
        ["git", "show", f"origin/main:interfaces/schemas/{name}.schema.json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return p.stdout if p.returncode == 0 else None


def main() -> None:
    base: dict[str, str] = {}
    head: dict[str, str] = {}
    for n in STREAMS:
        f = REPO / "interfaces" / "schemas" / f"{n}.schema.json"
        if f.exists():
            head[n] = f.read_text(encoding="utf-8")
        if (b := _base(n)) is not None:
            base[n] = b
    if names := changed(base, head):
        print(f"stream interface changed: restart all services together ({', '.join(names)})")


if __name__ == "__main__":
    main()
