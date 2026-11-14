import sys

import pytest

from tests.support.env import sh


def test_sh_failure_shows_captured_output() -> None:
    code = "import sys; print('out-marker'); print('err-marker', file=sys.stderr); sys.exit(3)"
    with pytest.raises(AssertionError) as e:
        sh(sys.executable, "-c", code)
    assert (
        "exited 3" in str(e.value) and "out-marker" in str(e.value) and "err-marker" in str(e.value)
    )
