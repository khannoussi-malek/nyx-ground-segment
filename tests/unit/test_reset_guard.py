import pytest

from scripts.reset_guard import main


def test_reset_refuses_with_archive_rows(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(rows=3, force=False) == 1
    assert capsys.readouterr().out.strip() == (
        "make reset deletes the database, Redis and the MinIO archive (3 archived hours)."
        " Run FORCE=1 make reset to confirm."
    )
    assert main(rows=0, force=False) == 0
    assert main(rows=3, force=True) == 0
