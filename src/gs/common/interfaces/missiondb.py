"""Response bodies of GET /api/vehicles and GET /api/mission-db (D-EXT-01, ruling G20)."""

from pydantic import ConfigDict

from gs.common.interfaces.base import StrictModel, VehicleId
from gs.common.missiondb import CheckItem, Command, MissionDb, ParameterEntry


class _Resp(StrictModel):
    model_config = ConfigDict(extra="forbid")


class Vehicles(_Resp):
    vehicles: list[VehicleId]
    parameters: dict[str, ParameterEntry]  # in parameters.yaml order


class LimitView(_Resp):
    checks: list[CheckItem]  # CheckItem dumps as its own kind (G21)


class DerivedView(_Resp):
    unit: str
    description: str


class MissionDbView(_Resp):
    set_sha256: str
    limits_sha256: str
    commands_sha256: str
    parameters_sha256: str
    parameters: dict[str, ParameterEntry]
    derived: dict[str, DerivedView]  # computed values, in parameters.yaml order
    limits: dict[str, LimitView]
    commands: dict[str, Command]

    @classmethod
    def of(cls, m: MissionDb) -> MissionDbView:
        return cls(
            set_sha256=m.set_sha256,
            limits_sha256=m.limits_sha256,
            commands_sha256=m.commands_sha256,
            parameters_sha256=m.parameters_sha256,
            parameters=m.parameters,
            derived={
                n: DerivedView(unit=d.unit, description=d.description) for n, d in m.derived.items()
            },
            limits={p: LimitView(checks=list(e.checks)) for p, e in m.limits.items()},
            commands=m.commands,
        )
