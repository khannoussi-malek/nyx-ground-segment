"""scripts/release_record.py: tag, commit, images, lockfile hashes, open NCRs (R25 F6)."""

import hashlib
from pathlib import Path

import pytest

from scripts import release_record as rr


def test_open_ncrs_lists_only_open(tmp_path: Path) -> None:
    (tmp_path / "NCR-01.md").write_text("# NCR-01: slow\n- **Status:** OPEN (accepted)\n")
    (tmp_path / "NCR-02.md").write_text("# NCR-02: fixed\n- **Status:** CLOSED\n")
    assert rr.open_ncrs(tmp_path) == ["NCR-01: slow"]


def test_ncr_without_status_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "NCR-09.md").write_text("# NCR-09: no status\n")
    with pytest.raises(ValueError, match="NCR-09"):
        rr.open_ncrs(tmp_path)


def test_render(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text("x")
    md = rr.render(
        tag="v1.2.3",
        commit="abc",
        images=["nyx-gs:v sha256:111"],
        locks=[lock],
        ncrs=["NCR-01: slow"],
        matrix="# Verification matrix\n",
    )
    assert "v1.2.3" in md and "abc" in md and "nyx-gs:v sha256:111" in md
    assert hashlib.sha256(b"x").hexdigest() in md
    assert "NCR-01: slow" in md and "# Verification matrix" in md
    assert "none" in rr.render("t", "c", [], [], [], "m")  # no open NCRs says so


def test_real_ncrs_all_carry_a_status() -> None:
    assert rr.open_ncrs(rr.REPO / "docs/ncr")  # parses every NCR; NCR-01 and NCR-02 are open


def test_main_writes_record(tmp_path: Path) -> None:
    (tmp_path / "i.txt").write_text("nyx-gs:v1 sha256:9\n")
    (tmp_path / "m.md").write_text("MATRIX\n")
    out = tmp_path / "r.md"
    argv = ["--tag", "v1", "--commit", "c", "--images", str(tmp_path / "i.txt")]
    assert rr.main([*argv, "--matrix", str(tmp_path / "m.md"), "--out", str(out)]) == 0
    text = out.read_text()
    assert "sha256:9" in text and "MATRIX" in text and "uv.lock" in text and "NCR-01" in text


def test_closed_with_a_note_is_still_closed(tmp_path: Path) -> None:
    (tmp_path / "NCR-04.md").write_text(
        "# NCR-04: x\n- **Status:** CLOSED (fixed in v1.0.1; see below)\n"
    )
    assert rr.open_ncrs(tmp_path) == []


def test_header_says_images_are_a_local_rebuild() -> None:
    assert "local rebuild" in rr.render("t", "c", [], [], [], "m")
