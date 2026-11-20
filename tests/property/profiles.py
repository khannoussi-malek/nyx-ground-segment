"""Hypothesis profiles (D-EXT-14): `ci` is derandomised, `nightly` searches wider (seed printed)."""

import os
import random

from hypothesis import core as _core
from hypothesis import settings

CI_EXAMPLES = 200
NIGHTLY_EXAMPLES = 10_000
CI_DEADLINE_MS = 500

settings.register_profile(
    "ci", max_examples=CI_EXAMPLES, derandomize=True, deadline=CI_DEADLINE_MS, print_blob=True
)
settings.register_profile(
    "nightly", max_examples=NIGHTLY_EXAMPLES, derandomize=False, deadline=None, print_blob=True
)

NAME = os.environ.get("HYPOTHESIS_PROFILE", "ci")
SEED: int | None = None
if NAME == "nightly":
    # the plugin's --hypothesis-seed sets the same global; an env seed replays a night
    SEED = int(os.environ.get("HYPOTHESIS_SEED") or random.SystemRandom().randrange(2**32))
    _core.global_force_seed = SEED  # type: ignore[assignment]
settings.load_profile(NAME)
