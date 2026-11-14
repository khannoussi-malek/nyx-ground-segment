import hashlib
from datetime import UTC, datetime

from gs.alarms.contract import Level
from gs.common.missiondb import load_dir
from gs.ingest.events import (
    alarm_id_for,
    link_key,
    poison_key,
    rejected_key,
    seq_key,
    transition_key,
)
from tests.support.mission import MISSION_DIR
from tests.support.pg import gapless

E = datetime(2026, 11, 14, 10, tzinfo=UTC)


def test_keys_are_the_contract() -> None:
    assert seq_key("GAP", "NYX-TA1", E, 50) == "GAP/NYX-TA1/2026-11-14T10:00:00.000000Z/50"
    assert seq_key("SEQ_RESET", "NYX-TA1", E) == "SEQ_RESET/NYX-TA1/2026-11-14T10:00:00.000000Z"
    assert link_key("AOS", "NYX-TA1", E, 7)[4:] == link_key("LOS", "NYX-TA1", E, 7)[4:]
    assert poison_key("tm:NYX-TA1", "17-0") == "POISON/tm:NYX-TA1/17-0"
    assert rejected_key("ab" * 32) == "LIMITS_REJECTED/" + "ab" * 32
    assert transition_key("NYX-TA1", "cabin_temp_c", E, 9, "RAISE").endswith("/9/RAISE")


def test_alarm_id_is_deterministic() -> None:
    k = transition_key("NYX-TA1", "p", E, 3, "RAISE")
    assert alarm_id_for(k) == alarm_id_for(k) != alarm_id_for(k + "x")


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
