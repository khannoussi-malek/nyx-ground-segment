"""Command tracker: tcack and echo evidence → command states, once per owner pass (D-CMD-05).
Stub: Track A Task 7 implements it; the worker's call site is final."""

from typing import TYPE_CHECKING

import psycopg

if TYPE_CHECKING:
    from gs.ingest.worker import Ctx


async def track_pass(
    ctx: Ctx, conn: psycopg.AsyncConnection, vehicle: str, caught_up: bool
) -> None:
    """`caught_up`: this pass did the `>` read and got fewer than COUNT entries (P-pf2); only
    then may timeouts be judged. Never raises (errors count in ctx.counters)."""
    return
