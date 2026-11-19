"""D-SIM-12 on the running stack: a leak started and cleared through the API (not the simulator's
own port) reaches the RED LOW alarm row."""

import httpx

from tests.support.compose import api_url
from tests.support.env import dotenv, now, wait_until
from tests.support.http import CLIENT_HEADERS
from tests.support.pg import alarm_events_for

V = "NYX-TA1"
LEAK_WAIT_S = 25.0  # -0.4 kPa/s from 101.3 reaches red_low 95 after ~16 s (design 3.4)


def test_leak_through_the_api_raises_red_low_then_clears() -> None:
    with httpx.Client(base_url=api_url(), headers=CLIENT_HEADERS) as c:
        pw = dotenv()["DEMO_PW_FC1"]
        assert c.post("/api/login", json={"user": "fc1", "password": pw}).status_code == 204
        body = {"vehicle": V, "fault": "leak"}
        t0 = now()
        assert c.post("/api/test-controls/fault", json=body).status_code == 204
        try:
            wait_until(
                lambda: any(
                    r["param"] == "cabin_pressure_kpa" and r["level"] == "RED_LOW"
                    for r in alarm_events_for(V, t0)
                ),
                LEAK_WAIT_S,
            )
        finally:
            assert c.request("DELETE", "/api/test-controls/fault", json=body).status_code == 204
