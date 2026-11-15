"""UI-07: a restarted api loads every module it imports from bytecode built into the image.

The container runs as `gs`, which cannot write `__pycache__`, so a module without a .pyc in the
image is compiled from source on every start, inside the < 5 s restart budget. The python base
image ships its standard library without .pyc and an editable install leaves gs without them:
together about 0.17 s of a 0.42 s import locally, several times that on a CI runner.
"""

from tests.support.compose import compose

# every module loaded from a .py file (stdlib, site-packages, gs) has its cached bytecode on disk
CHECK = (
    "import os, sys, gs.api.server\n"
    "missing = sorted(n for n, m in list(sys.modules.items())\n"
    "                 if getattr(m, '__cached__', None) and not os.path.exists(m.__cached__))\n"
    "print(' '.join(missing))\n"
)


def test_api_imports_its_code_from_bytecode() -> None:
    r = compose("exec", "-T", "api", "python", "-c", CHECK)
    assert r.stdout.strip() == "", f"compiled at every start: {r.stdout.strip()}"
