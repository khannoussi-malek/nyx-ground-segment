"""Compare maths and the worker that runs in the API's one-worker compare process (D-EXT-11).
Imports pyarrow, so the API imports this module only when a compare runs (UI-07 start budget)."""

from collections.abc import Sequence
from datetime import datetime

import pyarrow as pa
import pyarrow.compute as pc

from gs.api.worker_calc import Outcome, connect, guarded
from gs.archiver.s3 import client
from gs.archiver.window import window_hours
from gs.common.s3_settings import S3Settings

TARGET_BUCKETS = 1500
MIN_BUCKET_S = 0.1
US = 1_000_000
Bucket = tuple[float, float, float, float, int]  # t_rel_s, lo, hi, mean, n
Job = tuple[str, datetime, datetime]  # vehicle, session start, end


def bucket_width_s(durations_s: Sequence[float]) -> float:
    return max(max(durations_s) / TARGET_BUCKETS, MIN_BUCKET_S)


def session_end(ended_at: datetime | None, now: datetime) -> datetime:
    return now if ended_at is None else ended_at


def buckets(
    tables: Sequence[pa.Table], start: datetime, width_s: float, param: str
) -> list[Bucket]:
    """min/max/mean/count of `param` per bucket of `width_s` since `start`; NULLs skipped, empty
    buckets absent. One table per hour: concatenated, so a bucket across an hour edge is one."""
    if not tables:
        return []
    t = pa.concat_tables([x.select(["t_onboard", param]) for x in tables])
    rel = pc.subtract(t["t_onboard"], pa.scalar(start, pa.timestamp("us", tz="UTC")))
    width_us = round(width_s * US)
    idx = pc.divide(rel.cast(pa.int64()), width_us)  # int divide; rel >= 0, so it floors
    v = t[param]
    out = (
        pa.table({"b": idx, "v": v})
        .filter(pc.is_valid(v))
        .group_by("b")
        .aggregate([("v", "min"), ("v", "max"), ("v", "mean"), ("v", "count")])
        .sort_by("b")
    )
    return [
        (b * width_us / US, lo, hi, mean, n)
        for b, lo, hi, mean, n in zip(
            out["b"].to_pylist(), out["v_min"].to_pylist(), out["v_max"].to_pylist(),
            out["v_mean"].to_pylist(), out["v_count"].to_pylist(), strict=True,
        )
    ]  # fmt: skip


async def _compute(
    database_url: str, s3: S3Settings, jobs: Sequence[Job], params: Sequence[str], width_s: float
) -> list[list[Bucket]]:
    out: list[list[Bucket]] = []
    c = client(s3)
    async with await connect(database_url) as conn:
        for vehicle, start, end in jobs:
            tables = [
                t async for t in window_hours(conn, c, s3.bucket, vehicle, start, end, params)
            ]
            out.extend(buckets(tables, start, width_s, p) for p in params)
    return out


def compute(
    database_url: str, s3: S3Settings, jobs: Sequence[Job], params: Sequence[str], width_s: float
) -> Outcome:
    """Runs in the compare process: one list per (session, param), sessions in `jobs` order."""
    return guarded(_compute(database_url, s3, jobs, params, width_s))
