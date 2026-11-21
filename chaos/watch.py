"""The soak's I4 watcher (design 11.5, D-EXT-15): chaos/watch_console.spec.ts in a Playwright
browser, started with the soak, stopped by a file. Reported in summary.md, not gating (A-EXT-11)."""

import json
import os
import subprocess
from pathlib import Path

from tests.support.env import REPO

STOP_WAIT_S = 90.0  # the spec polls the stop file every second, then writes its result


def i4_result(out_file: Path, rc: int | None) -> str:
    try:
        r = json.loads(out_file.read_text())
    except OSError, ValueError:
        return f"NOT RUN (the watcher wrote no result, exit {rc}; is `make e2e/node_modules` done?)"
    if r["bad"]:
        more = f" (+{len(r['bad']) - 1} more in watch.json)" if len(r["bad"]) > 1 else ""
        return f"FAIL (reported, not gating): {r['bad'][0]}{more}"
    if not r["rows"]:
        return "NOT RUN (the watcher saw no OK row: logged out or console down)"
    return f"PASS ({r['samples']} samples, {r['rows']} OK rows)"


class Watcher:
    def __init__(self, out: Path) -> None:
        self.out_file, self.stop_file = out / "watch.json", out / "watch.stop"
        self.stop_file.unlink(missing_ok=True)
        env = {
            **os.environ,
            "CHAOS_WATCH_OUT": str(self.out_file),
            "CHAOS_WATCH_STOP": str(self.stop_file),
            "CHAOS_WATCH_TRACE": str(out / "trace"),
        }
        self.proc: subprocess.Popen[bytes] | None
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                ["npx", "playwright", "test", "-c", "chaos.config.ts"],  # noqa: S607
                cwd=REPO / "e2e",
                env=env,
                stdout=(out / "watch.log").open("wb"),
                stderr=subprocess.STDOUT,
            )
        except OSError:
            self.proc = None

    def finish(self) -> str:
        self.stop_file.touch()
        rc = None
        if self.proc:
            try:
                rc = self.proc.wait(timeout=STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return i4_result(self.out_file, rc)
