"""The chaos invariant checkers (D-EXT-15, design 11.5): each fails on a synthetic violation and
passes on a clean ledger."""

import uuid
from datetime import UTC, datetime, timedelta

from chaos.invariants import (
    AlarmRow,
    Command,
    Gap,
    LedgerLine,
    check_i1,
    check_i2,
    check_i3_paths,
    check_i3_recompute,
    check_i6,
    check_i7,
    check_i8,
    cutoff,
    fault_window,
)
from gs.alarms.contract import TransitionKind
from gs.alarms.quality import QEvent

T0 = datetime(2026, 11, 20, 10, tzinfo=UTC)
V = "NYX-TA1"
NOW = T0 + timedelta(minutes=10)
DUE = NOW - timedelta(seconds=30)


def t(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def clean(n: int = 100, fate: str = "sent") -> list[LedgerLine]:
    return [LedgerLine(V, i, t(i * 0.1), fate) for i in range(n)]


def keys(lines: list[LedgerLine]) -> list[tuple[str, datetime, int]]:
    return [(x.vehicle, x.t, x.seq) for x in lines if x.fate == "sent"]


def test_cutoff_waits_for_the_last_action_plus_60_s() -> None:
    assert cutoff(NOW, None) == DUE
    assert cutoff(NOW, NOW - timedelta(seconds=59)) is None
    assert cutoff(NOW, NOW - timedelta(seconds=60)) == DUE
    assert cutoff(NOW, NOW) is None  # an action in flight: the runner passes now


def test_i1_clean_ledger_passes() -> None:
    led = clean()
    assert check_i1(led, keys(led), [], DUE).ok


def test_i1_fails_on_a_lost_frame() -> None:
    led = clean()
    got = keys(led)
    got.pop(40)
    r = check_i1(led, got, [], DUE)
    assert not r.ok and "1 sent frames not stored" in r.detail


def test_i1_ignores_frames_that_are_not_due_yet() -> None:
    led = [*clean(), LedgerLine(V, 100, NOW - timedelta(seconds=5), "sent")]
    assert check_i1(led, keys(led)[:-1], [], DUE).ok


def test_i1_fails_on_simulator_overflow() -> None:
    led = clean()
    led[3] = LedgerLine(V, 3, led[3].t, "overflow")
    assert not check_i1(led, keys(led), [], DUE).ok


def dropout_ledger() -> list[LedgerLine]:  # seq 10..19 dropped
    return [LedgerLine(V, i, t(i * 0.1), "dropout" if 10 <= i < 20 else "sent") for i in range(50)]


def test_i1_dropout_run_must_be_one_gap_of_its_length() -> None:
    led = dropout_ledger()
    assert check_i1(led, keys(led), [Gap(V, 10, 10)], DUE).ok
    assert not check_i1(led, keys(led), [], DUE).ok  # no GAP at all
    assert not check_i1(led, keys(led), [Gap(V, 10, 9)], DUE).ok  # wrong count


def test_i1_dropout_cut_by_a_restart_is_not_expected_to_gap() -> None:
    led = dropout_ledger()
    led[20] = LedgerLine(V, 0, led[20].t, "sent")  # the simulator restarted: next seq is 0
    assert check_i1(led, keys(led), [], DUE).ok


def test_i2_clean_passes_extra_and_duplicate_rows_fail() -> None:
    led = clean()
    assert check_i2(led, keys(led), DUE).ok
    ghost = (V, t(0.05), 999)
    r = check_i2(led, [*keys(led), ghost], DUE)
    assert not r.ok and "1 rows not sent" in r.detail


def test_i2_one_row_per_vehicle_epoch_seq() -> None:
    led = clean(10)
    led.append(LedgerLine(V, 7, t(0.75), "sent"))  # seq 7 again, no reset in between
    assert not check_i2(led, keys(led), DUE).ok  # two rows for (vehicle, epoch, seq 7)
    reset = [*clean(10), *(LedgerLine(V, i, t(2 + i * 0.1), "sent") for i in range(10))]
    assert check_i2(reset, keys(reset), DUE).ok  # the same seqs after a reset: a new epoch


def row(alarm: str, kind: str, param: str = "cabin_temp_c") -> AlarmRow:
    return AlarmRow(alarm, V, param, kind)


def test_i3_valid_path_passes() -> None:
    rows = [row("a", "RAISE"), row("a", "ESCALATE"), row("a", "ACK"), row("a", "RTN")]
    assert check_i3_paths([*rows[:3], row("a", "CLEAR")]).ok
    assert not check_i3_paths(rows).ok  # RTN from ACKED is not a path


def test_i3_fails_on_a_second_raise_before_clear() -> None:
    assert not check_i3_paths([row("a", "RAISE"), row("a", "RAISE")]).ok


def test_i3_fails_on_two_open_alarms_for_one_param() -> None:
    r = check_i3_paths([row("a", "RAISE"), row("b", "RAISE")])
    assert not r.ok and "two open alarms" in r.detail
    assert check_i3_paths([row("a", "RAISE"), row("b", "RAISE", "bus_voltage_v")]).ok


def test_i3_an_alarm_open_before_the_soak_may_ack_rtn_or_clear() -> None:
    assert check_i3_paths([row("a", "CLEAR")], before={"a"}).ok
    assert check_i3_paths([row("a", "RTN"), row("a", "ACK")], before={"a"}).ok
    assert not check_i3_paths([row("b", "CLEAR")], before={"a"}).ok  # b is not one of them


def test_i3_fails_on_a_clear_without_a_raise() -> None:
    assert not check_i3_paths([row("a", "CLEAR")]).ok


def test_i3_reraise_after_rtn_is_the_same_alarm() -> None:
    rows = [row("a", "RAISE"), row("a", "RTN"), row("a", "RAISE"), row("a", "ACK")]
    assert check_i3_paths(rows).ok


def test_i6_poison_and_surplus_fail_injected_count_passes() -> None:
    led = [*clean(), LedgerLine(V, 5, t(1), "malformed")]
    dlq = [(t(1), "schema")]
    assert check_i6(dlq, led, DUE).ok
    assert not check_i6([*dlq, (t(2), "poison")], led, DUE).ok
    assert not check_i6([*dlq, (t(2), "schema")], led, DUE).ok  # a bad frame nobody injected
    assert not check_i6([], led, DUE).ok  # an injected one that vanished


def test_i6_an_entry_just_past_the_cutoff_is_not_a_mismatch() -> None:
    led = [LedgerLine(V, 5, DUE - timedelta(milliseconds=3), "malformed")]
    assert check_i6([(DUE + timedelta(milliseconds=2), "json_invalid")], led, DUE).ok


def cmd(queued: float, state: str, why: str | None = None) -> Command:
    return Command(t(queued), state, why)


def test_i7_stuck_command_fails_terminal_ones_pass() -> None:
    old = (NOW - T0).total_seconds() - 25
    assert check_i7([cmd(old, "VERIFIED"), cmd(old, "FAILED", "NOT_RECEIVED")], NOW, None).ok
    assert not check_i7([cmd(old, "SENT")], NOW, None).ok
    assert not check_i7([cmd(old, "FAILED")], NOW, None).ok  # FAILED without an outcome
    assert check_i7([cmd(old + 10, "SENT")], NOW, None).ok  # 15 s old: not due


def test_i7_grace_after_hang() -> None:
    """A 35 s worker hang starts at 100 s and a command is queued inside it: it is still SENT
    when the hang ends (135 s), and is judged only 60 s after that (EX-04)."""
    end = t(135)
    queued = cmd(110, "SENT")
    assert check_i7([queued], t(110 + 21), end).ok  # past QUEUED + 20 s, inside the grace
    assert check_i7([queued], t(135 + 59), end).ok
    assert not check_i7([queued], t(135 + 61), end).ok  # the grace is over, still stuck
    assert check_i7([cmd(110, "FAILED", "NOT_RECEIVED")], t(135 + 61), end).ok


def ev(kind: TransitionKind, at: float, param: str = "cabin_temp_c", alarm: str = "a") -> QEvent:
    return QEvent(V, param, kind, t(at), uuid.uuid5(uuid.NAMESPACE_OID, alarm))


def test_i8_false_raise_detected() -> None:
    leak = fault_window(V, "overheat", t(100), t(140))
    assert leak is not None
    inside = [ev("RAISE", 110), ev("RTN", 150), ev("CLEAR", 160)]
    assert check_i8(inside, [leak])[0].ok  # raised by the injected overheat
    outside = [ev("RAISE", 300, alarm="b"), ev("RTN", 400, alarm="b")]
    r, q = check_i8([*inside, *outside], [leak])
    assert not r.ok and q.false_raises == 1
    other_param = [ev("RAISE", 110, param="bus_voltage_v", alarm="c")]
    assert not check_i8(other_param, [leak])[0].ok  # a fault only explains its own parameter


def test_i8_short_alarm_outside_a_fault_fails_inside_one_it_does_not() -> None:
    leak = fault_window(V, "overheat", t(100), t(140))
    assert leak is not None
    short_in_fault = [ev("RAISE", 110), ev("RTN", 115)]  # the operator isolated the leak
    assert check_i8(short_in_fault, [leak])[0].ok
    short_outside = [ev("RAISE", 300, alarm="b"), ev("RTN", 305, alarm="b")]
    assert not check_i8(short_outside, [leak])[0].ok


def test_i8_reports_peak_and_flood_for_the_summary() -> None:
    win = fault_window(V, "overheat", t(0), t(1000))
    assert win is not None
    events = [ev("RAISE", 10 * i, alarm=f"x{i}") for i in range(1, 12)]
    _, q = check_i8(events, [win])
    assert q.peak_10min["all"] == 11 and q.time_in_flood_s > 0


def test_fault_window_only_for_faults_that_move_a_parameter() -> None:
    assert fault_window(V, "dropout", t(0), t(5)) is None
    w = fault_window(V, "leak", t(10), t(50))
    assert (
        w is not None and w.params == {"cabin_pressure_kpa"} and w.start < t(10) and w.end > t(50)
    )


def test_i3_recompute_equal_keys_pass() -> None:
    keys = {V: ["a", "b", "c"], "NYX-TA2": []}
    assert check_i3_recompute(keys, {V: ["a", "b", "c"], "NYX-TA2": []}).ok


def test_i3_recompute_fails_on_missing_extra_and_reordered_keys() -> None:
    rec = {V: ["a", "b", "c"]}
    missing = check_i3_recompute(rec, {V: ["a", "c"]})
    extra = check_i3_recompute(rec, {V: ["a", "b", "c", "d"]})
    swapped = check_i3_recompute(rec, {V: ["a", "c", "b"]})
    assert not missing.ok and "b" in missing.detail and "3 recorded, 2 recomputed" in missing.detail
    assert not extra.ok and "d" in extra.detail
    assert not swapped.ok and "index 1" in swapped.detail


def test_i1_expected_gap_does_not_depend_on_the_order_of_the_lines() -> None:
    # `sent` lines are written when the XADD succeeded, after the tick's dropout lines
    lines = [LedgerLine(V, 1, T0 + timedelta(seconds=1), "dropout"),
             LedgerLine(V, 2, T0 + timedelta(seconds=2), "dropout"),
             LedgerLine(V, 0, T0, "sent"),
             LedgerLine(V, 3, T0 + timedelta(seconds=3), "sent")]  # fmt: skip
    stored = [(V, T0, 0), (V, T0 + timedelta(seconds=3), 3)]
    assert check_i1(lines, stored, [Gap(V, 1, 2)], DUE).ok
    assert not check_i1(lines, stored, [], DUE).ok  # the dropout run is still seen
