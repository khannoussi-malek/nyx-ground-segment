import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gs.alarms.quality import Quality
from perf.soak_count import (
    Api,
    VehicleCount,
    epoch_rows,
    lost_by_epoch,
    passed,
    render,
    run_sessions,
)

E1 = datetime(2026, 11, 16, 8, tzinfo=UTC)
E2 = E1 + timedelta(hours=1)


def test_lost_by_epoch() -> None:
    assert lost_by_epoch([]) == 0
    assert lost_by_epoch([(E1, 0, 99, 100)]) == 0  # gapless
    assert lost_by_epoch([(E1, 10, 19, 7)]) == 3  # one epoch missing 3
    assert lost_by_epoch([(E1, 0, 9, 8), (E2, 0, 4, 4)]) == 3  # two epochs add up


def _q(false_raises: int | None = 0, chattering: tuple[str, ...] = ()) -> Quality:
    return Quality(5, false_raises, {}, chattering, 0, 0, {"all": 1}, 0.0)


def _c(lost: int = 0, gaps: int = 0, epochs: int = 1, resets: int = 0) -> VehicleCount:
    return VehicleCount("NYX-TA1", 1, 72000, lost, gaps, epochs, resets)


def _f(epoch: datetime, seqs: range) -> list[tuple[datetime, int]]:
    return [(epoch + timedelta(milliseconds=100 * s), s) for s in seqs]


def _lost(sent: list[tuple[datetime, int]], stored: list[tuple[datetime, int]]) -> int:
    return lost_by_epoch(epoch_rows(sent, set(stored)))


def test_sent_vs_stored_sees_head_tail_and_reset_loss() -> None:
    sent = _f(E1, range(100))
    assert _lost(sent, sent) == 0
    assert _lost(sent, sent[5:]) == 5  # head: the first 5 never stored
    assert _lost(sent, sent[:-7]) == 7  # tail: nothing after them exposes a hole
    assert _lost(sent, sent[:40] + sent[50:]) == 10  # inside
    boot2 = _f(E2, range(50))  # a reset: seq drops back, a new epoch
    assert _lost(sent + boot2, sent + boot2[40:]) == 40  # reset with seq > 0: 0..39 never stored
    assert _lost(sent + boot2, sent + boot2) == 0
    assert _lost(sent + sent[:3], sent) == 0  # a sender retry (same frame twice) is one frame


def test_onboard_clock_step_back_is_not_a_reset() -> None:
    # soak 2026-11-17: the VM clock stepped back 1.6 s mid-run; every frame was stored, but
    # ordering by t_onboard split the epoch and counted 5 and 6 "lost" frames
    sent = _f(E1, range(10)) + [(t - timedelta(seconds=1.6), s) for t, s in _f(E1, range(10, 20))]
    assert _lost(sent, sent) == 0
    assert _lost(sent, sent[:12] + sent[13:]) == 1  # a real hole is still seen


def test_every_kind_of_loss_fails_the_soak() -> None:
    sent = _f(E1, range(100)) + _f(E2, range(50))
    for stored in (sent[5:], sent[:-7], sent[:140] + sent[145:], sent[:100] + sent[140:]):
        c = VehicleCount("NYX-TA1", 1, len(stored), _lost(sent, stored), 0)  # GAP column is 0
        assert not passed([c], _q())


def test_summary_pass_rule() -> None:
    assert passed([_c(), _c()], _q())
    assert not passed([_c(), _c(lost=1)], _q())  # one lost frame
    assert not passed([_c()], _q(false_raises=1))
    assert not passed([_c()], _q(false_raises=None))  # unjudged is not a pass
    assert not passed([_c()], _q(chattering=("NYX-TA1/bus_current_a",)))
    assert not passed([_c()], _q(), overflow=1)  # the simulator dropped a frame


def test_render_states_the_verdict_and_the_numbers() -> None:
    ok = render(E1, 2.0, [_c()], 0, _q())
    assert "**PASS**" in ok and "72000" in ok and "soak): 0" in ok
    bad = render(E1, 2.0, [_c(lost=3, gaps=1)], 4, _q(chattering=("NYX-TA1/x",)))
    assert "**FAIL**" in bad and "NYX-TA1/x" in bad and "soak): 4" in bad


@pytest.mark.parametrize("c", [_c(epochs=2), _c(resets=1)])
def test_a_simulator_restart_is_incomplete_not_pass(c: VehicleCount) -> None:
    text = render(E1, 2.0, [_c(), c], 0, _q())  # every other check is clean: the false PASS
    assert "**INCOMPLETE**" in text and "**PASS**" not in text
    assert "| 72000 | 0 | 0 | 1 | 0 |" in text and f"| {c.epochs} | {c.resets} |" in text


def test_a_restart_does_not_hide_behind_a_fail_verdict() -> None:
    assert "**INCOMPLETE**" in render(E1, 2.0, [_c(lost=3, epochs=2)], 0, _q())


class FakeApi(Api):
    def __init__(self, fail_start: str | None = None, fail_stop: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self.fail_start, self.fail_stop = fail_start, fail_stop

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> bytes:
        self.calls.append(f"{method} {path}")
        if method == "POST" and path == "/api/sessions":
            assert body is not None
            if body["vehicle"] == self.fail_start:
                raise OSError("start refused")
            return json.dumps({"id": 1 if body["vehicle"] == "A" else 2}).encode()
        if path.endswith("/stop"):
            if path in self.fail_stop:
                raise OSError("stop refused")
            return json.dumps({"id": 0}).encode()
        raise AssertionError(path)


def test_failed_second_start_stops_the_first_session() -> None:
    api = FakeApi(fail_start="B")
    with pytest.raises(OSError, match="start refused"):
        run_sessions(api, ("A", "B"), lambda: None)
    assert api.calls[-1] == "POST /api/sessions/1/stop"


def test_a_failed_stop_still_stops_the_other_and_reports_all() -> None:
    api = FakeApi(fail_stop=("/api/sessions/1/stop", "/api/sessions/2/stop"))
    with pytest.raises(RuntimeError) as e:
        run_sessions(api, ("A", "B"), lambda: None)
    assert api.calls[-2:] == ["POST /api/sessions/1/stop", "POST /api/sessions/2/stop"]
    assert "A" in str(e.value) and "B" in str(e.value)
    ok = FakeApi(fail_stop=("/api/sessions/1/stop",))
    with pytest.raises(RuntimeError):
        run_sessions(ok, ("A", "B"), lambda: None)
    assert ok.calls[-1] == "POST /api/sessions/2/stop"  # the second is attempted after a failure


def test_an_interrupted_wait_still_stops_both() -> None:
    api = FakeApi()

    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_sessions(api, ("A", "B"), interrupted)
    assert api.calls[-2:] == ["POST /api/sessions/1/stop", "POST /api/sessions/2/stop"]
