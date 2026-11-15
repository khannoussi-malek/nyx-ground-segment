"""Fixture for test_except_coverage: two handlers, the one in `g` is never executed."""


def f(x: dict[str, int]) -> int:
    try:
        return x["a"]
    except KeyError:
        return 0


def g(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1
