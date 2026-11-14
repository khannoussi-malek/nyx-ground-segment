from datetime import UTC, datetime

from gs.common.interfaces.dlq import schema_reject


def test_schema_reject_fields() -> None:
    now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    rec = schema_reject("{}", "tcack", "1763200000123-5", "bad", now)
    assert (rec.reason, rec.deliveries, rec.stream, rec.entry_id, rec.raw, rec.detail) == (
        "schema", 1, "tcack", "1763200000123-5", "{}", "bad",
    )  # fmt: skip
    assert rec.first_seen == datetime.fromtimestamp(1763200000.123, UTC)
    assert rec.dlq_at == now
