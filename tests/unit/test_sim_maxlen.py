def test_maxlen_holds_the_soak_window_with_margin() -> None:
    from gs.simulator.main import MAXLEN

    assert MAXLEN == 108_000 and MAXLEN >= 2 * 3600 * 10 * 1.25  # 2 h soak at 10 Hz + 25 %
