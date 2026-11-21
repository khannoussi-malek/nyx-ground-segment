"""Mission database: loads and validates limits/commands/parameters as one set (D-EXT-01..03/07).

A key whose feature is not built yet (calibration `poly`, allowed_modes, frozen_check) is not in
the models, so a file using it is rejected (extra="forbid").
"""

import hashlib
import re
from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PlainValidator,
    SerializeAsAny,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from gs.alarms.checks import BUILTIN, CHECKS, CheckConfig
from gs.alarms.checks.limit import LimitCheck as LimitCheck  # moved there, re-exported
from gs.alarms.checks.limit import deadband, floor, meets_floor
from gs.alarms.checks.roc import RocCheck as RocCheck  # moved there, re-exported
from gs.common.calibration import Table, eng_range
from gs.common.expr import Expr, ExprError, compile_expr
from gs.common.interfaces.base import Mode

NAME_RE = r"^[a-z][a-z0-9_]{0,62}$"
# frame fields, plus every BaseModel attribute: build_frame_model makes parameters model fields
RESERVED_NAMES = (
    frozenset({"vehicle", "seq", "t_onboard", "t_received", "mode", "last_cmd_id"})
    | {n for n in dir(BaseModel) if re.fullmatch(NAME_RE, n)}
    | {"abs", "min", "max", "sqrt"}
)  # expression function names (D-EXT-05)
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
class Calibration(_M):
    # table form only; a polynomial is an open owner decision (D-EXT-04)
    table: list[list[float]]

    @field_validator("table")
    @classmethod
    def _valid(cls, v: list[list[float]]) -> list[list[float]]:
        Table.from_pairs(v)  # ValueError (≥ 2 rows of 2 finite values, raw strictly increasing)
        return v


class ParameterEntry(_M):
    unit: str
    type: Literal["float"]
    description: str = ""
    calibration: Calibration | None = None


class DerivedEntry(_M):
    unit: str
    description: str = ""
    expr: str | None = None
    rate_of: str | None = None
    window_s: float | None = Field(None, gt=0, le=3600)

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        if (self.expr is None) == (self.rate_of is None and self.window_s is None):
            raise ValueError("a derived parameter needs exactly one of expr or rate_of + window_s")
        if self.expr is None and (self.rate_of is None or self.window_s is None):
            raise ValueError("rate_of and window_s go together")
        if self.expr is not None and (self.rate_of is not None or self.window_s is not None):
            raise ValueError("expr cannot be combined with rate_of or window_s")
        return self


class ParametersFile(_M):
    version: Literal[1]
    parameters: dict[str, ParameterEntry]
    derived: dict[str, DerivedEntry] = {}


# ---- limits.yaml
class LimitDefaults(_M):
    hysteresis_pct: float = Field(2.0, gt=0)
    persistence: int = Field(3, ge=1)


def _check_item(v: object) -> CheckConfig:
    """A `checks` item is validated by the registered kind named in its `type` (D-EXT-01)."""
    if isinstance(v, CheckConfig):
        return v
    kind = v.get("type") if isinstance(v, dict) else None
    known = sorted(CHECKS.keys() - BUILTIN)
    cls = CHECKS.get(kind) if isinstance(kind, str) and kind not in BUILTIN else None
    if cls is None:
        raise ValueError(f"unknown check type {kind!r}; known: {', '.join(known)}")
    return cls.Config.model_validate(v)


