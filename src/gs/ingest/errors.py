"""Postgres error classes and the poison rule's numbers (D-ING-04, D-ING-24, A-R2R-1)."""

from typing import Literal

POISON_DELIVERIES = 3  # times_delivered > this → DLQ + POISON, before processing
RETRIES = 3  # in-process attempts per frame after a data error


class OperationalDbError(Exception):
    """A non-data Postgres error in the frame path: batch rolled back, counters reset; exit 1."""


def pg_error_class(sqlstate: str | None) -> Literal["data", "operational"]:
    """Classes 22 (data exception) and 23 (integrity) are the frame's fault; the rest, incl.
    None (no server reply), are operational."""
    return "data" if sqlstate is not None and sqlstate[:2] in ("22", "23") else "operational"
