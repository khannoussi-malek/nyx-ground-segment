"""Compare freshly generated schemas against the committed interfaces/ tree."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*.json")}


def stale_files(generated_root: Path) -> list[str]:
    committed = REPO / "interfaces"
    out = []
    for rel in sorted(_files(generated_root) | _files(committed)):
        a, b = generated_root / rel, committed / rel
        if not (a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()):
            out.append(rel)
    return out
