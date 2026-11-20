"""Stand-ins for gs.api.compare_calc.compute that fail the way a real compare worker can. Module
level so the spawned worker can import them by name."""

import os
from dataclasses import replace
from datetime import datetime
from typing import Any

from gs.api import compare_calc
from gs.common.s3_settings import S3Settings

Args = tuple[Any, ...]


def dead_s3(database_url: str, s3: S3Settings, *rest: Any) -> Any:
    return compare_calc.compute(database_url, replace(s3, endpoint="127.0.0.1:1"), *rest)


def dead_db(database_url: str, *rest: Any) -> Any:
    return compare_calc.compute("postgresql://u@127.0.0.1:1/x", *rest)


def killed(*_: Any) -> Any:
    os._exit(1)


def corrupt(database_url: str, s3: S3Settings, jobs: Any, *rest: Any) -> Any:
    from gs.archiver.window import ArchiveCorrupt

    when = datetime(2026, 11, 1, 10, tzinfo=jobs[0][1].tzinfo)
    return ("corrupt", compare_calc.corrupt_text(ArchiveCorrupt("V1", when, "a", None)))
