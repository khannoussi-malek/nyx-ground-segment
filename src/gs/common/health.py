"""Minimal GET /healthz server shared by the long-running services."""

import asyncio
import json
from collections.abc import Callable
from typing import Literal

State = Literal["ok", "degraded", "down"]
_REASON = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}


async def serve_health(
    host: str, port: int, status: Callable[[], tuple[State, dict[str, object]]]
) -> asyncio.Server:
    """200 ok / 200 degraded / 503 down on GET /healthz, 404 elsewhere (R9)."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # ponytail: request line only; headers are never read, so no keep-alive
            parts = (await asyncio.wait_for(reader.readline(), 5)).split()
            if parts[:2] == [b"GET", b"/healthz"]:
                state, checks = status()
                code = 503 if state == "down" else 200
                body: dict[str, object] = {"status": state, "checks": checks}
            else:
                code, body = 404, {"status": "not found"}
            payload = json.dumps(body).encode()
            head = f"HTTP/1.1 {code} {_REASON[code]}\r\nContent-Type: application/json\r\n"
            head += f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            writer.write(head.encode() + payload)
            await writer.drain()
        except TimeoutError, ConnectionError:
            pass  # client went away; nothing to report to
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)
