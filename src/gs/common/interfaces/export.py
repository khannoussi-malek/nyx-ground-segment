"""Export the interface models as JSON Schema files (GS-EXT-04, pydantic-first)."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from gs.api.app import ROUTER
from gs.api.openapi import build_openapi
from gs.common.interfaces import (
    Alarm,
    Cmd,
    DlqRecord,
    FrameHeader,
    Ground,
    Hb,
    Hello,
    Link,
    ReplayEvent,
    Snapshot,
    Tc,
    TcAck,
    Tm,
    UiEvent,
)
from gs.common.interfaces.render import render
from gs.common.missiondb import CommandsFile, LimitsFile, ParametersFile

OPENAPI = "openapi.json"

# path under interfaces/ -> model; code-derived only (EX-01: nothing from mission/ content)
EXPORTS: dict[str, type[BaseModel]] = {
    "schemas/frame.schema.json": FrameHeader,
    "schemas/dlq.schema.json": DlqRecord,
    "schemas/tc.schema.json": Tc,
    "schemas/tcack.schema.json": TcAck,
    "schemas/ui-event.schema.json": UiEvent,
    "schemas/sse/alarm.schema.json": Alarm,
    "schemas/sse/cmd.schema.json": Cmd,
    "schemas/sse/link.schema.json": Link,
    "schemas/sse/ground.schema.json": Ground,
    "schemas/sse/hello.schema.json": Hello,
    "schemas/sse/snapshot.schema.json": Snapshot,
    "schemas/sse/tm.schema.json": Tm,
    "schemas/sse/hb.schema.json": Hb,
    "schemas/sse/replay.schema.json": ReplayEvent,
    "schemas/mission/limits.schema.json": LimitsFile,
    "schemas/mission/commands.schema.json": CommandsFile,
    "schemas/mission/parameters.schema.json": ParametersFile,
}


def render_openapi() -> str:  # routes only: no settings or connections needed
    return json.dumps(build_openapi(ROUTER.routes), indent=2, sort_keys=True) + "\n"


def write_all(root: Path) -> list[Path]:
    written = []
    for rel, model in EXPORTS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(model), encoding="utf-8")
        written.append(path)
    api = root / OPENAPI
    api.write_text(render_openapi(), encoding="utf-8")
    return [*written, api]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("interfaces"))
    for p in write_all(ap.parse_args().out):
        print(p)


if __name__ == "__main__":
    main()
