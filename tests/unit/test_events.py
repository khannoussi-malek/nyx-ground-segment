import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from gs.alarms.contract import Level
from gs.common.missiondb import load_dir
from gs.ingest.events import (
    alarm_id_for,
    applied_key,
    details_json,
    frozen_key,
    link_key,
    poison_key,
    rejected_key,
    reset_key,
    seq_key,
    transition_key,
)
from tests.support.mission import MISSION_DIR
from tests.support.pg import gapless

E = datetime(2026, 11, 14, 10, tzinfo=UTC)


EP = "2026-11-14T10:00:00.000000Z"


def test_keys_are_the_contract() -> None:
    assert seq_key("GAP", "NYX-TA1", E, 50) == f"GAP/NYX-TA1/{EP}/50"
    assert seq_key("SEQ_JUMP", "NYX-TA1", E, 8) == f"SEQ_JUMP/NYX-TA1/{EP}/8"
    assert seq_key("SEQ_RESET", "NYX-TA1", E) == f"SEQ_RESET/NYX-TA1/{EP}"
    assert seq_key("SEQ_ANOMALY", "NYX-TA1", E) == f"SEQ_ANOMALY/NYX-TA1/{EP}"
    assert link_key("LOS", "NYX-TA1", E, 7) == f"LOS/NYX-TA1/{EP}/7"
    assert link_key("AOS", "NYX-TA1", E, 7) == f"AOS/NYX-TA1/{EP}/7"
    assert frozen_key("NYX-TA1", E, 12) == f"FROZEN/NYX-TA1/{EP}/12"
    assert applied_key("NYX-TA1", E, 4) == f"LIMITS_APPLIED/NYX-TA1/{EP}/4"
    assert reset_key("NYX-TA1", E, 99) == f"ENGINE_STATE_RESET/NYX-TA1/{EP}/99"
    assert poison_key("tm:NYX-TA1", "17-0") == "POISON/tm:NYX-TA1/17-0"
    assert rejected_key("ab" * 32) == "LIMITS_REJECTED/" + "ab" * 32
    assert (
        transition_key("NYX-TA1", "cabin_temp_c", E, 9, "RAISE")
        == f"NYX-TA1/cabin_temp_c/{EP}/9/RAISE"
    )


def test_alarm_id_is_deterministic() -> None:
    k = transition_key("NYX-TA1", "p", E, 3, "RAISE")
    assert alarm_id_for(k) == UUID("8b35eb51-ec91-5123-b582-c27dcd7a62a6")  # precomputed uuid5
    assert alarm_id_for(k) != alarm_id_for(k + "x")


def test_level_order() -> None:
    assert Level.NOMINAL < Level.YELLOW < Level.RED


def test_mission_texts_hash_to_file_hashes() -> None:
    db = load_dir(MISSION_DIR)
    assert hashlib.sha256(db.limits_yaml.encode()).hexdigest() == db.limits_sha256
    assert hashlib.sha256(db.commands_yaml.encode()).hexdigest() == db.commands_sha256
    assert hashlib.sha256(db.parameters_yaml.encode()).hexdigest() == db.parameters_sha256


def test_gapless() -> None:
    assert gapless([1, 2, 3]) and gapless([2**32 - 1, 0, 1]) and gapless([90, 91, 3, 4])
    assert not gapless([100, 102]) and not gapless([100, 99]) and not gapless([100, 100])


def test_event_details_times_leave_as_iso_z() -> None:
    assert json.loads(details_json({"t": E, "n": [{"u": E}]})) == {"t": EP, "n": [{"u": EP}]}


def test_new_keys() -> None:
    from gs.ingest.events import late_key, mismatch_key

    u = UUID(int=7)
    assert late_key(u) == f"LATE_VERIFICATION/{u}"
    assert mismatch_key("tcack", "0123456789ab") == "INTERFACE_MISMATCH/tcack/0123456789ab"


def test_archive_corrupt_is_an_event_kind() -> None:
    from typing import get_args

    from gs.ingest.events import EventKind

    assert "ARCHIVE_CORRUPT" in get_args(EventKind)
