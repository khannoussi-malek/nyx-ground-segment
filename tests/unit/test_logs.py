"""JSON log lines (D-ENG-07, ENG-05)."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from gs.common.logs import JsonFormatter, setup_logging


def last(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    return out


def test_json_line_with_vehicle_and_seq(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("gs-ingest")
    logging.getLogger("x").warning("gap", extra={"vehicle": "NYX-TA1", "seq": 42})
    rec = last(capsys)
    assert (rec["service"], rec["vehicle"], rec["seq"], rec["msg"], rec["level"]) == (
        "gs-ingest", "NYX-TA1", 42, "gap", "WARNING",
    )  # fmt: skip
    assert rec["logger"] == "x" and rec["t"].endswith("Z")


def test_exception_is_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("gs-api")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("failed")
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    rec = json.loads(err)
    assert "ValueError: boom" in rec["exc"] and "Traceback" in rec["exc"]


def test_no_vehicle_key_when_absent(capsys: pytest.CaptureFixture[str]) -> None:
    setup_logging("gs-sim")
    logging.getLogger("x").info("hello %s", "world")
    rec = last(capsys)
    assert rec["msg"] == "hello world"
    assert "vehicle" not in rec and "seq" not in rec and "exc" not in rec


def test_timestamp_is_record_created() -> None:
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
    rec.created = datetime(2026, 11, 15, 8, 0, 0, 500000, tzinfo=UTC).timestamp()
    t = json.loads(JsonFormatter("s").format(rec))["t"]
    assert t == "2026-11-15T08:00:00.500000Z"


def test_setup_replaces_root_handlers_and_sets_level() -> None:
    setup_logging("a", logging.DEBUG)
    setup_logging("b", logging.WARNING)
    root = logging.getLogger()
    assert len(root.handlers) == 1 and root.level == logging.WARNING


def test_uvicorn_leaves_logging_to_the_json_root() -> None:
    from fastapi import FastAPI

    from gs.api.server import build_server

    assert build_server(FastAPI(), "127.0.0.1", 0).config.log_config is None
