"""EXT-01 Task 3: derived-parameter expression compiler (D-EXT-05, R23 S2/M4)."""

from __future__ import annotations

import math

import pytest

from gs.common.expr import Expr, ExprError, compile_expr


def ev(text: str, **values: float | None) -> tuple[float | None, str | None]:
    return compile_expr(text).eval(values)


@pytest.mark.parametrize(
    "text",
    [
        "a ** 2",
        "a.b",
        "a < b",
        "__import__('os')",
        "[a]",
        "lambda: 1",
        "a if b else c",
        "foo(a)",
        "abs(a, key=1)",
        "min(a)",
        "abs(a, b)",
        "sqrt()",
        "a[0]",
        "'x'",
        "1j",
        "None",
        "a % b",
        "a // b",
        "(a for a in b)",
        "abs.__call__(a)",
        "abs(*a)",
        "",
        "a +",
    ],
)
def test_grammar_whitelist(text: str) -> None:
    with pytest.raises(ExprError):
        compile_expr(text)


def test_load_accepted() -> None:
    assert compile_expr("-(a + 2.5) * b / 3").names == ("a", "b")
    assert compile_expr("sqrt(a*a + b*b)").names == ("a", "b")
    assert compile_expr("max(a, abs(b))").names == ("a", "b")
    assert compile_expr("a + a * c + a").names == ("a", "c")
    assert issubclass(ExprError, ValueError)
    assert isinstance(compile_expr("1"), Expr)


def test_values() -> None:
    assert ev("sqrt(a*a + b*b)", a=3.0, b=4.0) == (5.0, None)
    assert ev("max(a, abs(b), 1)", a=1.0, b=-7.0) == (7.0, None)
    assert ev("min(a, b)", a=1.0, b=-7.0) == (-7.0, None)


def test_precedence() -> None:
    assert ev("1 + 2 * 3") == (7.0, None)
    assert ev("(1 + 2) * 3") == (9.0, None)
    assert ev("-a * 2", a=3.0) == (-6.0, None)
    assert ev("8 / 4 / 2") == (1.0, None)
    assert ev("10 - 4 - 3") == (3.0, None)


def test_div0_gives_div0() -> None:
    assert ev("a / b", a=1.0, b=0.0) == (None, "div0")
    assert ev("a / 0", a=1.0) == (None, "div0")
    assert ev("1 / (a - a)", a=2.0) == (None, "div0")


@pytest.mark.parametrize("text", ["True", "False", "1e999", "1" + "0" * 400, "-1e999"])
def test_bool_and_huge_literal_rejected(text: str) -> None:
    with pytest.raises(ExprError):
        compile_expr(text)


def test_length_nodes_depth_limits() -> None:
    compile_expr("a" + " " * 255)  # 256 chars
    with pytest.raises(ExprError, match="256"):
        compile_expr("a" + " " * 256)
    compile_expr("max(" + ",".join(["1"] * 60) + ")")  # 64 nodes
    with pytest.raises(ExprError, match="64"):
        compile_expr("max(" + ",".join(["1"] * 61) + ")")
    compile_expr("-" * 14 + "a")  # depth 16
    with pytest.raises(ExprError, match="16"):
        compile_expr("-" * 15 + "a")
    with pytest.raises(ExprError):  # nested calls: same limit, never a RecursionError
        compile_expr("abs(" * 60 + "a" + ")" * 60)
    compile_expr("(" * 100 + "a" + ")" * 100)  # parentheses make no nodes


def test_overflow_gives_nonfinite() -> None:
    assert ev("a * b", a=1e308, b=1e308) == (None, "nonfinite")
    assert ev("a * b / b", a=1e308, b=1e308) == (None, "nonfinite")
    assert ev("a / b", a=1e308, b=1e-300) == (None, "nonfinite")


def test_sqrt_negative_nonfinite() -> None:
    assert ev("sqrt(a)", a=-1.0) == (None, "nonfinite")
    assert ev("sqrt(a)", a=0.0) == (0.0, None)


def test_missing_input() -> None:
    assert ev("a + b", a=1.0, b=None) == (None, "missing")
    assert ev("a + b", a=1.0) == (None, "missing")


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_input(bad: float) -> None:
    assert ev("a + 1", a=bad) == (None, "nonfinite")


def test_any_exception_gives_error() -> None:
    e = compile_expr("a + 1")

    def boom(_: object) -> float:
        raise RuntimeError("x")

    object.__setattr__(e, "_run", boom)
    assert e.eval({"a": 1.0}) == (None, "error")
