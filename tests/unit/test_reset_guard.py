import subprocess
from collections.abc import Callable, Mapping

import pytest

from scripts.reset_guard import count_rows, force_requested, main

UNKNOWN_MESSAGE = (
    "make reset could not count the archived hours (is the stack up? try make up)."
    " Run FORCE=1 make reset to delete anyway."
)


def _fake_run(
    returncode: int = 0, stdout: str = "0\n"
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)

    return run


def _raising_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError("docker")


def test_reset_refuses_with_archive_rows(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(rows=3, force=False) == 1
    assert capsys.readouterr().out.strip() == (
        "make reset deletes the database, Redis and the MinIO archive (3 archived hours)."
        " Run FORCE=1 make reset to confirm."
    )
    assert main(rows=0, force=False) == 0
    assert main(rows=3, force=True) == 0


def test_reset_refuses_when_count_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(rows=None, force=False) == 1
    assert capsys.readouterr().out.strip() == UNKNOWN_MESSAGE


def test_force_allows_when_count_unknown() -> None:
    assert main(rows=None, force=True) == 0


def test_count_rows_refuses_when_exec_fails() -> None:
    assert count_rows(run=_fake_run(returncode=1, stdout="")) is None
    assert main(rows=count_rows(run=_fake_run(returncode=1, stdout="")), force=False) == 1


def test_count_rows_none_when_docker_missing() -> None:
    assert count_rows(run=_raising_run) is None


def test_count_rows_none_when_output_not_integer() -> None:
    assert count_rows(run=_fake_run(stdout="abc\n")) is None
    assert count_rows(run=_fake_run(stdout="")) is None
    assert count_rows(run=_fake_run(stdout="-1\n")) is None


def test_count_rows_returns_count() -> None:
    assert count_rows(run=_fake_run(stdout="3\n")) == 3
    assert count_rows(run=_fake_run(stdout="0\n")) == 0


def test_count_rows_refuses_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(rows=count_rows(run=_fake_run(stdout="3\n")), force=False) == 1
    assert "(3 archived hours)" in capsys.readouterr().out
    assert main(rows=count_rows(run=_fake_run(stdout="0\n")), force=False) == 0


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"FORCE": "1"}, True),
        ({"FORCE": "0"}, False),
        ({"FORCE": ""}, False),
        ({"FORCE": "yes"}, False),
        ({}, False),
    ],
)
def test_force_requested_only_for_one(env: Mapping[str, str], expected: bool) -> None:
    assert force_requested(env) is expected
