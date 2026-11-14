from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from gs.ingest.events import GroundEvent, seq_key
from gs.ingest.seq import MOD, SeqState, is_new, step

V = "NYX-TA1"
T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
EPOCH = T0  # t of the first frame fed

Frame = tuple[int, datetime]
Result = tuple[str, tuple[GroundEvent, ...]]


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def feed(seqs: Iterable[int], start_t: float = 0.0, period: float = 0.1) -> list[Frame]:
    """t follows the on-board counter, so a resent seq carries the same t_onboard."""
    seqs = list(seqs)
    return [(q, at(start_t + ((q - seqs[0]) % MOD) * period)) for q in seqs]


def run(frames: list[Frame], s: SeqState | None = None) -> tuple[SeqState | None, list[Result]]:
    out: list[Result] = []
    for q, t in frames:
        s, k, ev = step(s, V, q, t)
        out.append((k, ev))
    return s, out


def events(frames: list[Frame]) -> list[GroundEvent]:
    return [e for _, ev in run(frames)[1] for e in ev]


def classes(frames: list[Frame]) -> list[str]:
    return [k for k, _ in run(frames)[1]]


def test_t1_nominal_no_events() -> None:
    assert events(feed(range(100))) == []
    assert classes(feed(range(3))) == ["FIRST", "IN_ORDER", "IN_ORDER"]


def test_t2_dropout_one_gap() -> None:  # ING-04
    ev = events(feed(range(50)) + feed(range(150, 200), start_t=5.0 + 10.0))
    (g,) = ev
    assert (g.kind, g.details["from_seq"], g.details["lost"]) == ("GAP", 50, 100)
    assert abs(g.details["lost_by_time"] - 100) <= 1 and g.key == seq_key("GAP", V, EPOCH, 50)
    assert (g.vehicle, g.t_onboard, g.details["epoch"]) == (V, at(15.0), EPOCH)


def test_t3_duplicate() -> None:
    frames = feed([0, 1, 2, 3, 4, 4, 5])
    assert classes(frames)[5] == "DUP_OR_LATE" and events(frames) == []
    assert classes(frames)[6] == "IN_ORDER"


def test_t4_redelivery_after_gap_no_second_gap() -> None:
    s, _ = run(feed(range(50)) + feed(range(150, 200), start_t=15.0))
    s2, out = run(feed(range(145, 151), start_t=14.5), s)
    assert [k for k, _ in out] == ["DUP_OR_LATE"] * 6
    assert [e for _, ev in out for e in ev] == [] and s2 == s  # state unchanged


def test_t5_seq_jump() -> None:
    (j,) = events(feed(range(10)) + [(1010, at(1.0))])
    assert (j.kind, j.details["from_seq"], j.details["lost"], j.details["lost_by_time"]) == (
        "SEQ_JUMP",
        10,
        1000,
        0,
    )
    assert j.key == seq_key("SEQ_JUMP", V, EPOCH, 10)


def test_t6_restart_is_reset_not_dup() -> None:
    frames = feed(range(31)) + feed(range(5), start_t=3.0 + 1.5)
    s, out = run(frames)
    assert [k for k, _ in out][31:] == ["SEQ_RESET"] + ["IN_ORDER"] * 4
    (r,) = [e for _, ev in out for e in ev]
    assert (r.kind, r.key, r.t_onboard) == ("SEQ_RESET", seq_key("SEQ_RESET", V, at(4.5)), at(4.5))
    assert r.details == {"epoch": at(4.5), "prev_epoch": EPOCH, "from_seq": 30}
    assert s is not None and s.epoch == at(4.5)


def test_t7_restart_first_seen_3() -> None:
    new = at(60.0)
    r, g = events(feed(range(500)) + [(3, new)])
    assert (r.kind, r.key) == ("SEQ_RESET", seq_key("SEQ_RESET", V, new))
    assert (g.kind, g.key, g.details["from_seq"], g.details["lost"]) == (
        "GAP",
        seq_key("GAP", V, new, 0),
        0,
        3,
    )
    assert g.details["epoch"] == new


def test_t8_wrap() -> None:
    assert events(feed([MOD - 2, MOD - 1, 0, 1])) == []
    (g,) = events(feed([MOD - 2, MOD - 1]) + [(2, at(0.4))])  # 0.3 s after MOD-1
    assert (g.kind, g.details["from_seq"], g.details["lost"]) == ("GAP", 0, 2)


FRAMES = (
    feed(range(50))
    + feed(range(150, 200), start_t=15.0)
    + feed([200, 1500], start_t=20.0)  # GAP by time
    + [(1500, at(150.0)), (3000, at(150.1))]  # DUP, SEQ_JUMP
    + feed(range(3, 10), start_t=200.0)  # SEQ_RESET + GAP
    + feed([700], start_t=300.0)  # SEQ_JUMP (counter far ahead of the clock)
    + [(100, at(301.0))]  # SEQ_ANOMALY
)


def test_t9_deterministic() -> None:
    assert run(FRAMES) == run(FRAMES)
    kinds = [e.kind for e in events(FRAMES)]
    assert kinds == ["GAP", "GAP", "SEQ_JUMP", "SEQ_RESET", "GAP", "SEQ_JUMP", "SEQ_ANOMALY"]


def test_backward_jump_above_50_is_anomaly() -> None:
    s, out = run(feed(range(501)) + [(100, at(60.0))])
    k, (a,) = out[-1]
    assert (k, a.kind, a.key) == ("SEQ_ANOMALY", "SEQ_ANOMALY", seq_key("SEQ_ANOMALY", V, at(60.0)))
    assert a.details == {"epoch": at(60.0), "prev_epoch": EPOCH, "from_seq": 500}
    assert s is not None and (s.epoch, s.last_seq) == (at(60.0), 100)


def test_period_tracks_100hz() -> None:
    s, _ = run(feed(range(200), period=0.01))
    assert s is not None and abs(s.period_s - 0.01) < 1e-4
    # a gap at 100 Hz is judged against the learned period: 0.51 s after seq 199, seq 250 = 50 lost
    s2, out = run([(250, s.last_t + timedelta(seconds=0.51))], s)
    ((_, (g,)),) = out
    assert (g.kind, g.details["lost"]) == ("GAP", 50)


def test_period_ignores_gaps_and_dups() -> None:
    s, _ = run(feed(range(10)))
    assert s is not None
    s2, _ = run([(20, s.last_t + timedelta(seconds=1.0))], s)
    assert s2 is not None and s2.period_s == s.period_s


def test_is_new() -> None:
    assert not is_new("DUP_OR_LATE")
    assert all(
        is_new(k) for k in ("FIRST", "IN_ORDER", "GAP", "SEQ_JUMP", "SEQ_RESET", "SEQ_ANOMALY")
    )
