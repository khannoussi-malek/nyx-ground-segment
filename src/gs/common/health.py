"""Minimal GET /healthz server shared by the long-running services, plus optional JSON routes."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

State = Literal["ok", "degraded", "down"]
Route = Callable[[bytes], Awaitable[tuple[int, dict[str, object] | None]]]
MAX_BODY = 65_536
MAX_HEADER_LINES = 100
_REASON = {
    200: "OK",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    413: "Content Too Large",
    422: "Unprocessable Content",
    503: "Service Unavailable",
}


async def _read_body(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Headers up to the blank line, then Content-Length bytes; (400|413, b"") when unusable."""
    length = 0
    for _ in range(MAX_HEADER_LINES):
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            try:
                length = int(value)
            except ValueError:
                return 400, b""
    else:
        return 400, b""
    if length < 0:
        return 400, b""
    if length > MAX_BODY:
        return 413, b""
    return 200, await reader.readexactly(length)


async def serve_health(
    host: str,
    port: int,
    status: Callable[[], tuple[State, dict[str, object]]],
    routes: Mapping[tuple[str, str], Route] | None = None,
) -> asyncio.Server:
    """200 ok / 200 degraded / 503 down on GET /healthz, 404 elsewhere (R9).

    `routes` maps (method, path) to a handler taking the JSON body bytes.
    """
    routes = routes or {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # ponytail: no keep-alive; headers are only read for a matched route
            parts = (await asyncio.wait_for(reader.readline(), 5)).split()
            route = (
                routes.get((parts[0].decode("latin-1"), parts[1].decode("latin-1")))
                if (len(parts) >= 2)
                else None
            )
            payload = b""
            if route:
                code, raw = await asyncio.wait_for(_read_body(reader), 5)
                if code == 200:
                    code, out = await route(raw)
                    payload = json.dumps(out).encode() if out is not None else b""
                else:
                    payload = json.dumps({"error": _REASON[code]}).encode()
            elif parts[:2] == [b"GET", b"/healthz"]:
                state, checks = status()
                code = 503 if state == "down" else 200
                payload = json.dumps({"status": state, "checks": checks}).encode()
            else:
                code, payload = 404, json.dumps({"status": "not found"}).encode()
            head = f"HTTP/1.1 {code} {_REASON[code]}\r\n"
            if payload:
                head += "Content-Type: application/json\r\n"
            head += f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            writer.write(head.encode() + payload)
            await writer.drain()
        except TimeoutError, ConnectionError, asyncio.IncompleteReadError:
            pass  # client went away; nothing to report to
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)
