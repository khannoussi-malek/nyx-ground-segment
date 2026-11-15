"""Hot rows <-> Parquet: one row group, zstd, the hot schema, no custom footer metadata."""

import io
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql

from gs.archiver.hours import HourWindow

PG_TO_ARROW: dict[str, pa.DataType] = {
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "text": pa.string(),
    "bigint": pa.int64(),
    "uuid": pa.string(),
    "double precision": pa.float64(),
}
SORT_KEYS = [("t_onboard", "ascending"), ("seq", "ascending")]

Conn = psycopg.AsyncConnection[Any]


async def hot_schema(conn: Conn) -> pa.Schema:
    """The telemetry columns in table order as Arrow fields; an unmappable type is an error."""
    cur = await conn.execute(
        "select column_name, data_type from information_schema.columns"
        " where table_schema = 'public' and table_name = 'telemetry' order by ordinal_position"
    )
    fields = []
    for name, dtype in await cur.fetchall():
        if dtype not in PG_TO_ARROW:
            raise ValueError(f"telemetry.{name}: no Arrow type for {dtype!r}")
        fields.append(pa.field(name, PG_TO_ARROW[dtype]))
    return pa.schema(fields)


async def hot_table(conn: Conn, w: HourWindow, schema: pa.Schema) -> pa.Table:
    """The rows of `w`, sorted (t_onboard, seq); the window is two UTC instants (zone-proof)."""
    query = sql.SQL("select {cols} from telemetry where {where} order by t_onboard, seq").format(
        cols=sql.SQL(", ").join(sql.Identifier(n) for n in schema.names),
        where=sql.SQL(HourWindow.SQL),
    )
    cur = await conn.execute(query, w.params())
    data = await cur.fetchall()
    columns = []
    for i, field in enumerate(schema):
        values = [r[i] for r in data]
        if field.type == pa.string():  # uuid columns arrive as UUID objects
            values = [None if v is None else str(v) for v in values]
        columns.append(pa.array(values, type=field.type))
    return pa.Table.from_arrays(columns, schema=schema)


def encode(t: pa.Table) -> bytes:
    out = io.BytesIO()
    pq.write_table(t, out, compression="zstd", row_group_size=max(len(t), 1))
    return out.getvalue()


def decode(b: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(b))


def sort_rows(t: pa.Table) -> pa.Table:
    return t.sort_by(SORT_KEYS)


def same_rows(a: pa.Table, b: pa.Table) -> bool:
    return bool(sort_rows(a).equals(sort_rows(b)))
