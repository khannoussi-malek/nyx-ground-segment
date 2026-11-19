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
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PlainValidator,
    SerializeAsAny,
    ValidationError,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from gs.alarms.checks import CHECKS, CheckConfig
from gs.alarms.checks.limit import LimitCheck as LimitCheck  # moved there, re-exported
from gs.alarms.checks.limit import deadband, floor, meets_floor
from gs.alarms.checks.roc import RocCheck as RocCheck  # moved there, re-exported

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


def _check_item(v: object) -> CheckConfig:
    """A `checks` item is validated by the registered kind named in its `type` (D-EXT-01)."""
    if isinstance(v, CheckConfig):
        return v
    kind = v.get("type") if isinstance(v, dict) else None
    cls = CHECKS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise ValueError(f"unknown check type {kind!r}; known: {', '.join(sorted(CHECKS))}")
    return cls.Config.model_validate(v)


class _ChecksUnionSchema:
    """JSON Schema of a `checks` item: discriminated union of every registered kind's Config."""

    @staticmethod
    def __get_pydantic_json_schema__(
        _cs: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        choices = {k: CHECKS[k].Config.__pydantic_core_schema__ for k in sorted(CHECKS)}
        return handler(core_schema.tagged_union_schema(choices, discriminator="type"))


# SerializeAsAny: a dump keeps the item's own kind (red_low, limit_per_s, …), not just the
# CheckConfig base fields (ruling G21); callers narrow with isinstance (G8)
CheckItem = Annotated[
    CheckConfig, PlainValidator(_check_item), _ChecksUnionSchema(), SerializeAsAny()
]


class LimitEntry(_M):
    checks: list[CheckItem]

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


_UNSET = "unset: the file's `defaults` value"


class Command(_M):
    description: str
    hazardous: bool
    hazard: str | None = None
    args: dict[str, Arg] = {}
    # unset → the file's `defaults` at load (_resolve_commands), so consumers read only these
    receive_timeout_s: float = Field(
        RECEIVE_TIMEOUT_MAX_S, gt=0, le=RECEIVE_TIMEOUT_MAX_S, description=_UNSET
    )
    verify_timeout_s: float = Field(
        VERIFY_TIMEOUT_MAX_S, gt=0, le=VERIFY_TIMEOUT_MAX_S, description=_UNSET
    )

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
        checks: list[CheckItem] = []
        for c in entry.checks:
            upd: dict[str, Any] = {}
            if c.persistence is None:
                upd["persistence"] = d.persistence
            if isinstance(c, LimitCheck) and c.hysteresis_pct is None and c.hysteresis_abs is None:
                upd["hysteresis_pct"] = d.hysteresis_pct
            checks.append(c.model_copy(update=upd))
        out[name] = LimitEntry(checks=checks)
    return out


def _resolve_commands(cmd: CommandsFile) -> dict[str, Command]:
    """P-pf3: a timeout a command does not set comes from the file's `defaults`."""
    d = cmd.defaults
    return {
        name: c.model_copy(
            update={
                f: getattr(d, f)
                for f in ("receive_timeout_s", "verify_timeout_s")
                if f not in c.model_fields_set
            }
        )
        for name, c in cmd.commands.items()
    }


def _check_floor(limits: dict[str, LimitEntry]) -> None:
    """Deadband floor (D-ALM-02), on every limit check of every parameter."""
    for name, entry in limits.items():
        for c in entry.checks:
            if isinstance(c, LimitCheck) and not meets_floor(c):
                d, f = deadband(c), floor(c)
                raise MissionDbError(_LIMITS, f"{name}: deadband {d:g} below floor {f:g}")


Hashes = tuple[str, str, str]  # sha256 hex of limits, commands, parameters


def set_hashes(limits: bytes, commands: bytes, parameters: bytes) -> tuple[Hashes, str]:
    """sha256 of each file and of the set ('<h>\\n' of limits, commands, parameters, in order)."""
    lim, cmd, par = (hashlib.sha256(b).hexdigest() for b in (limits, commands, parameters))
    return (lim, cmd, par), hashlib.sha256(f"{lim}\n{cmd}\n{par}\n".encode()).hexdigest()


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
    resolved = _resolve(lim)
    _check_floor(resolved)
    hashes, set_sha = set_hashes(limits, commands, parameters)
    return MissionDb(
        parameters=dict(p.parameters),
        limits=resolved,
        commands=_resolve_commands(cmd),
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
