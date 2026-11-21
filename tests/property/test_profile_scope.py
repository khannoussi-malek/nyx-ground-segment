"""The ci/nightly profile applies to the property tests only (D-EXT-14), never to the rest of the
suite: a global load gave the REST fuzz test a 500 ms deadline that a password hash exceeds."""

import importlib
import inspect

from hypothesis import settings

from tests.property import profiles

MODULES = [
    "test_seq_tracker_props",
    "test_engine_props",
    "test_frame_schema_props",
    "test_derived_props",
]


def test_the_global_default_is_untouched() -> None:
    default = settings.default
    assert default is not None
    assert default.max_examples != profiles.CI_EXAMPLES
    assert default.deadline is None or default.deadline.total_seconds() < 0.5


def test_every_property_test_carries_the_profile() -> None:
    tests = [
        fn
        for m in MODULES
        for _, fn in inspect.getmembers(importlib.import_module(f"tests.property.{m}"))
        if hasattr(fn, "hypothesis")
    ]
    assert len(tests) == 11
    for fn in tests:
        s = fn._hypothesis_internal_use_settings
        assert s.max_examples == profiles.PROFILE.max_examples, fn.__name__
        assert s.deadline == profiles.PROFILE.deadline, fn.__name__
