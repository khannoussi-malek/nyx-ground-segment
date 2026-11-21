"""delete_hot and merge_late refuse a non-autocommit connection with an explicit error."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from gs.archiver.job import delete_hot, merge_late


async def test_delete_hot_needs_autocommit() -> None:
    conn = SimpleNamespace(autocommit=False)
    with pytest.raises(RuntimeError, match="delete_hot needs an autocommit connection"):
        await delete_hot(cast(Any, None), cast(Any, conn), cast(Any, None), cast(Any, None))


async def test_merge_late_needs_autocommit() -> None:
    conn = SimpleNamespace(autocommit=False)
    with pytest.raises(RuntimeError, match="merge_late needs an autocommit connection"):
        await merge_late(cast(Any, None), cast(Any, conn), cast(Any, None))
