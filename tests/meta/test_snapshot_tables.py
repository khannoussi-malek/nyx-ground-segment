import re
from pathlib import Path

from gs.archiver.snapshot import TABLES

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


def test_tables_cover_every_table_but_telemetry() -> None:
    found = {
        m.group(1).lower()
        for f in MIGRATIONS.glob("*.sql")
        for m in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", f.read_text(), re.IGNORECASE
        )
    }
    assert found - {"telemetry"} == set(TABLES)
