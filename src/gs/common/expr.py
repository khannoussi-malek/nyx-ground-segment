"""Derived-parameter expressions (D-EXT-05, R23 S2/M4): a whitelist compiler to a closure tree.

Mission files are hand-edited, so the text is a trust boundary: `compile_expr` walks the `ast`
tree itself (never eval/exec/compile of the text) and rejects anything outside the grammar or
over the limits with an `ExprError`. `Expr.eval` never raises: it returns `(value, None)` or
`(None, reason)`, reason in `missing | div0 | nonfinite | error` (NULL, never the last value).
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

MAX_CHARS = 256
MAX_NODES = 64
MAX_DEPTH = 16

Values = Mapping[str, float | None]
Fn = Callable[[Values], float]


class ExprError(ValueError):
    """The expression is outside the grammar or over a limit."""


class _Null(Exception):
    """Internal: evaluation gives NULL with `reason`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _finite(x: float) -> float:
    if not math.isfinite(x):
        raise _Null("nonfinite")
    return x


def _div(a: float, b: float) -> float:
    if b == 0:
        raise _Null("div0")
    return a / b


def _sqrt(x: float) -> float:
    if x < 0:
        raise _Null("nonfinite")
    return math.sqrt(x)


_BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: _div,
}
# name -> (function, min args, max args)
_FUNCS: dict[str, tuple[Callable[..., float], int, int | None]] = {
    "abs": (abs, 1, 1),
    "sqrt": (_sqrt, 1, 1),
    "min": (min, 2, None),
    "max": (max, 2, None),
}


def _const(node: ast.Constant) -> Fn:
    v = node.value
    if not (type(v) is int or type(v) is float):  # noqa: E721  excludes bool, str, complex, None
        raise ExprError(f"literal of type {type(v).__name__} is not allowed")
    try:
        x = float(v)
    except OverflowError:
        raise ExprError("numeric literal too large") from None
    if not math.isfinite(x):
        raise ExprError("numeric literal too large")
    return lambda _: x


def _name(node: ast.Name) -> Fn:
    ident = node.id

    def get(values: Values) -> float:
        v = values.get(ident)
        if v is None:
            raise _Null("missing")
        return _finite(float(v))

    return get


def _unary(node: ast.UnaryOp, depth: int) -> Fn:
    if not isinstance(node.op, ast.USub):
        raise ExprError(f"operator {type(node.op).__name__} is not allowed")
    operand = _node(node.operand, depth + 1)
    return lambda values: -operand(values)


def _binop(node: ast.BinOp, depth: int) -> Fn:
    op = _BINOPS.get(type(node.op))
    if op is None:
        raise ExprError(f"operator {type(node.op).__name__} is not allowed")
    left, right = _node(node.left, depth + 1), _node(node.right, depth + 1)
    return lambda values: _finite(op(left(values), right(values)))


def _call(node: ast.Call, depth: int) -> Fn:
    f = node.func
    if not isinstance(f, ast.Name) or f.id not in _FUNCS or node.keywords:
        raise ExprError("only abs, min, max, sqrt with positional arguments may be called")
    fn, lo, hi = _FUNCS[f.id]
    n = len(node.args)
    if n < lo or (hi is not None and n > hi) or any(isinstance(a, ast.Starred) for a in node.args):
        raise ExprError(f"wrong arguments for {f.id}")
    args = [_node(a, depth + 1) for a in node.args]
    return lambda values: _finite(fn(*(a(values) for a in args)))


def _node(node: ast.AST, depth: int) -> Fn:
    if depth > MAX_DEPTH:
        raise ExprError(f"expression is nested deeper than {MAX_DEPTH}")
    if isinstance(node, ast.Constant):
        return _const(node)
    if isinstance(node, ast.Name):
        return _name(node)
    if isinstance(node, ast.UnaryOp):
        return _unary(node, depth)
    if isinstance(node, ast.BinOp):
        return _binop(node, depth)
    if isinstance(node, ast.Call):
        return _call(node, depth)
    raise ExprError(f"{type(node).__name__} is not allowed")


@dataclass(frozen=True)
class Expr:
    names: tuple[str, ...]
    _run: Fn = field(repr=False, compare=False)

    def eval(self, values: Values) -> tuple[float | None, str | None]:
        try:
            return self._run(values), None
        except _Null as e:
            return None, e.reason
        except Exception:  # noqa: BLE001  ponytail: catch-all by design (A-EXT-4)
            return None, "error"


def compile_expr(text: str) -> Expr:
    if len(text) > MAX_CHARS:
        raise ExprError(f"expression is longer than {MAX_CHARS} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        raise ExprError(f"cannot parse expression: {type(e).__name__}") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExprError(f"expression has more than {MAX_NODES} nodes")
    run = _node(tree.body, 2)  # the Expression root is depth 1
    return Expr(_input_names(tree), run)


def _input_names(tree: ast.AST) -> tuple[str, ...]:
    """Ordered, de-duplicated `Name` ids; callable names are not inputs."""
    seen: dict[str, None] = {}

    def visit(n: ast.AST) -> None:
        if isinstance(n, ast.Call):
            for a in n.args:
                visit(a)
        elif isinstance(n, ast.Name):
            seen.setdefault(n.id)
        else:
            for c in ast.iter_child_nodes(n):
                visit(c)

    visit(tree)
    return tuple(seen)
