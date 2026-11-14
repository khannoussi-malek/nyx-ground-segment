from datetime import UTC, datetime


def iso_z(dt: datetime) -> str:
    """Format an aware datetime as YYYY-MM-DDTHH:MM:SS.ffffffZ (UTC, always 6 digits)."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime not allowed")
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
