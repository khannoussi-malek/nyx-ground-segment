from typing import Any

import pytest

from gs.archiver.snapshot import TABLES
from scripts.restore_state import restore


class _Cur:
    def __init__(self, row: tuple[bool]) -> None:
        self.row = row

    def fetchone(self) -> tuple[bool]:
        return self.row


class FakeConn:
    def __init__(self, full: str) -> None:
        self.full = full

    def execute(self, query: str, *a: Any) -> _Cur:
        return _Cur((f"from {self.full}" in query,))


@pytest.mark.parametrize("table", [TABLES[0], "audit_log"])
def test_restore_refuses_non_empty_target(table: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert restore(FakeConn(table), b"never read") == 2  # type: ignore[arg-type]
    assert capsys.readouterr().err.strip() == (
        f"restore_state: target database is not empty ({table} has rows)"
    )
