"""The export file writer, run in the API's one-worker export process (D-EXT-13). Hour by hour from
the window reader into a file, so memory stays at one hour. Imports pyarrow: the API imports this
module only when an export runs (UI-07)."""

import csv
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from gs.api.worker_calc import Outcome, connect, guarded
from gs.archiver.parquet import hot_schema
from gs.archiver.s3 import client
from gs.archiver.window import window_hours
from gs.common.s3_settings import S3Settings
from gs.common.time import iso_z

FIXED = ("vehicle", "seq", "t_onboard", "t_received", "mode", "last_cmd_id")


def cell(v: Any) -> str:
    """One CSV field: times as iso_z (Z), floats via repr (shortest round-trip), NULL empty."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return iso_z(v)
    return repr(v) if isinstance(v, float) else str(v)


async def _write(
    database_url: str, s3: S3Settings, vehicle: str, t_from: datetime, t_to: datetime,
    params: Sequence[str], fmt: str, path: str,
) -> None:  # fmt: skip
    cols = [*FIXED, *params]
    c = client(s3)
    async with await connect(database_url) as conn:
        hot = await hot_schema(conn)
        schema = pa.schema(
            [hot.field(n) if n in hot.names else pa.field(n, pa.float64()) for n in cols]
        )
        hours = window_hours(conn, c, s3.bucket, vehicle, t_from, t_to, params)
        if fmt == "parquet":
            with pq.ParquetWriter(path, schema, compression="zstd") as w:
                async for t in hours:
                    w.write_table(t.select(cols).cast(schema))
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            out = csv.writer(f)
            out.writerow(cols)
            async for t in hours:
                out.writerows(
                    zip(*([cell(x) for x in t[n].to_pylist()] for n in cols), strict=True)
                )


def export(
    database_url: str, s3: S3Settings, vehicle: str, t_from: datetime, t_to: datetime,
    params: Sequence[str], fmt: str, path: str,
) -> Outcome:  # fmt: skip
    """Runs in the export process: the window into `path`; ("ok", None) or a failure marker."""
    return guarded(_write(database_url, s3, vehicle, t_from, t_to, params, fmt, path))
