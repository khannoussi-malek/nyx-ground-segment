from datetime import UTC, datetime, timedelta

from gs.ingest.events import link_key
from gs.ingest.link import STALE_S, LinkState, on_frame, on_silence
from gs.ingest.seq import SeqState

V = "NYX-TA1"
T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)


def t_at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def prev_at(seq: int = 0, t: float = 0.0) -> SeqState:
    return SeqState(epoch=T0, last_seq=seq, last_t=t_at(t), period_s=0.1)


def test_gap_over_2s_gives_los_and_aos_once() -> None:
    s, ev, stale = on_frame(LinkState(), V, prev_at(seq=49, t=4.9), t_at(15.0), lost=100)
    assert [e.kind for e in ev] == ["LOS", "AOS"] and stale and not s.los_open
    assert ev[1].details["lost_frames"] == 100 and ev[0].key[4:] == ev[1].key[4:]
    los, aos = ev
    assert (los.key, los.t_onboard, los.vehicle) == (link_key("LOS", V, T0, 49), t_at(4.9), V)
    assert (aos.key, aos.t_onboard) == (link_key("AOS", V, T0, 49), t_at(15.0))
    assert aos.details == {"epoch": T0, "last_seq": 49, "lost_frames": 100}
    # the next in-order frame is quiet
    assert on_frame(s, V, prev_at(seq=150, t=15.0), t_at(15.1), 0) == (s, (), False)


def test_exactly_2s_is_not_stale() -> None:
    assert STALE_S == 2.0
    assert on_frame(LinkState(), V, prev_at(t=0.0), t_at(2.0), 0) == (LinkState(), (), False)
    assert on_frame(LinkState(), V, prev_at(t=0.0), t_at(2.000001), 0)[2] is True


def test_first_frame_is_not_stale() -> None:
    assert on_frame(LinkState(), V, None, t_at(0.0), None) == (LinkState(), (), False)


def test_silence_then_frame() -> None:  # LOS from the read-loop timer, AOS on the next frame
    s, (los,) = on_silence(LinkState(), V, prev_at(seq=9, t=1.0))
    assert s.los_open and (los.t_onboard, los.key) == (t_at(1.0), link_key("LOS", V, T0, 9))
    s2, (aos,), stale = on_frame(s, V, prev_at(seq=9, t=1.0), t_at(1.1), lost=0)
    assert los.kind == "LOS" and aos.kind == "AOS" and not stale and los.key[4:] == aos.key[4:]
    assert not s2.los_open and aos.details == {"epoch": T0, "last_seq": 9, "lost_frames": 0}


def test_silence_then_stale_frame_one_los() -> None:  # open LOS + stale frame → AOS only
    s, _ = on_silence(LinkState(), V, prev_at(seq=9, t=1.0))
    _, ev, stale = on_frame(s, V, prev_at(seq=9, t=1.0), t_at(30.0), lost=289)
    assert [e.kind for e in ev] == ["AOS"] and stale


def test_silence_twice_one_los() -> None:
    s, (los,) = on_silence(LinkState(), V, prev_at(seq=9, t=1.0))
    assert on_silence(s, V, prev_at(seq=9, t=1.0)) == (s, ())
    assert los.kind == "LOS"


def test_silence_without_frames_no_los() -> None:
    assert on_silence(LinkState(), V, None) == (LinkState(), ())


def test_reset_lost_unknown() -> None:
    _, (_, aos), _ = on_frame(LinkState(), V, prev_at(seq=500, t=50.0), t_at(60.0), lost=None)
    assert aos.details["lost_frames"] is None


def test_los_carries_rejected() -> None:
    _, (los,) = on_silence(
        LinkState(), V, prev_at(seq=9, t=1.0), {"schema": 2, "t_onboard_range": 2}
    )
    assert los.details["rejected"] == {"count": 4, "reason": "schema"}  # tie: DlqReason order
    _, (los,) = on_silence(LinkState(), V, prev_at(seq=9), {"poison": 1, "json_invalid": 3})
    assert los.details["rejected"] == {"count": 4, "reason": "json_invalid"}


def test_los_without_rejected_has_no_key() -> None:
    for rej in (None, {}, {"schema": 0}):
        _, (los,) = on_silence(LinkState(), V, prev_at(seq=9), rej)
        assert "rejected" not in los.details
