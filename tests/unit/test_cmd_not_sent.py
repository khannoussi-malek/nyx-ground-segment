"""not_sent(): replies that come after the XADD may have run stay ambiguous (P-pf16/38)."""

import pytest
from redis._parsers.base import BaseParser

from gs.api.commands import not_sent


@pytest.mark.parametrize(
    "reply",
    [
        "ERR EXEC without MULTI",  # MULTI refused: the XADD ran on its own, then EXEC failed
        "ERR Wrong number of response items from pipeline execution",
    ],
)
def test_post_exec_errors_are_ambiguous(reply: str) -> None:
    assert not not_sent(BaseParser.parse_error(reply))  # type: ignore[no-untyped-call]


def test_a_refused_transaction_is_definite() -> None:
    assert not_sent(BaseParser.parse_error("EXECABORT Transaction discarded"))  # type: ignore[no-untyped-call]
