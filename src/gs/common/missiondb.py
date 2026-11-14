"""Mission database: loads and validates limits/commands/parameters as one set (D-EXT-01..03/07).

A key whose feature is not built yet (calibration, derived, checks_by_mode, allowed_modes,
frozen_check) is not in the models, so a file using it is rejected (extra="forbid").
"""

import hashlib
import re
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

NAME_RE = r"^[a-z][a-z0-9_]{0,62}$"
# frame fields, plus every BaseModel attribute: build_frame_model makes parameters model fields
RESERVED_NAMES = frozenset({"vehicle", "seq", "t_onboard", "t_received", "mode", "last_cmd_id"}) | {
    n for n in dir(BaseModel) if re.fullmatch(NAME_RE, n)
}
MAX_FILE_BYTES = 256 * 1024
RECEIVE_TIMEOUT_MAX_S = 5.0
VERIFY_TIMEOUT_MAX_S = 15.0

_NAME = re.compile(NAME_RE)
_LIMITS, _COMMANDS, _PARAMETERS = "limits.yaml", "commands.yaml", "parameters.yaml"


class MissionDbError(Exception):
    def __init__(self, file: str, message: str) -> None:
        super().__init__(f"{file}: {message}")
        self.file = file
        self.message = message


class _M(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


# ---- parameters.yaml
class ParameterEntry(_M):
    unit: str
    type: Literal["float"]
    description: str = ""


class ParametersFile(_M):
    version: Literal[1]
    parameters: dict[str, ParameterEntry]


# ---- limits.yaml
class LimitDefaults(_M):
    hysteresis_pct: float = Field(2.0, gt=0)
    persistence: int = Field(3, ge=1)


class LimitCheck(_M):
    type: Literal["limit"]
    red_low: float | None = None
    yellow_low: float | None = None
    yellow_high: float | None = None
    red_high: float | None = None
    hysteresis_pct: float | None = Field(None, gt=0)
    hysteresis_abs: float | None = Field(None, gt=0)
    persistence: int | None = Field(None, ge=1)

    @model_validator(mode="after")
    def _shape(self) -> LimitCheck:
        lows = (self.red_low, self.yellow_low)
        highs = (self.yellow_high, self.red_high)
        bounds = [v for v in (*lows, *highs) if v is not None]
        if not bounds:
            raise ValueError("a limit check needs at least one bound")
        if any(a >= b for a, b in zip(bounds, bounds[1:], strict=False)):
            raise ValueError("bounds must satisfy red_low < yellow_low < yellow_high < red_high")
        if self.hysteresis_pct is not None and self.hysteresis_abs is not None:
            raise ValueError("set hysteresis_pct or hysteresis_abs, not both")
        one_sided = all(v is None for v in lows) or all(v is None for v in highs)
        if one_sided and self.hysteresis_abs is None:
            raise ValueError("a one-sided limit requires hysteresis_abs")
        return self


class RocCheck(_M):
    type: Literal["roc"]
    limit_per_s: float = Field(gt=0)
    window_s: float = Field(gt=0)
    direction: Literal["rising", "falling", "both"] = "both"
    severity: Literal["YELLOW", "RED"]
    persistence: int | None = Field(None, ge=1)


Check = Annotated[LimitCheck | RocCheck, Field(discriminator="type")]


class LimitEntry(_M):
    checks: list[Check]

    @model_validator(mode="after")
    def _one_per_type(self) -> LimitEntry:
        types = [c.type for c in self.checks]
        if len(types) != len(set(types)):
            raise ValueError("at most one check per type (slot key is (param, type))")
        return self


class LimitsFile(_M):
    version: Literal[1]
    defaults: LimitDefaults = LimitDefaults()
    limits: dict[str, LimitEntry]


# ---- commands.yaml
class EnumArg(_M):
    type: Literal["enum"]
    values: list[str] = Field(min_length=1)
    required: bool = False


class NumArg(_M):
    type: Literal["float", "int"]
    min: float
    max: float
    unit: str = ""
    required: bool = False

    @model_validator(mode="after")
    def _range(self) -> NumArg:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


Arg = Annotated[EnumArg | NumArg, Field(discriminator="type")]


class CommandDefaults(_M):
    receive_timeout_s: float = Field(RECEIVE_TIMEOUT_MAX_S, gt=0, le=RECEIVE_TIMEOUT_MAX_S)
    verify_timeout_s: float = Field(VERIFY_TIMEOUT_MAX_S, gt=0, le=VERIFY_TIMEOUT_MAX_S)


class Command(_M):
    description: str
    hazardous: bool
    hazard: str | None = None
    args: dict[str, Arg] = {}
    receive_timeout_s: float | None = Field(None, gt=0, le=RECEIVE_TIMEOUT_MAX_S)
    verify_timeout_s: float | None = Field(None, gt=0, le=VERIFY_TIMEOUT_MAX_S)

    @model_validator(mode="after")
    def _hazard(self) -> Command:
        if self.hazardous and not self.hazard:
            raise ValueError("hazard text is required when hazardous is true")
        return self


class CommandsFile(_M):
    version: Literal[1]
    defaults: CommandDefaults = CommandDefaults()
    commands: dict[str, Command]


@dataclass(frozen=True)
class MissionDb:
    parameters: dict[str, ParameterEntry]
    limits: dict[str, LimitEntry]
    commands: dict[str, Command]
    param_names: tuple[str, ...]
    limits_sha256: str
    commands_sha256: str
    parameters_sha256: str
    set_sha256: str
    limits_yaml: str  # the file texts, as stored in limits_versions
    commands_yaml: str
    parameters_yaml: str


# ---- reading
class _NoDupLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep)