class _ChecksUnionSchema:
    """JSON Schema of a `checks` item: discriminated union of every registered kind's Config."""

    @staticmethod
    def __get_pydantic_json_schema__(
        _cs: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        choices = {
            k: CHECKS[k].Config.__pydantic_core_schema__ for k in sorted(CHECKS.keys() - BUILTIN)
        }
        return handler(core_schema.tagged_union_schema(choices, discriminator="type"))


# SerializeAsAny: a dump keeps the item's own kind (red_low, limit_per_s, …), not just the
# CheckConfig base fields (ruling G21); callers narrow with isinstance (G8)
CheckItem = Annotated[
    CheckConfig, PlainValidator(_check_item), _ChecksUnionSchema(), SerializeAsAny()
]


class LimitEntry(_M):
    checks: list[CheckItem]
    checks_by_mode: dict[Mode, list[CheckItem]] = {}  # {MODE: []} = not monitored (D-EXT-06)

    @model_validator(mode="after")
    def _one_per_type(self) -> LimitEntry:
        for cs in (self.checks, *self.checks_by_mode.values()):
            types = [c.type for c in cs]
            if len(types) != len(set(types)):
                raise ValueError("at most one check per type (slot key is (param, type))")
        return self

    def all_checks(self) -> list[CheckConfig]:
        return [*self.checks, *(c for cs in self.checks_by_mode.values() for c in cs)]


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
class DerivedSpec:
    unit: str
    expr: Expr | None
    rate_of: str | None
    window_s: float | None
    description: str = ""


@dataclass(frozen=True)
class MissionDb:
    parameters: dict[str, ParameterEntry]  # raw parameters, file order
    limits: dict[str, LimitEntry]
    commands: dict[str, Command]
    param_names: tuple[str, ...]  # every stored engineering column: raw, then derived
    raw_names: tuple[str, ...]  # the frame fields (frozen set)
    derived_names: tuple[str, ...]
    raw_columns: tuple[str, ...]  # `<name>_raw` of each calibrated raw parameter
    calibrations: Mapping[str, Table]
    default_persistence: int  # limits.yaml `defaults`, used by the built-in calibration_range
    derived: Mapping[str, DerivedSpec]
    limits_sha256: str
    commands_sha256: str
    parameters_sha256: str
    set_sha256: str
    limits_yaml: str  # the file texts, as stored in limits_versions
    commands_yaml: str
    parameters_yaml: str


def changed_parameters(old: MissionDb, new: MissionDb) -> dict[str, list[str]]:
    """Parameters in both sets whose calibration table or unit differs, with what changed
    (R23 M1, A-R23-1: stored engineering values are not recomputed, so the change is audited)."""

    def units(m: MissionDb) -> dict[str, str]:
        return {
            **{n: p.unit for n, p in m.parameters.items()},
            **{n: d.unit for n, d in m.derived.items()},
        }

    ou, nu = units(old), units(new)
    out: dict[str, list[str]] = {}
    for n in nu.keys() & ou.keys():
        what = [
            *(["calibration"] if old.calibrations.get(n) != new.calibrations.get(n) else []),
            *(["unit"] if ou.get(n) != nu.get(n) else []),
        ]
        if what:
            out[n] = what
    return dict(sorted(out.items()))


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


def _fill(c: CheckConfig, d: LimitDefaults) -> CheckConfig:
    upd: dict[str, Any] = {}
    if c.persistence is None:
        upd["persistence"] = d.persistence
    if isinstance(c, LimitCheck) and c.hysteresis_pct is None and c.hysteresis_abs is None:
        upd["hysteresis_pct"] = d.hysteresis_pct
    return c.model_copy(update=upd)


def _resolve(limits: LimitsFile) -> dict[str, LimitEntry]:
    """Fill inherited defaults so consumers never look at LimitsFile.defaults."""
    d = limits.defaults
    return {
        name: LimitEntry(
            checks=[_fill(c, d) for c in e.checks],
            checks_by_mode={m: [_fill(c, d) for c in cs] for m, cs in e.checks_by_mode.items()},
        )
        for name, e in limits.limits.items()
    }


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
        for c in entry.all_checks():
            if isinstance(c, LimitCheck) and not meets_floor(c):
                d, f = deadband(c), floor(c)
                raise MissionDbError(_LIMITS, f"{name}: deadband {d:g} below floor {f:g}")


def _check_calibration(p: ParametersFile, limits: dict[str, LimitEntry]) -> dict[str, Table]:
    """Tables, and their engineering range must cover red limits plus deadband (D-EXT-04)."""
    out: dict[str, Table] = {}
    for name, e in p.parameters.items():
        if e.calibration is None:
            continue
        out[name] = t = Table.from_pairs(e.calibration.table)
        lo, hi = eng_range(t)
        for c in limits[name].all_checks() if name in limits else ():
            if not isinstance(c, LimitCheck):
                continue
            db = deadband(c)
            if (c.red_low is not None and lo > c.red_low - db) or (
                c.red_high is not None and hi < c.red_high + db
            ):
                raise MissionDbError(
                    _PARAMETERS,
                    f"{name}: calibration table range {lo:g}..{hi:g} must cover the red limits"
                    f" plus the deadband ({db:g})",
                )
    return out


def _check_derived(p: ParametersFile) -> dict[str, DerivedSpec]:
    """Names, one form, inputs known and earlier in the file (so no cycle, EX-12)."""
    raw_cols = [f"{n}_raw" for n, e in p.parameters.items() if e.calibration is not None]
    _check_names(p.derived, _PARAMETERS, "derived parameter", reserved=True)
    _check_names(raw_cols, _PARAMETERS, "raw column", reserved=False)
    everything = [*p.parameters, *p.derived, *raw_cols]
    dup = sorted({n for n in everything if everything.count(n) > 1})
    if dup:
        raise MissionDbError(
            _PARAMETERS,
            f"names must be unique across parameters, derived and _raw columns: {', '.join(dup)}",
        )
    known = set(p.parameters)
    out: dict[str, DerivedSpec] = {}
    for name, d in p.derived.items():
        expr: Expr | None = None
        inputs = [d.rate_of] if d.rate_of is not None else []
        if d.expr is not None:
            if not d.expr.isascii():  # Python NFKC-folds identifiers: `ａ` would compile as `a`
                raise MissionDbError(_PARAMETERS, f"{name}: expr must be ASCII")
            try:
                expr = compile_expr(d.expr)
            except ExprError as e:
                raise MissionDbError(_PARAMETERS, f"{name}: {e}") from e
            inputs = list(expr.names)
        for i in inputs:
            if not _NAME.fullmatch(i) or i not in known:
                raise MissionDbError(
                    _PARAMETERS,
                    f"{name}: input {i!r} is not a raw parameter or a derived one defined earlier",
                )
        known.add(name)
        out[name] = DerivedSpec(d.unit, expr, d.rate_of, d.window_s, d.description)
    return out


Hashes = tuple[str, str, str]  # sha256 hex of limits, commands, parameters


def set_hashes(limits: bytes, commands: bytes, parameters: bytes) -> tuple[Hashes, str]:
    """sha256 of each file and of the set ('<h>\\n' of limits, commands, parameters, in order)."""
    lim, cmd, par = (hashlib.sha256(b).hexdigest() for b in (limits, commands, parameters))
    return (lim, cmd, par), hashlib.sha256(f"{lim}\n{cmd}\n{par}\n".encode()).hexdigest()


def load(limits: bytes, commands: bytes, parameters: bytes) -> MissionDb:
    p_doc = _read(parameters, _PARAMETERS, "parameters")
    p = _validate(
        ParametersFile, p_doc, _PARAMETERS, "derived" if "derived" in p_doc else "parameters"
    )
    lim = _validate(LimitsFile, _read(limits, _LIMITS, "limits"), _LIMITS, "limits")
    cmd = _validate(CommandsFile, _read(commands, _COMMANDS, "commands"), _COMMANDS, "commands")
    _check_names(p.parameters, _PARAMETERS, "parameter", reserved=True)
    _check_names(cmd.commands, _COMMANDS, "command", reserved=False)
    for c_name, c in cmd.commands.items():
        _check_names(c.args, _COMMANDS, f"argument of {c_name}", reserved=False)
    if not p.parameters:
        raise MissionDbError(_PARAMETERS, "at least one raw parameter is required (D-EXT-09)")
    derived = _check_derived(p)
    unknown = [k for k in lim.limits if k not in p.parameters and k not in derived]
    if unknown:
        raise MissionDbError(_LIMITS, f"limits for unknown parameter(s): {', '.join(unknown)}")
    resolved = _resolve(lim)
    _check_floor(resolved)
    calibrations = _check_calibration(p, resolved)
    hashes, set_sha = set_hashes(limits, commands, parameters)
    return MissionDb(
        parameters=dict(p.parameters),
        limits=resolved,
        commands=_resolve_commands(cmd),
        param_names=(*p.parameters, *derived),
        raw_names=tuple(p.parameters),
        derived_names=tuple(derived),
        raw_columns=tuple(f"{n}_raw" for n in calibrations),
        calibrations=calibrations,
        default_persistence=lim.defaults.persistence,
        derived=derived,
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
