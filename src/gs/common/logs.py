"""One JSON object per log line on stderr (D-ENG-07, ENG-05)."""

import json
import logging
import sys
from datetime import UTC, datetime

from gs.common.time import iso_z


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "t": iso_z(datetime.fromtimestamp(record.created, UTC)),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("vehicle", "seq", "hour"):  # from extra={"vehicle": v, "seq": s, "hour": iso}
            if key in record.__dict__:
                out[key] = record.__dict__[key]
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(service: str, level: int = logging.INFO) -> None:
    """Replace the root handlers with one JSON stderr handler."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
