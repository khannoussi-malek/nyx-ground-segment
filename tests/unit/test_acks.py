"""apply_ack branches (D-ALM-14, ruling R-w)."""

import uuid
from dataclasses import replace

from gs.alarms.contract import Level
from gs.alarms.engine import EngineState, compile_set, initial_state
from gs.common.missiondb import load_dir
from gs.ingest.acks import AckReq, apply_ack
from tests.support.alarms import PERIOD_S, Frame, V, at, feed
from tests.support.mission import MISSION_DIR

CSET = compile_set(load_dir(MISSION_DIR))
TEMP = "cabin_temp_c"
T = at(10.0)


def red_state() -> EngineState:
    frames: list[Frame] = [(i, i * PERIOD_S, {TEMP: 31.0}) for i in range(3)]
    st, _ = feed(initial_state(CSET), CSET, frames)
    return st


def req(alarm_id: uuid.UUID, note: str = "seen, venting") -> AckReq:
    return AckReq(uuid.uuid4(), alarm_id, TEMP, "fc1", note)


def test_apply_ack_applies() -> None:
    st = red_state()
    r = req(st.alarms[TEMP].alarm_id)
    new, tr, result, reason = apply_ack(st, r, V, T)
    assert (result, reason) == ("applied", None)
    assert tr is not None and (tr.kind, tr.key, tr.by_user, tr.note) == (
        "ACK",
        str(r.request_id),
        "fc1",
        "seen, venting",
    )
    assert (tr.alarm_state, tr.acked_sev, tr.value) == ("ACKED", Level.RED, None)
    assert new.alarms[TEMP].state == "ACKED" and st.alarms[TEMP].state == "UNACK"  # pure


def test_already_acked_is_superseded() -> None:
    st = red_state()
    aid = st.alarms[TEMP].alarm_id
    acked, _, _, _ = apply_ack(st, req(aid), V, T)
    new, tr, result, reason = apply_ack(acked, req(aid), V, T)
    assert (new, tr, result, reason) == (acked, None, "superseded", "already acked")


def test_other_or_missing_alarm_is_superseded() -> None:  # R-w
    st = red_state()
    for state in (st, initial_state(CSET)):  # a newer alarm on the param; none open at all
        new, tr, result, reason = apply_ack(state, req(uuid.uuid4()), V, T)
        assert (new, tr, result, reason) == (state, None, "superseded", "alarm no longer open")


def test_rtn_unack_ack_clears() -> None:  # ACK of a returned alarm closes it at apply time
    st = red_state()
    a = st.alarms[TEMP]
    rtn = replace(st, alarms={TEMP: replace(a, state="RTN_UNACK")})
    new, tr, result, _ = apply_ack(rtn, req(a.alarm_id), V, T)
    assert result == "applied" and tr is not None
    assert (tr.alarm_state, tr.closed_at) == ("CLEARED", T) and TEMP not in new.alarms


def test_changed_since_request_is_superseded() -> None:  # P-pf42
    st = red_state()
    r = replace(req(st.alarms[TEMP].alarm_id), changed=True)
    assert apply_ack(st, r, V, T) == (st, None, "superseded", "alarm changed since the request")


def test_seen_key_decides_over_the_margins() -> None:  # P-pf43
    st = red_state()
    base = req(st.alarms[TEMP].alarm_id)
    seen = replace(base, seen_key="k1", last_key="k1", changed=True)  # margin says changed
    assert apply_ack(st, seen, V, T)[2:] == ("applied", None)
    stale = replace(base, seen_key="k1", last_key="k2")
    assert apply_ack(st, stale, V, T)[1:] == (None, "superseded", "alarm changed since you saw it")
    absent = replace(base, last_key="k2", changed=True)  # no key: the margins (P-pf42/42b)
    assert apply_ack(st, absent, V, T)[2:] == ("superseded", "alarm changed since the request")
