"""Release record for a v* tag (R25 F6, ECSS-E-ST-40C Annex G): tag, commit, image digests,
lockfile hashes, open NCRs and the as-run verification matrix, in one markdown file.
Run: python -m scripts.release_record --tag T --commit SHA --images F --matrix F --out F"""

import argparse
import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCKS = ("uv.lock", "ui/package-lock.json", "e2e/package-lock.json")


def open_ncrs(ncr_dir: Path) -> list[str]:
    out = []
    for f in sorted(ncr_dir.glob("NCR-*.md")):
        text = f.read_text(encoding="utf-8")
        title = text.splitlines()[0].removeprefix("# ")
        if not (m := re.search(r"^- \*\*Status:\*\* (OPEN|CLOSED)", text, re.M)):
            raise ValueError(f"{f.name}: no '- **Status:** OPEN|CLOSED' line")
        if m[1] == "OPEN":
            out.append(title)
    return out


def render(
    tag: str, commit: str, images: list[str], locks: list[Path], ncrs: list[str], matrix: str
) -> str:
    sha = [f"- `{p.name if p.parent == REPO else p.as_posix()}`: {_sha(p)}" for p in locks]
    return (
        f"# Release record {tag}\n\n- Tag: {tag}\n- Commit: {commit}\n\n## Images\n\n"
        + "".join(f"- `{i}`\n" for i in images)
        + "\n## Lockfile SHA-256\n\n"
        + "\n".join(sha)
        + "\n\n## Open NCRs (known problems)\n\n"
        + ("".join(f"- {n}\n" for n in ncrs) or "none\n")
        + "\n"
        + matrix
    )


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--images", type=Path, required=True, help="one 'name digest' per line")
    ap.add_argument("--matrix", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    imgs = [ln for ln in a.images.read_text(encoding="utf-8").splitlines() if ln.strip()]
    md = render(
        a.tag,
        a.commit,
        imgs,
        [REPO / p for p in LOCKS],
        open_ncrs(REPO / "docs/ncr"),
        a.matrix.read_text(encoding="utf-8"),
    )
    a.out.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
