"""Fail when a commit since <base> has a subject that is not `type: description` (ENG-07), or a
message with a Co-Authored-By trailer or an AI attribution (owner rule, G4-11)."""

import re
import subprocess
import sys
from collections.abc import Sequence

OK = re.compile(r"^(feat|fix|refactor|docs|test|chore|perf|ci|merge)(\([^)]+\))?!?: \S|^Merge ")


ATTRIBUTION = re.compile(
    r"^co-authored-by:|\b(claude|anthropic)\b|generated (with|by)|\bAI[- ](generated|assisted)\b"
    r"|\b(written|authored|created|drafted|made|co-?written)\s+(with|by)\s+(an?\s+)?"
    r"(ai|chatgpt|gpt[-\w.]*|copilot|gemini|llm|codex|cursor)\b",
    re.IGNORECASE | re.MULTILINE,
)


def attributed(messages: Sequence[str]) -> list[str]:
    return [m.splitlines()[0] if m else m for m in messages if ATTRIBUTION.search(m)]


def bad_subjects(subjects: Sequence[str]) -> list[str]:
    return [s for s in subjects if not OK.match(s)]


def main(base: str) -> int:
    p = subprocess.run(
        ["git", "log", "--format=%B%x00", f"{base}..HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        print(p.stderr.strip(), file=sys.stderr)
        return 2
    messages = [m.strip() for m in p.stdout.split("\0") if m.strip()]
    bad = bad_subjects([m.splitlines()[0] for m in messages])
    for s in bad:
        print(f"bad commit subject: {s!r} (want 'type: description')", file=sys.stderr)
    attr = attributed(messages)
    for s in attr:
        print(f"commit has a Co-Authored-By trailer or AI attribution: {s!r}", file=sys.stderr)
    return 1 if bad or attr else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
