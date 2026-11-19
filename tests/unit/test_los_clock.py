from gs.ingest.worker import RESUME_GRACE_S, los_due

LOS_S = 2.0


def test_no_grace_is_the_plain_silence_rule() -> None:
    assert not los_due(102.0, 100.0, 0.0, LOS_S)
    assert los_due(102.1, 100.0, 0.0, LOS_S)


def test_silence_inside_the_resume_grace_is_not_los() -> None:
    reconnect = 100.0  # the broker is back; the source needs up to RESUME_GRACE_S to resume
    grace_until = reconnect + RESUME_GRACE_S
    assert not los_due(grace_until + LOS_S, 90.0, grace_until, LOS_S)


def test_silence_beyond_grace_plus_los_s_is_los() -> None:
    grace_until = 100.0 + RESUME_GRACE_S
    assert los_due(grace_until + LOS_S + 0.1, 90.0, grace_until, LOS_S)


def test_a_frame_after_the_grace_restarts_the_clock() -> None:
    assert not los_due(120.0, 119.0, 105.0, LOS_S)
