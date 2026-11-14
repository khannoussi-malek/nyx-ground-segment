"""Fault injection for the simulator (D-SIM-07, D-SIM-09): request models, board, pure apply()."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from gs.common.health import Route
from gs.common.interfaces import VehicleId
from gs.simulator.nominals import NOMINALS

FaultName = Literal[
    "leak", "overheat", "dropout", "freeze", "seq_jump", "duplicate", "malformed", "step"
]
LEAK_KPA_PER_S, OVERHEAT_C_PER_S = -0.4, 0.5
ONE_SHOT = frozenset({"seq_jump", "duplicate", "malformed"})
Duration = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Timed(_Params):
    duration_s: Duration | None = None  # absent = until DELETE


class _Dropout(_Params):
    duration_s: Duration


class _SeqJump(_Params):
    by: Annotated[int, Field(ge=1)] = 1000


class _Duplicate(_Params):
    count: Annotated[int, Field(ge=1)] = 1


class _Malformed(_Params):
    kind: Literal["json", "schema"] = "json"


class _Step(_Params):
    param: Literal[tuple(NOMINALS)]  # type: ignore[valid-type]  # mission params
    value: Annotated[float, Field(allow_inf_nan=False)]
    duration_s: Duration


_PARAMS: dict[str, type[_Params]] = {
    "leak": _Timed,
    "overheat": _Timed,
    "freeze": _Timed,
    "dropout": _Dropout,
    "seq_jump": _SeqJump,
    "duplicate": _Duplicate,
    "malformed": _Malformed,
    "step": _Step,
}


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    vehicle: VehicleId
    fault: FaultName
    params: dict[str, object] = {}

    @model_validator(mode="after")
    def _check_params(self) -> FaultRequest:
        self.params = _PARAMS[self.fault].model_validate(self.params).model_dump(exclude_none=True)
        return self


class FaultClear(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    vehicle: VehicleId
    fault: FaultName


@dataclass(frozen=True)
class Active:
    name: str
    started: float  # monotonic
    until: float | None
    params: Mapping[str, object]


def apply(
    values: Mapping[str, float],
    active: Sequence[Active],
    now_mono: float,
    frozen_values: Mapping[str, float] | None,
) -> dict[str, float]:
    out = dict(values)
    for a in active:
        elapsed = now_mono - a.started
        if a.name == "leak":
            out["cabin_pressure_kpa"] += LEAK_KPA_PER_S * elapsed
        elif a.name == "overheat":
            out["cabin_temp_c"] += OVERHEAT_C_PER_S * elapsed
        elif a.name == "step":
            out[str(a.params["param"])] = float(str(a.params["value"]))  # exact, no noise
    if frozen_values is not None and any(a.name == "freeze" for a in active):
        return dict(frozen_values)
    return out


class FaultBoard:
    """The one mutable holder shared by the HTTP handler and the tick loop (tuples swapped)."""

    def __init__(self) -> None:
        self._by_vehicle: dict[str, tuple[Active, ...]] = {}

    def add(self, req: FaultRequest, now_mono: float) -> None:
        d = req.params.get("duration_s")
        until = now_mono + float(str(d)) if d is not None else None
        new = Active(req.fault, now_mono, until, dict(req.params))
        kept = tuple(a for a in self._by_vehicle.get(req.vehicle, ()) if a.name != req.fault)
        self._by_vehicle[req.vehicle] = (*kept, new)

    def clear(self, vehicle: str, fault: str) -> None:
        cur = self._by_vehicle.get(vehicle, ())
        self._by_vehicle[vehicle] = tuple(a for a in cur if a.name != fault)

    def active(self, vehicle: str, now_mono: float) -> tuple[Active, ...]:
        """Time-based faults still running (expired ones are dropped); one-shots excluded."""
        cur = self._by_vehicle.get(vehicle, ())
        live = tuple(a for a in cur if a.until is None or a.until > now_mono)
        if live != cur:
            self._by_vehicle[vehicle] = live
        return tuple(a for a in live if a.name not in ONE_SHOT)

    def take_once(self, vehicle: str, name: str) -> Active | None:
        cur = self._by_vehicle.get(vehicle, ())
        hit = next((a for a in cur if a.name == name), None)
        if hit:
            self._by_vehicle[vehicle] = tuple(a for a in cur if a is not hit)
        return hit


def _invalid(e: ValidationError | ValueError) -> tuple[int, dict[str, object]]:
    msg = (
        "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        if isinstance(e, ValidationError)
        else "body is not valid JSON"
    )
    return 422, {"error": "INVALID_FAULT", "message": msg}


def fault_routes(
    board: FaultBoard, vehicles: Sequence[str], monotonic: Callable[[], float]
) -> dict[tuple[str, str], Route]:
    def known(vehicle: str) -> tuple[int, dict[str, object]] | None:
        if vehicle in vehicles:
            return None
        return 422, {"error": "INVALID_FAULT", "message": f"unknown vehicle {vehicle}"}

    async def post(body: bytes) -> tuple[int, dict[str, object] | None]:
        try:
            req = FaultRequest.model_validate_json(body)
        except ValueError as e:  # ValidationError is a ValueError
            return _invalid(e)
        if bad := known(req.vehicle):
            return bad
        board.add(req, monotonic())
        return 204, None

    async def delete(body: bytes) -> tuple[int, dict[str, object] | None]:
        try:
            key = FaultClear.model_validate_json(body)
        except ValueError as e:
            return _invalid(e)
        if bad := known(key.vehicle):
            return bad
        board.clear(key.vehicle, key.fault)
        return 204, None

    return {("POST", "/sim/fault"): post, ("DELETE", "/sim/fault"): delete}
