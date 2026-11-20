"""Stand-ins for gs.api.export_calc.export that fail the way a real export worker can. Module
level so the spawned worker can import them by name."""

import os
from dataclasses import replace
from typing import Any

from gs.api import export_calc
from gs.common.s3_settings import S3Settings


def dead_s3(database_url: str, s3: S3Settings, *rest: Any) -> Any:
    return export_calc.export(database_url, replace(s3, endpoint="127.0.0.1:1"), *rest)


def dead_db(database_url: str, *rest: Any) -> Any:
    return export_calc.export("postgresql://u@127.0.0.1:1/x", *rest)


def killed(*_: Any) -> Any:
    os._exit(1)
