"""P6 (design 11.5, D-EXT-14): a derived `expr` matches an independent reference evaluator, and
every string outside the grammar is rejected with ExprError and nothing else."""

import math
import operator
from collections.abc import Callable, Mapping

import pytest
from hypothesis import event, given
from hypothesis import strategies as st

from gs.common.expr import ExprError, compile_expr
from tests.property import profiles
from tests.property.strategies import EXPR_INPUTS, Tree, exprs, render


def _ref(t: Tree, v: Mapping[str, float | None]) -> float | None:
    """The reference: Python float arithmetic over the generated tree, the NULL rules applied here
    (a missing or non-finite input, division by zero, sqrt of a negative, a non-finite result).
    It never calls gs.common.expr and never parses text."""
    kind = t[0]
    if kind == "num":
        return float(t[1])
    if kind == "name":
        x = v.get(t[1])
        return x if x is not None and math.isfinite(x) else None
    args = [_ref(a, v) for a in ([t[1]] if kind == "neg" else t[2:] if kind == "bin" else t[2])]
    if any(a is None for a in args):
        return None
    xs = [a for a in args if a is not None]
    out: float | None
    if kind == "neg":
        out = -xs[0]
    elif kind == "bin":
        ops: dict[str, Callable[[float, float], float]] = {
            "+": operator.add,
            "-": operator.sub,
            "*": operator.mul,
            "/": operator.truediv,
        }
        out = None if t[1] == "/" and xs[1] == 0 else ops[t[1]](*xs)
    elif t[1] == "sqrt":
        out = None if xs[0] < 0 else math.sqrt(xs[0])
    else:
        fns: dict[str, Callable[..., float]] = {"abs": abs, "min": min, "max": max}
        out = fns[t[1]](*xs)
    return out if out is not None and math.isfinite(out) else None


@profiles.PROFILE
@given(exprs(), EXPR_INPUTS)
def test_p6_expr_matches_reference(tree: Tree, values: dict[str, float | None]) -> None:
    got, why = compile_expr(render(tree)).eval(values)
    want = _ref(tree, values)
    assert (got is None) == (want is None) == (why is not None), (render(tree), got, want, why)
    if want is not None:
        assert got is not None
        assert got.hex() == want.hex(), (render(tree), got, want)  # bit for bit
    event(f"null={want is None}")
    event("nodes=" + tree[0])
    event(f"missing_input={any(values.get(n) is None for n in values) or len(values) < 4}")


_FORBIDDEN = [
    "a ** b", "a // b", "a % b", "a @ b", "a << b", "a & b", "a if b else c", "[a]", "(a, b)",
    "{a: b}", "{a}", "a.b", "a[0]", "f(a)", "abs(a, b)", "sqrt()", "sqrt(a, b)", "min(a)",
    "max()", "abs(a=1)", "abs(*a)", "a < b", "a and b", "not a", "~a", "+a", "lambda: a", "'s'",
    "True", "None", "1j", "1e999", "__import__('os')", "(x := 1)", "[a for a in b]", "a.abs()",
    "abs.__call__(a)", "a; b", "a b", "", "(", "a +", "1 2",
]  # fmt: skip
_JUNK = st.text(
    alphabet=st.characters(blacklist_categories=("L", "N", "Cs")), min_size=1, max_size=40
).filter(lambda s: s != "_")  # no letter or digit: never an in-grammar expression


@st.composite
def _outside(draw: st.DrawFn) -> str:
    return draw(
        st.sampled_from(_FORBIDDEN)
        | _JUNK
        | st.tuples(exprs(), st.sampled_from(_FORBIDDEN[:-6])).map(
            lambda p: f"({render(p[0])}) + ({p[1]})"  # a forbidden node inside a valid one
        )
        | st.integers(17, 100).map(lambda n: "-" * n + "a")  # nested past the depth limit
        | st.integers(40, 100).map(lambda n: "a" + " + a" * n)  # past the depth limit (a chain)
        | st.integers(40, 100).map(lambda n: "min(" + ",".join("a" * n) + ")")  # flat: only the
        # 64-node limit is crossed (height 2); a chain of sums trips the depth limit first
        | st.integers(257, 400).map(lambda n: "a" * n)  # past the length limit
    )


@profiles.PROFILE
@given(_outside())
def test_p6_rejects_everything_outside_grammar(text: str) -> None:
    with pytest.raises(ExprError):
        compile_expr(text)
    event("kind=" + ("forbidden" if text in _FORBIDDEN else "other"))
