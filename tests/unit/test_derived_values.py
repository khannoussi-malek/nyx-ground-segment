"""Derived expr parameters in the frame path: compute() (D-EXT-05, R23 M4)."""

import random
from datetime import UTC, datetime
from pathlib import Path

from gs.common.interfaces import FrameHeader, build_frame_model
from gs.common.missiondb import MissionDb, load_dir
from gs.ingest.compute import compute
from gs.ingest.validate import FieldStats
from tests.support.mission import calibrated, files, ld

T0 = datetime(2026, 11, 14, 10, tzinfo=UTC)
SHIPPED = load_dir(Path("mission"))
PW = "  power_w: {unit: W, expr: bus_voltage_v * bus_current_a}\n"


def with_derived(extra: str, cal: bool = False) -> MissionDb:
    base = calibrated() if cal else ld(files())  # raw-only bases
    p = files(parameters=base.parameters_yaml.encode())["parameters"].decode()
    return ld(files(parameters=(p + "derived:\n" + extra).encode()))


def frame(mdb: MissionDb, **over: float | None) -> FrameHeader:
    vals = {p: 1.0 for p in mdb.raw_names} | over
    return build_frame_model(mdb.raw_names)(
        vehicle="NYX-TA1", seq=0, t_onboard=T0, mode="TEST", last_cmd_id=None, **vals
    )


def test_power_w_bit_equal_product() -> None:
    rng = random.Random(6)
    for _ in range(1000):
        v, i = rng.uniform(20, 35), rng.uniform(0, 12)
        c = compute(SHIPPED, frame(SHIPPED, bus_voltage_v=v, bus_current_a=i))
        assert c.values["power_w"] == v * i  # == not approx
        assert c.nulls == ()


def test_null_input_gives_null_not_last() -> None:  # M4
    m = with_derived(PW, cal=True)  # bus_current_a: raw 0..10 -> eng 0..100
    assert compute(m, frame(m, bus_voltage_v=2.0, bus_current_a=5.0)).values["power_w"] == 100.0
    c = compute(m, frame(m, bus_voltage_v=2.0, bus_current_a=11.0))
    assert c.values["power_w"] is None
    assert ("power_w", "missing") in c.nulls
    c = compute(m, frame(m, bus_voltage_v=2.0, bus_current_a=None))
    assert c.values["power_w"] is None


def test_chained_derived() -> None:
    m = with_derived(
        "  a2:\n    unit: W\n    expr: bus_voltage_v * 2\n  a3:\n    unit: W\n    expr: a2 + 1\n"
    )
    assert compute(m, frame(m, bus_voltage_v=3.0)).values["a3"] == 7.0


def test_chained_derived_null_propagates() -> None:
    m = with_derived(
        "  a2:\n    unit: W\n    expr: 1 / (bus_voltage_v - 1)\n"
        "  a3:\n    unit: W\n    expr: a2 + 1\n"
    )
    c = compute(m, frame(m, bus_voltage_v=1.0))
    assert c.values["a2"] is None and c.values["a3"] is None
    assert c.nulls == (("a2", "div0"), ("a3", "missing"))


def test_bad_expr_values_null_with_reason() -> None:
    m = with_derived(
        "  d0:\n    unit: x\n    expr: 1 / (bus_voltage_v - 1)\n"
        "  ov:\n    unit: x\n    expr: bus_voltage_v * 1e308 * 10\n"
        "  sq:\n    unit: x\n    expr: sqrt(bus_voltage_v - 5)\n"
    )
    c = compute(m, frame(m, bus_voltage_v=1.0))
    assert dict(c.nulls) == {"d0": "div0", "ov": "nonfinite", "sq": "nonfinite"}
    assert all(c.values[n] is None for n in ("d0", "ov", "sq"))


def test_error_reason_is_reported() -> None:
    def boom(_: object) -> float:
        raise RuntimeError("x")

    m = load_dir(Path("mission"))  # own copy: the patch must not leak
    object.__setattr__(m.derived["power_w"].expr, "_run", boom)  # frozen dataclass
    c = compute(m, frame(m))
    assert c.values["power_w"] is None and c.nulls == (("power_w", "error"),)


def test_derived_in_frame_is_ignored_and_counted() -> None:
    f = build_frame_model(SHIPPED.raw_names)(
        vehicle="NYX-TA1", seq=0, t_onboard=T0, mode="TEST", last_cmd_id=None,
        power_w=999.0, **{p: 2.0 for p in SHIPPED.raw_names},
    )  # fmt: skip
    assert compute(SHIPPED, f).values["power_w"] == 4.0
    st = FieldStats()
    st.observe(f, SHIPPED.raw_names)
    assert st.unknown["power_w"] == 1
