"""uvicorn entry point whose SIGTERM/SIGINT also ends every SSE loop (S6: 11.1 s -> ~1.2 s)."""

import types

import uvicorn
from fastapi import FastAPI

from gs.api.app import create_app
from gs.api.settings import Settings
from gs.common.config import require


class GracefulServer(uvicorn.Server):
    def handle_exit(self, sig: int, frame: types.FrameType | None) -> None:
        # ponytail: plain set(); uvicorn's 0.1 s main-loop tick wakes the loop, no threadsafe call
        app = self.config.app
        assert isinstance(app, FastAPI)
        app.state.shutdown.set()
        super().handle_exit(sig, frame)
        # uvicorn re-raises captured signals after shutdown (exit 143); a clean stop must exit 0
        self._captured_signals.clear()


def build_server(app: FastAPI, host: str, port: int) -> GracefulServer:
    return GracefulServer(
        uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=1, log_level="info")
    )


def main() -> None:
    server = build_server(
        create_app(Settings.from_env()), require("BIND_HOST"), int(require("API_PORT"))
    )
    server.run()
