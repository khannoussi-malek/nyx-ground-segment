import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from gs.common.interfaces import build_frame_model
from gs.common.missiondb import load_dir
from gs.simulator.frames import NOMINALS
from tests.support.env import vid
from tests.support.redisx import client, recorded_frames, run_sim


async def test_two_vehicles_stream_at_10hz() -> None:
    v1, v2 = vid(), vid()
    await run_sim([v1, v2], seconds=1.25, user="sim")
    for v in (v1, v2):
        entries: Any = client("admin").xrange(f"tm:{v}")
        assert all(set(f) == {"data"} for _, f in entries)  # D-SIM-04
        frames = [json.loads(f["data"]) for _, f in entries]
        assert [f["seq"] for f in frames] == list(range(len(frames)))
        assert 11 <= len(frames) <= 14
        ts = [f["t_onboard"] for f in frames]
        assert ts == sorted(set(ts))


def test_sim_frames_match_schema() -> None:  # contract, D-EXT-18
    schema = json.loads(Path("interfaces/schemas/frame.schema.json").read_text())
    validator = Draft202012Validator(schema)
    frame_model = build_frame_model(load_dir(Path("mission")).param_names)
    raws = recorded_frames()
    assert raws
    for raw in raws:
        d = json.loads(raw)
        validator.validate(d)
        f = frame_model.model_validate_json(
            raw, context={"stream_vehicle": d["vehicle"], "now": datetime.now(UTC)}
        )
        assert f.model_extra == {} and f.model_fields_set >= set(NOMINALS)
