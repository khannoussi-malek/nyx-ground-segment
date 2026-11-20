"""Route side of the one-worker process pattern shared by compare and export (EX-02): a pool of
one spawned process per feature, one request at a time, the worker's failure markers mapped to
HTTP. Imports no pyarrow (UI-07); the worker side is worker_calc."""

import asyncio
import logging
from collections.abc import Callable, Iterator
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from contextlib import contextmanager
from http import HTTPStatus
from multiprocessing import get_context
from typing import Any

from fastapi import Request

from gs.api.deps import ApiError
from gs.api.replays import _unavailable

log = logging.getLogger(__name__)


def new_pool() -> ProcessPoolExecutor:
    # spawn: no fork of a threaded, event-loop process
    return ProcessPoolExecutor(1, mp_context=get_context("spawn"))


@contextmanager
def one_at_a_time(
    request: Request, name: str, headers: dict[str, str] | None = None
) -> Iterator[None]:
    """429 `<NAME>_BUSY` while another `name` request runs; the flag is freed in finally."""
    st, flag = request.app.state, f"{name}_busy"
    if getattr(st, flag):
        article = "an" if name[0] in "aeiou" else "a"
        raise ApiError(
            HTTPStatus.TOO_MANY_REQUESTS, f"{name.upper()}_BUSY",
            f"{article} {name} is running; retry", headers=headers,
        )  # fmt: skip
    setattr(st, flag, True)  # no await between the check and this line
    try:
        yield
    finally:
        setattr(st, flag, False)


async def run_in_worker(request: Request, name: str, fn: Callable[..., Any], *args: Any) -> Any:
    """`fn(*args)` in the `name` pool; it returns an Outcome (worker_calc.guarded). ok: the result;
    corrupt: 409; s3 / db: 503 (the worker's own words are only logged); a killed worker: the
    dead pool is replaced and the answer is 503."""
    st, pool = request.app.state, f"{name}_pool"
    try:
        kind, out = await asyncio.get_running_loop().run_in_executor(getattr(st, pool), fn, *args)
    except BrokenExecutor as e:
        getattr(st, pool).shutdown(wait=False, cancel_futures=True)
        setattr(st, pool, new_pool())
        raise _unavailable(f"{name} worker", e) from e
    if kind == "ok":
        return out
    log.warning("%s worker: %s: %s", name, kind, out)
    if kind == "corrupt":
        raise ApiError(HTTPStatus.CONFLICT, "ARCHIVE_CORRUPT", out)
    what = "archive store" if kind == "s3" else "database"
    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "UNAVAILABLE", f"{what} unreachable")
