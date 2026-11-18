"""D-ING-07: the XADD cost bound passes when one batch of 1 000 serial XADDs has p99 < 10 ms."""

from tests.support.redisx import xadd_bound_met


def test_best_batch_rule() -> None:
    assert xadd_bound_met([[0.02] * 1000, [0.001] * 1000]) is True
    assert xadd_bound_met([[0.02] * 1000] * 3) is False
    # 990th sorted value (index 989) is 10.1 ms: this batch's p99 is over the bound
    over = [0.001] * 989 + [0.0101] * 11
    assert xadd_bound_met([over]) is False
    assert xadd_bound_met([over, [0.001] * 1000]) is True
