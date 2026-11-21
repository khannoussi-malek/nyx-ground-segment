"""checks_by_mode, load part (D-EXT-06); the engine part comes with its own task."""

from tests.support.mission import files, ld


def test_absent_mode_has_no_entry_and_empty_list_is_kept() -> None:
    lim = files()["limits"].replace(
        b"  cabin_temp_c: {checks: [", b"  cabin_temp_c: {checks_by_mode: {SAFE: []}, checks: ["
    )
    e = ld(files(limits=lim)).limits["cabin_temp_c"]
    assert tuple(e.checks_by_mode["SAFE"]) == () and "TEST" not in e.checks_by_mode
    assert len(e.checks) == 1