def _read(raw: bytes, file: str, section: str) -> dict[str, Any]:
    if len(raw) > MAX_FILE_BYTES:
        raise MissionDbError(file, f"file is {len(raw)} bytes, limit is {MAX_FILE_BYTES}")
    try:
        for ev in yaml.parse(raw, Loader=yaml.SafeLoader):
            if getattr(ev, "anchor", None) is not None:
                raise MissionDbError(file, "YAML anchors and aliases are not allowed")
        doc = yaml.load(raw, Loader=_NoDupLoader)  # noqa: S506  (SafeLoader subclass)
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError, TypeError) as e:
        raise MissionDbError(file, f"invalid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise MissionDbError(file, "top level must be a mapping")
    return doc


def _validate[T: _M](model: type[T], doc: dict[str, Any], file: str, section: str) -> T:
    try:
        out = model.model_validate(doc)
    except ValidationError as e:
        raise MissionDbError(file, str(e)) from e
    if list(doc)[-1] != section:
        raise MissionDbError(file, f"`{section}` must be the last top-level key")
    return out


def _check_names(names: list[str] | dict[str, Any], file: str, what: str, reserved: bool) -> None:
    for n in names:
        if not _NAME.fullmatch(n):
            raise MissionDbError(file, f"{what} name {n!r} does not match {NAME_RE}")
        if reserved and n in RESERVED_NAMES:
            raise MissionDbError(file, f"{what} name {n!r} is reserved")


def _resolve(limits: LimitsFile) -> dict[str, LimitEntry]:
    """Fill inherited defaults so consumers never look at LimitsFile.defaults."""
    d = limits.defaults
    out: dict[str, LimitEntry] = {}
    for name, entry in limits.limits.items():
        checks: list[LimitCheck | RocCheck] = []
        for c in entry.checks:
            upd: dict[str, Any] = {}
            if c.persistence is None:
                upd["persistence"] = d.persistence
            if isinstance(c, LimitCheck) and c.hysteresis_pct is None and c.hysteresis_abs is None:
                upd["hysteresis_pct"] = d.hysteresis_pct
            checks.append(c.model_copy(update=upd))
        out[name] = LimitEntry(checks=checks)
    return out


def load(limits: bytes, commands: bytes, parameters: bytes) -> MissionDb:
    p = _validate(
        ParametersFile, _read(parameters, _PARAMETERS, "parameters"), _PARAMETERS, "parameters"
    )
    lim = _validate(LimitsFile, _read(limits, _LIMITS, "limits"), _LIMITS, "limits")
    cmd = _validate(CommandsFile, _read(commands, _COMMANDS, "commands"), _COMMANDS, "commands")
    _check_names(p.parameters, _PARAMETERS, "parameter", reserved=True)
    _check_names(cmd.commands, _COMMANDS, "command", reserved=False)
    for c_name, c in cmd.commands.items():
        _check_names(c.args, _COMMANDS, f"argument of {c_name}", reserved=False)
    unknown = [k for k in lim.limits if k not in p.parameters]
    if unknown:
        raise MissionDbError(_LIMITS, f"limits for unknown parameter(s): {', '.join(unknown)}")
    hashes = [hashlib.sha256(b).hexdigest() for b in (limits, commands, parameters)]
    set_sha = hashlib.sha256("".join(f"{h}\n" for h in hashes).encode()).hexdigest()
    return MissionDb(
        parameters=dict(p.parameters),
        limits=_resolve(lim),
        commands=dict(cmd.commands),
        param_names=tuple(p.parameters),
        limits_sha256=hashes[0],
        commands_sha256=hashes[1],
        parameters_sha256=hashes[2],
        set_sha256=set_sha,
        limits_yaml=limits.decode(),  # _read already rejected non-UTF-8
        commands_yaml=commands.decode(),
        parameters_yaml=parameters.decode(),
    )


def load_dir(path: Path) -> MissionDb:
    raws: list[bytes] = []
    for n in (_LIMITS, _COMMANDS, _PARAMETERS):
        try:
            raws.append((path / n).read_bytes())
        except OSError as e:
            raise MissionDbError(n, f"cannot read: {e}") from e
    return load(*raws)
