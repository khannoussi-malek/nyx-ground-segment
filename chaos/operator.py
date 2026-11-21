"""Operator traffic (D-EXT-15): isolate_valve every 60 s per vehicle, every RED alarm ACKed with
a note. Errors are expected while the soak breaks things; a failed call is just skipped."""

from typing import Any
from uuid import uuid4

import httpx

from tests.support.compose import api_url
from tests.support.env import dotenv
from tests.support.http import CLIENT_HEADERS

USER = "fc1"


class Operator:
    def __init__(self) -> None:
        self.http = httpx.Client(base_url=api_url(), headers=CLIENT_HEADERS, timeout=5)
        self.sent = self.acked = self.failed = 0

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        r = self.http.request(method, path, json=body)
        if r.status_code == 401:  # no session yet, or the api restarted and lost it
            login = {"user": USER, "password": dotenv()["DEMO_PW_FC1"]}
            self.http.post("/api/login", json=login).raise_for_status()
            r = self.http.request(method, path, json=body)
        return r

    def isolate(self, vehicle: str) -> None:
        body = {"vehicle": vehicle, "name": "isolate_valve", "args": {"valve": "A"}}
        try:
            ok = self._call("POST", "/api/commands", body).status_code == 202
        except httpx.HTTPError:
            ok = False
        self.sent += ok
        self.failed += not ok

    def ack_reds(self) -> None:
        try:
            alarms = self._call("GET", "/api/alarms").json()
            for a in alarms:
                if a["severity"] == "RED" and a["state"] == "UNACK":
                    body = {"request_id": str(uuid4()), "note": "chaos watcher: seen"}
                    r = self._call("POST", f"/api/alarms/{a['alarm_id']}/ack", body)
                    self.acked += r.status_code == 202
        except httpx.HTTPError, ValueError:
            self.failed += 1
