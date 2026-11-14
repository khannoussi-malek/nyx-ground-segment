import re
from pathlib import Path

HOST = re.compile(r"\blocalhost\b|\b\d{1,3}(\.\d{1,3}){3}\b")


def test_no_host_literals_in_src() -> None:
    hits = [
        f"{p}:{n}"
        for p in Path("src").rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if HOST.search(line)
    ]
    assert hits == []
