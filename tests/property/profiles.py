"""Hypothesis profiles (D-EXT-14): `nyx-ci` is derandomised, `nightly` searches wider (seed
printed)."""

import os
import random

from hypothesis import core as _core
from hypothesis import settings

CI_EXAMPLES = 200
NIGHTLY_EXAMPLES = 10_000
CI_DEADLINE_MS = 500

# not named "ci": Hypothesis loads a profile of that name globally whenever CI=true, which
# gave every Hypothesis test in the run (the REST fuzz too) this 500 ms deadline
settings.register_profile(
    "nyx-ci", max_examples=CI_EXAMPLES, derandomize=True, deadline=CI_DEADLINE_MS, print_blob=True
)
settings.register_profile(
    "nightly", max_examples=NIGHTLY_EXAMPLES, derandomize=False, deadline=None, print_blob=True
)

NAME = os.environ.get("HYPOTHESIS_PROFILE", "nyx-ci")
SEED: int | None = None
if NAME == "nightly":
    # the plugin's --hypothesis-seed sets the same global; an env seed replays a night
    SEED = int(os.environ.get("HYPOTHESIS_SEED") or random.SystemRandom().randrange(2**32))
    _core.global_force_seed = SEED  # type: ignore[assignment]
# ponytail: applied per test (@profiles.PROFILE), never loaded globally: a global load would give
# every Hypothesis test in the run (the REST fuzz too) this profile's 500 ms deadline
PROFILE = settings.get_profile(NAME)
