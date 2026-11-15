"""Check registry (D-EXT-01): every module in this package registers its check kinds.

Adding a check kind = dropping one file here with an `@register` class (ALM-07); the mission-db
validates `limits.yaml` checks through `CHECKS`, so the new kind is accepted without other edits.
"""

import importlib
import pkgutil
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from gs.alarms.contract import Level

Side = Literal["LOW", "HIGH", "RISING", "FALLING"]


@dataclass(frozen=True)
class Sample:
    seq: int
    t: float  # t_onboard, epoch seconds
    value: float
    period_s: float


@dataclass(frozen=True)
class Reading:
    level: Level
    side: Side | None
    value: float | None = None  # what was judged: the sample (limit), the slope per s (roc)


class CheckConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    type: str
    persistence: int | None = Field(None, ge=1)


class Check(Protocol):
    kind: ClassVar[str]
    Config: ClassVar[type[CheckConfig]]
    State: ClassVar[type]  # frozen dataclass of JSON-native fields

    def __init__(self, cfg: CheckConfig) -> None: ...
    def initial(self) -> object: ...
    def step(self, state: object, s: Sample, current: Level) -> tuple[object, Reading | None]:
        """`None` reading = no opinion (e.g. too few samples)."""
        ...


CHECKS: dict[str, type[Check]] = {}


def register[C: Check](cls: type[C]) -> type[C]:
    if CHECKS.setdefault(cls.kind, cls) is not cls:
        raise ValueError(f"check kind {cls.kind!r} is already registered")
    return cls


def load_all() -> None:
    for m in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{m.name}")


load_all()
