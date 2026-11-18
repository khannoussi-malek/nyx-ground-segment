import pytest

from gs.ingest.worker import _eid


def test_eid_parses_stream_ids() -> None:
    assert _eid("1700000000000-3") == (1700000000000, 3)
    assert _eid("1700000000000") == (1700000000000, 0)  # no sequence part
    assert _eid("7-") == (7, 0)  # empty sequence part


@pytest.mark.parametrize("bad", ["", "-", "abc", "x-1", "1-y", "1-2-3"])
def test_eid_raises_on_a_malformed_id(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid literal"):
        _eid(bad)
