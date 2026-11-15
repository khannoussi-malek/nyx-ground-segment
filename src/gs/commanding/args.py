"""One argument check for the API (422 INVALID_ARGS) and the simulator (tcack INVALID_ARGS).

Both sides read the same `mission/commands.yaml` bounds (D-ENG-17, D-EXT-07).
"""

import json
from collections.abc import Mapping
from uuid import UUID

from gs.common.missiondb import Command, EnumArg


def readable_cmd_id(raw: str) -> UUID | None:
    """The `cmd_id` of a stream entry that failed its model, when it is still a UUID string
    (D-ING-27: the tc rejection answers it, the tcack rejection fails that command)."""
    try:
        return UUID(json.loads(raw)["cmd_id"])
    except ValueError, KeyError, TypeError, AttributeError, RecursionError:  # not a UUID string
        return None


def check_args(cmd: Command | None, args: Mapping[str, object]) -> str | None:
    """None when `args` are valid for `cmd`; otherwise the operator text naming the problem."""
    if cmd is None:
        return "unknown command"
    for name in args:
        if name not in cmd.args:
            return f"unknown arg {name}"
    for name, spec in cmd.args.items():
        if name not in args:
            if spec.required:
                return f"missing required arg {name}"
            continue
        v = args[name]
        if isinstance(spec, EnumArg):
            if not isinstance(v, str):
                return f"{name}: expected one of {spec.values}"
            if v not in spec.values:
                return f"{name}: {v!r} is not one of {spec.values}"
            continue
        if (
            isinstance(v, bool)
            or not isinstance(v, int | float)
            or (spec.type == "int" and not isinstance(v, int))
        ):
            return f"{name}: expected {spec.type}"
        if not spec.min <= v <= spec.max:  # NaN fails every comparison
            return f"{name}: {v} is outside [{spec.min}, {spec.max}]"
    return None
