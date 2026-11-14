"""Frame classification: raw stream entry -> valid frame or DLQ record (D-ING-05, 18..20). Pure."""

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from gs.common.interfaces import DlqReason, DlqRecord, FrameHeader

log = logging.getLogger(__name__)
DETAIL_MAX = 2048
# highest priority first; anything else is "schema"
_PRECEDENCE: tuple[DlqReason, ...] = ("json_invalid", "vehicle_mismatch", "t_onboard_range")


def entry_time(entry_id: str) -> datetime:
    return datetime.fromtimestamp(int(entry_id.split("-")[0]) / 1000, UTC)


def _reason(errors: list[Any]) -> DlqReason:
    types = {e["type"] for e in errors}
    return next((r for r in _PRECEDENCE if r in types), "schema")


def classify(
    fields: Mapping[bytes, bytes],
    stream: str,
    entry_id: str,
    deliveries: int,
    model: type[FrameHeader],
    now: datetime,
) -> FrameHeader | DlqRecord:
    def dlq(reason: DlqReason, detail: str, raw: str) -> DlqRecord:
        return DlqRecord(
            reason=reason,
            detail=detail[:DETAIL_MAX],
            raw=raw,
            stream=stream,
            entry_id=entry_id,
            deliveries=deliveries,
            first_seen=entry_time(entry_id),
            dlq_at=now,
        )

    data = fields.get(b"data")
    if data is None:
        decoded = {
            k.decode(errors="backslashreplace"): v.decode(errors="backslashreplace")
            for k, v in fields.items()
        }
        return dlq("json_invalid", "missing field 'data'", json.dumps(decoded))
    try:
        return model.model_validate_json(
            data, context={"stream_vehicle": stream.removeprefix("tm:"), "now": now}
        )
    except ValidationError as e:
        errs = e.errors(include_url=False, include_input=False, include_context=False)
        return dlq(_reason(errs), json.dumps(errs), data.decode(errors="backslashreplace"))


class FieldStats:
    """Counts unknown fields and missing registered params; one WARNING per name (D-ING-19/20).
    One instance per process; the worker calls observe() per valid frame."""

    def __init__(self) -> None:
        self.unknown: Counter[str] = Counter()
        self.missing: Counter[str] = Counter()

    def observe(self, frame: FrameHeader, params: Sequence[str]) -> None:
        for name in frame.model_extra or {}:
            self._bump(self.unknown, name, "unknown field %r in frame (not a registered parameter)")
        for name in params:
            if name not in frame.model_fields_set:
                self._bump(self.missing, name, "registered parameter %r missing from frame")

    @staticmethod
    def _bump(counter: Counter[str], name: str, msg: str) -> None:
        counter[name] += 1
        if counter[name] == 1:
            log.warning(msg, name)
