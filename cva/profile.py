"""Offer profile (backend-profile): 'ai' vs 'backend'.

Beacon offers carry the profile of the beacon that found them (the extension
sends it in the bridge payload). Manual offers (/analyze URL or paste) infer
it from the title: no AI term -> 'backend'. A beacon offer whose title shows an
AI term is 'ai' whatever the beacon profile (the backend filter lets any
"Engineer" through). NULL in the DB means a legacy row
and is treated as 'ai' everywhere.

The rules live in the role-family registry (``cva/families.py``); these
functions answer for the built-in ``ai``/``backend`` families.
"""

from typing import Final

from cva.families import AI_TITLE_TERMS, BUILTIN_AI, BUILTIN_REGISTRY

__all__ = [
    "AI_TITLE_TERMS",
    "DEFAULT_PROFILE",
    "Profile",
    "has_ai_title",
    "infer_profile",
    "normalize_profile",
    "role_for_profile",
]

# An offer profile: a role family id (FAMILY_ID_RE). The functions below answer
# for the built-in families; profile-aware callers use ``ProfilePrefs.families``
# (``FamilyRegistry.normalize``/``infer``/``route``).
Profile = str
DEFAULT_PROFILE: Final[Profile] = "ai"


def _builtin(profile_id: str) -> Profile:
    return "backend" if profile_id == "backend" else "ai"


def normalize_profile(value: object) -> Profile:
    """'backend' only when explicitly so; anything else is 'ai'."""
    return _builtin(BUILTIN_REGISTRY.normalize(value))


def role_for_profile(profile: object) -> str:
    """CV role a card starts on: 'BACKEND' for backend offers, else 'AI'."""
    return BUILTIN_REGISTRY.role_for(profile)


def has_ai_title(title: str | None) -> bool:
    """True when the title contains an AI term (whole word, case/accent-blind)."""
    return BUILTIN_AI.matches_title(title)


def infer_profile(title: str | None) -> Profile:
    """Profile of a manual offer from its title; unknown title -> 'ai'."""
    return _builtin(BUILTIN_REGISTRY.infer(title))
