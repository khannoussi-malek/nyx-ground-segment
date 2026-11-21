"""chaos I3 recompute loads only the mission sets the vehicle applied (a stored set nobody applied,
such as a test placeholder with empty files, must not stop the final pass)."""

from pathlib import Path

from chaos.recompute import applied_sets
from tests.support.mission import MISSION_DIR


def _row(sha: str, parameters: str) -> dict[str, str]:
    files = {n: (MISSION_DIR / f"{n}.yaml").read_text() for n in ("limits", "commands")}
    return {"set_sha256": sha, "limits_yaml": files["limits"], "commands_yaml": files["commands"],
            "parameters_yaml": parameters}  # fmt: skip


def test_only_applied_sets_are_loaded() -> None:
    good = (Path(MISSION_DIR) / "parameters.yaml").read_text()
    stored = [_row("a" * 64, good), _row("b" * 64, "")]  # b: never applied, unloadable
    sets = applied_sets(stored, [(1, "a" * 64)])
    assert list(sets) == ["a" * 64]
