"""Fail when a commit subject since <base> is not `type: description` (ENG-07)."""

import re
import subprocess
import sys
from collections.abc import Sequence

OK = re.compile(r"^(feat|fix|refactor|docs|test|chore|perf|ci|merge)(\([^)]+\))?!?: \S|^Merge ")


def bad_subjects(subjects: Sequence[str]) -> list[str]:
    return [s for s in subjects if not OK.match(s)]


def main(base: str) -> int:
    p = subprocess.run(
        ["git", "log", "--format=%s", f"{base}..HEAD"], capture_output=True, text=True, check=False
    )
    if p.returncode != 0:
        print(p.stderr.strip(), file=sys.stderr)
        return 2
    bad = bad_subjects(p.stdout.splitlines())
    for s in bad:
        print(f"bad commit subject: {s!r} (want 'type: description')", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
