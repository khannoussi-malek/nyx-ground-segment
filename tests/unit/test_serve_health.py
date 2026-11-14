import asyncio
import json
from typing import Literal

from gs.common.health import serve_health


async def _get(port: int, path: str) -> tuple[int, dict[str, object]]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    raw = await r.read()
    w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body) if body else {}


async def _probe(
    state: Literal["ok", "degraded", "down"], path: str
) -> tuple[int, dict[str, object]]:
    server = await serve_health("127.0.0.1", 0, lambda: (state, {"buffered": 3}))
    try:
        return await _get(server.sockets[0].getsockname()[1], path)
    finally:
        server.close()
        await server.wait_closed()


async def test_ok_is_200() -> None:
    assert await _probe("ok", "/healthz") == (200, {"status": "ok", "checks": {"buffered": 3}})


async def test_degraded_is_200() -> None:  # R9
    code, body = await _probe("degraded", "/healthz")
    assert code == 200 and body["status"] == "degraded"


async def test_down_is_503() -> None:
    code, body = await _probe("down", "/healthz")
    assert code == 503 and body["status"] == "down"


async def test_other_path_is_404() -> None:
    assert (await _probe("ok", "/other"))[0] == 404


async def _call(port: int, method: str, path: str, body: bytes, length: int | None = None) -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    n = len(body) if length is None else length
    w.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {n}\r\n\r\n".encode() + body)
    raw = await r.read()
    w.close()
    return raw


async def _echo(body: bytes) -> tuple[int, dict[str, object] | None]:
    d = json.loads(body)
    return (204, None) if d.get("ok") else (422, {"got": d})


async def test_routes_post_json_and_204_has_no_body() -> None:
    server = await serve_health("127.0.0.1", 0, lambda: ("ok", {}), {("POST", "/x"): _echo})
    port = server.sockets[0].getsockname()[1]
    try:
        raw = await _call(port, "POST", "/x", b'{"ok": true}')
        assert raw.startswith(b"HTTP/1.1 204 No Content") and raw.endswith(b"\r\n\r\n")
        raw = await _call(port, "POST", "/x", b'{"ok": false}')
        assert raw.startswith(b"HTTP/1.1 422") and json.loads(raw.partition(b"\r\n\r\n")[2]) == {
            "got": {"ok": False}
        }
        assert (await _call(port, "GET", "/x", b"")).startswith(b"HTTP/1.1 404")  # method matters
        big = await _call(port, "POST", "/x", b"", length=65_537)
        assert big.startswith(b"HTTP/1.1 413")
        assert (await _call(port, "POST", "/x", b"{}", length=-1)).startswith(b"HTTP/1.1 400")
        assert (await _get(port, "/healthz"))[0] == 200  # unchanged
    finally:
        server.close()
        await server.wait_closed()
