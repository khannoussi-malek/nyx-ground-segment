"""GS-EXT-04: committed JSON Schemas must equal what the pydantic models generate."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gs.api.app import ROUTER, create_app
from gs.api.openapi import build_openapi
from gs.api.settings import Settings
from gs.common.interfaces.export import write_all
from tests.support.gen import REPO, gen_types, stale_files


def test_committed_schemas_are_fresh(tmp_path: Path) -> None:
    write_all(tmp_path)
    assert stale_files(tmp_path) == []


def test_negative_control_model_edit_is_stale(tmp_path: Path) -> None:  # D-EXT-17
    shutil.copytree(REPO / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    f = tmp_path / "src/gs/common/interfaces/frame.py"
    text = f.read_text()
    line = "    last_cmd_id: UUID | None\n"
    assert text.count(line) == 1
    f.write_text(text.replace(line, line + "    negative_control: int = 0\n"))
    subprocess.run(
        [sys.executable, "-m", "gs.common.interfaces.export", "--out", str(tmp_path / "out")],
        env={**os.environ, "PYTHONPATH": str(tmp_path / "src")},
        check=True,
    )
    assert "schemas/frame.schema.json" in stale_files(tmp_path / "out")


def test_mission_edit_leaves_no_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # EX-01: nothing committed depends on mission/
    shutil.copytree(REPO / "mission", tmp_path / "mission")
    p = tmp_path / "mission/parameters.yaml"
    p.write_text(p.read_text() + "  cabin_humidity_pct: {unit: '%', type: float}\n")
    monkeypatch.setenv("MISSION_DB", str(tmp_path / "mission"))
    write_all(tmp_path / "out")
    assert stale_files(tmp_path / "out") == []


def test_openapi_has_interface_components(tmp_path: Path) -> None:
    write_all(tmp_path)
    doc = json.loads((tmp_path / "openapi.json").read_text())
    assert {"FrameHeader", "DlqRecord", "Hello", "Snapshot", "Tm", "Hb"} <= set(
        doc["components"]["schemas"]
    )
    assert {"/api/login", "/api/logout", "/api/me", "/api/events", "/healthz"} <= set(doc["paths"])
    assert doc["x-generated"] == "make gen - do not edit"
    assert stale_files(tmp_path) == []


def test_schema_dts_is_deterministic_and_fresh(tmp_path: Path) -> None:  # A-EXT-13
    write_all(tmp_path)
    first = gen_types(tmp_path / "openapi.json", tmp_path / "a.d.ts")
    assert gen_types(tmp_path / "openapi.json", tmp_path / "b.d.ts") == first
    assert first == (REPO / "ui/src/api/schema.d.ts").read_bytes()


def test_negative_control_openapi_edit_changes_schema_dts(tmp_path: Path) -> None:
    write_all(tmp_path)
    doc = json.loads((tmp_path / "openapi.json").read_text())
    doc["components"]["schemas"]["Tm"]["properties"]["negative_control"] = {"type": "integer"}
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(doc))
    assert gen_types(edited, tmp_path / "c.d.ts") != (REPO / "ui/src/api/schema.d.ts").read_bytes()


def test_served_openapi_is_the_built_document(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            redis_url="redis://x",
            database_url="postgresql://x",
            session_secret="s",
            users_file=tmp_path / "u.yaml",
            mission_db=tmp_path,
            vehicles=(),
            ui_dist=tmp_path,
        )
    )
    assert app.openapi() == build_openapi(ROUTER.routes)


def test_ack_request_carries_seen_key() -> None:  # P-pf43, owner-approved contract change
    spec = json.loads((REPO / "interfaces/openapi.json").read_text())
    body = spec["components"]["schemas"]["AckRequest"]
    assert "seen_key" in body["properties"] and "seen_key" not in body["required"]
    assert "seen_key?: string | null;" in (REPO / "ui/src/api/schema.d.ts").read_text()
