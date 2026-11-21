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
        bad, rows, samples = list(r["bad"]), int(r["rows"]), int(r["samples"])
    except OSError:
        return f"NOT RUN (the watcher wrote no result, exit {rc}; is `make e2e/node_modules` done?)"
    except ValueError, KeyError, TypeError:
        return f"NOT RUN (watch.json is malformed, exit {rc})"
    if bad:
        more = f" (+{len(bad) - 1} more in watch.json)" if len(bad) > 1 else ""
        return f"FAIL (reported, not gating): {bad[0]}{more}"
    if not rows:
        return "NOT RUN (the watcher saw no OK row: logged out or console down)"
    return f"PASS ({samples} samples, {rows} OK rows)"


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
        self.log = (out / "watch.log").open("wb")
        try:
            self.proc = subprocess.Popen(  # noqa: S603
                ["npx", "playwright", "test", "-c", "chaos.config.ts"],  # noqa: S607
                cwd=REPO / "e2e",
                env=env,
                stdout=self.log,
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
        self.log.close()
        return i4_result(self.out_file, rc)
