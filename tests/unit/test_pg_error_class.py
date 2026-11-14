import pytest

from gs.ingest.errors import POISON_DELIVERIES, RETRIES, pg_error_class


@pytest.mark.parametrize(
    ("code", "want"),
    [
        ("22003", "data"),
        ("22023", "data"),
        ("23505", "data"),
        ("23503", "data"),
        ("08006", "operational"),
        ("57P01", "operational"),
        ("25006", "operational"),
        ("53100", "operational"),
        ("57014", "operational"),
        ("40P01", "operational"),
        ("42P01", "operational"),
        (None, "operational"),
        ("XX000", "operational"),
        ("2", "operational"),
        ("", "operational"),
    ],
)
def test_class(code: str | None, want: str) -> None:
    assert pg_error_class(code) == want


def test_design_numbers() -> None:
    assert (POISON_DELIVERIES, RETRIES) == (3, 3)
