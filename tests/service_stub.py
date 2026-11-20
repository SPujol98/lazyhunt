"""A duck-typed ``JobService`` for bridge tests that need no real service.

``bridge.register`` reads the threshold and, on every request, the service's
live ``loaded_preferences`` (onboarding-v2 T2). The stub loads them once from
the profile, exactly as the real service does at start-up.
"""

from pathlib import Path
from types import SimpleNamespace

from cva.profile_paths import resolve_profile_paths
from cva.profile_prefs import load_preferences


def service_stub(threshold: int, profile: Path | None = None) -> SimpleNamespace:
    """A service with ``threshold`` and the preferences of ``profile``."""
    return SimpleNamespace(
        threshold=threshold,
        loaded_preferences=load_preferences(resolve_profile_paths(profile).preferences),
    )
