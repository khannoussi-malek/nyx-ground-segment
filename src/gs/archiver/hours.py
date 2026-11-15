"""Hour arithmetic: bounds are UTC instants, never local hours (session TimeZone is irrelevant)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

HOUR = timedelta(hours=1)
ELIGIBLE_AFTER = timedelta(minutes=10)  # late frames of the hour have had time to arrive


@dataclass(frozen=True)
class HourWindow:
    vehicle: str
    start: datetime
    end: datetime
    SQL: ClassVar[str] = "vehicle = %(vehicle)s AND t_onboard >= %(start)s AND t_onboard < %(end)s"

    def params(self) -> dict[str, object]:
        return {"vehicle": self.vehicle, "start": self.start, "end": self.end}


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"naive datetime {t!r}")
    return t.astimezone(UTC)


def hour_of(t: datetime) -> datetime:
    return _utc(t).replace(minute=0, second=0, microsecond=0)


def hour_bounds(vehicle: str, hour: datetime) -> HourWindow:
    start = _utc(hour)
    if start != hour_of(start):
        raise ValueError(f"{hour!r} is not aligned to a UTC hour")
    return HourWindow(vehicle, start, start + HOUR)


def eligible(hour: datetime, now: datetime) -> bool:
    return hour + HOUR + ELIGIBLE_AFTER <= now


def delete_due(hour: datetime, now: datetime, retention_h: float) -> bool:
    return hour + HOUR <= now - timedelta(hours=retention_h)


def object_key(vehicle: str, hour: datetime, version: int = 0) -> str:
    h = hour_bounds(vehicle, hour).start  # naive or unaligned raises
    if version < 0:
        raise ValueError(f"negative version {version}")
    suffix = f".v{version}" if version else ""
    return f"{vehicle}/{h:%Y/%m/%d}/{h:%H}{suffix}.parquet"


def sidecar(key: str, sha_hex: str) -> tuple[str, bytes]:
    """The `sha256sum` companion object of `key`."""
    return key + ".sha256", f"{sha_hex}  {key.rsplit('/', 1)[-1]}\n".encode()
