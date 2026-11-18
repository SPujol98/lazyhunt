"""Deterministic search suggestions from the owner's profile (no LLM).

``suggest_searches`` turns the target roles and the most-used skills of the
claims DB into 6-8 ready-to-edit auto searches, one group per role family
(the built-in ``ai`` and ``backend``, or the profile's declared families). The bridge serves them (``GET /searches/suggested``) to
the extension options page, where the owner picks the ones to add. Pure and
stable: the same inputs always give the same list in the same order.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from cva.claimsdb import ClaimsDB, load_claims
from cva.families import FamilyRegistry
from cva.ingest.sources import SOURCES
from cva.profile_paths import resolve_profile_paths

MAX_SUGGESTIONS: Final[int] = 8
_SKILLS_PER_SEARCH: Final[int] = 2

# Primary claim tag (first entry of a skill claim's ``tags``) -> profile.
# Skills outside both sets (C, Bash, Pygame, ...) are never searched for;
# families other than ai/backend get role searches only.
_AI_SKILL_TAGS: Final[frozenset[str]] = frozenset(
    {"llm", "rag", "nlp", "pytorch", "transformers", "sentence-transformers", "ir"}
)
_BACKEND_SKILL_TAGS: Final[frozenset[str]] = frozenset(
    {"python", "django", "fastapi", "sql", "rest", "docker", "pydantic"}
)
_SKILL_TAGS: Final[dict[str, frozenset[str]]] = {
    "ai": _AI_SKILL_TAGS,
    "backend": _BACKEND_SKILL_TAGS,
}


@dataclass(frozen=True)
class ProfileTargets:
    """What the owner is looking for: roles per profile and where."""

    ai_roles: tuple[str, ...]
    backend_roles: tuple[str, ...]
    locations: tuple[str, ...] = ("Barcelona", "remoto")
    # The profile's declared role families (``[families]`` in
    # preferences.toml); ``None`` = the built-in ai/backend from the roles
    # above. Set by cva/profile_prefs.py so every caller of the targets
    # (the bridge included) follows the profile's families.
    families: FamilyRegistry | None = None


DEFAULT_PROFILE_TARGETS: Final[ProfileTargets] = ProfileTargets(
    ai_roles=("AI Engineer", "LLM Engineer"),
    backend_roles=("Backend Developer", "Python Developer"),
)


@dataclass(frozen=True)
class Search:
    """One suggested auto search (the shape the extension's searches use)."""

    name: str
    keywords: str
    location: str
    filter_profile: str  # a role family id
    portals: tuple[str, ...]
    title_filter: Literal["on", "off"]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "keywords": self.keywords,
            "location": self.location,
            "filterProfile": self.filter_profile,
            "portals": list(self.portals),
            "titleFilter": self.title_filter,
        }


def default_portals(enabled: Collection[str] | None = None) -> tuple[str, ...]:
    """linkedin + infojobs first, then other registered portals that opt in.

    ``enabled`` (``Settings.enabled_portals``) narrows the list to enabled
    portals; ``None`` is the legacy "every registered portal" behaviour.
    """
    base = ("linkedin", "infojobs")
    portals = base + tuple(
        p for p, spec in SOURCES.items() if p not in base and spec.suggest_by_default
    )
    if enabled is None:
        return portals
    return tuple(p for p in portals if p in enabled)


def _label(text: str) -> str:
    """Search term of a skill claim: ``RAG (Retrieval ...)`` -> ``RAG``."""
    return text.split(" (")[0].split(" / ")[0].strip()


def _ranked_skills(claims: ClaimsDB, allowed: frozenset[str]) -> list[str]:
    """Skill labels of one profile, most used by the other claims first."""
    uses: dict[str, int] = {}
    for claim in claims.claims:
        if claim.category == "skill":
            continue
        for tag in set(claim.tags):
            uses[tag] = uses.get(tag, 0) + 1
    seen: set[str] = set()
    ranked: list[tuple[int, int, str]] = []
    for order, claim in enumerate(claims.claims):
        if claim.category != "skill" or not claim.tags:
            continue
        tag = claim.tags[0]
        if tag not in allowed or tag in seen:
            continue
        seen.add(tag)
        ranked.append((-uses.get(tag, 0), order, _label(claim.text)))
    ranked.sort()
    return [label for _, _, label in ranked]


def _profile_searches(
    profile: str,
    roles: Sequence[str],
    skills: Sequence[str],
    locations: Sequence[str],
    portals: tuple[str, ...],
) -> list[Search]:
    """Role searches (title filter on) then one skill-combo search (off)."""
    out: list[Search] = []

    def add(keywords: str, location: str, title_filter: Literal["on", "off"]) -> None:
        out.append(
            Search(
                name=f"{keywords} · {location}",
                keywords=keywords,
                location=location,
                filter_profile=profile,
                portals=portals,
                title_filter=title_filter,
            )
        )

    if roles and locations:
        for location in locations:
            add(roles[0], location, "on")
        for role in roles[1:]:
            add(role, locations[0], "on")
    if skills and locations:
        add(" ".join(skills[:_SKILLS_PER_SEARCH]), locations[0], "off")
    return out


def suggest_searches(
    claims: ClaimsDB,
    profile_targets: ProfileTargets,
    *,
    portals: Sequence[str] | None = None,
    families: FamilyRegistry | None = None,
) -> list[Search]:
    """Up to MAX_SUGGESTIONS deterministic searches, one group per family.

    ``families`` (else ``profile_targets.families``) are the profile's role
    families, in declared order, each with its ``search_roles``; with neither,
    the built-in ai and backend groups from ``profile_targets``' roles.
    """
    chosen = tuple(portals) if portals is not None else default_portals()
    registry = families if families is not None else profile_targets.families
    groups: list[tuple[str, Sequence[str]]] = (
        [("ai", profile_targets.ai_roles), ("backend", profile_targets.backend_roles)]
        if registry is None
        else [(f.id, f.search_roles) for f in registry.families]
    )
    suggestions: list[Search] = []
    for profile, roles in groups:
        tags = _SKILL_TAGS.get(profile)
        skills = _ranked_skills(claims, tags) if tags is not None else []
        suggestions += _profile_searches(
            profile, roles, skills, profile_targets.locations, chosen
        )
    seen: set[tuple[str, str]] = set()
    unique: list[Search] = []
    for search in suggestions:
        key = (search.keywords.casefold(), search.location.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(search)
    return unique[:MAX_SUGGESTIONS]


def suggested_searches_payload(
    claims_path: Path | None = None,
    targets: ProfileTargets | None = None,
    enabled_portals: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """The profile's claims DB + targets as JSON-ready search dicts.

    ``claims_path`` defaults to the unset-profile location (today's file) and
    ``targets`` to ``DEFAULT_PROFILE_TARGETS`` (the profile's ``[search_targets]``
    arrive here from the caller, see ``cva/profile_prefs.py``). ``enabled_portals``
    (None = every portal) restricts the portals the searches name; with none
    of the default portals enabled there is nothing to suggest.
    """
    portals = default_portals(enabled_portals)
    if not portals:
        return []
    claims = load_claims(
        claims_path if claims_path is not None else resolve_profile_paths().claims
    )
    return [
        s.to_json()
        for s in suggest_searches(
            claims,
            targets if targets is not None else DEFAULT_PROFILE_TARGETS,
            portals=portals,
        )
    ]


__all__ = [
    "DEFAULT_PROFILE_TARGETS",
    "ProfileTargets",
    "Search",
    "default_portals",
    "suggest_searches",
    "suggested_searches_payload",
]
