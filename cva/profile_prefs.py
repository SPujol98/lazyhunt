"""What the profile owner wants: ``preferences.toml`` (profile-extraction E3).

``preferences.toml`` lives in the profile directory (``cva/profile_paths.py``).
The file is optional (like ``profile.toml``) and so is every key: a missing
file, table or key keeps the engine default FOR THAT KEY, and the defaults are
the values that used to be hard-coded, so an unset ``profile_dir`` behaves
exactly as before. Unknown tables/keys and wrongly typed values raise
:class:`~cva.profile_identity.ProfileError` instead of being ignored::

    [stack_wall]                       # cva/match/stack_wall.py
    core_language = "Python"           # an offer in another language is walled
    wall_languages = ["Java", "Go"]    # default: every other known language

    [gates]                            # cva/match/gates.py
    allowed_languages = ["es", "ca", "en"]   # ISO codes; any other mandatory
                                             # language FAILs the offer
    flag_english_above_c1 = true       # FLAG offers asking for C2/native English

    [location]
    home_region = "catalonia"          # key of cva.match.locations.REGIONS

    [search_targets]                   # cva/suggest.py
    ai_roles = ["AI Engineer", "LLM Engineer"]
    backend_roles = ["Backend Developer", "Python Developer"]
    locations = ["Barcelona", "remoto"]

    [cover_letter]                     # cva/match/cover_letter.py
    career_change = "The career change (...) takes ONE sentence ..."  # rule 7
    [cover_letter.branch_emphasis]     # appended per offer profile (AI/BACKEND)
    AI = "AI branch: emphasise ..."
    BACKEND = "BACKEND branch: emphasise ..."

    [title_filters]                    # cva/title_filter.py + the extension
    company_blocklist = ["Acme Staffing"]   # dropped by both filters
    [title_filters.ai]                 # also [title_filters.backend]
    enabled = true
    include_groups = [["AI", "LLM"], ["Engineer", "Developer"]]
    exclude_terms = ["Senior", "Lead"] # the blocklist is appended

Thresholds (match score, gray zone, ...) are NOT here on purpose: they are
operational knobs that ``Settings`` (``config.toml`` -> ``config.local.toml``
-> ``CVA_*`` env) already layers per machine, and the bot's cards, daily caps
and tests read them from there.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from cva.match.gates import DEFAULT_GATE_PREFS, LANGUAGE_CODES, GatePrefs
from cva.match.locations import UnknownRegionError, get_region
from cva.match.persona import CoverLetterPersona
from cva.match.persona_defaults import OWNER_COVER_LETTER_PERSONA
from cva.match.stack_wall import (
    DEFAULT_STACK_WALL_PREFS,
    LANGUAGE_PATTERNS,
    StackWallPrefs,
)
from cva.profile_identity import ProfileError
from cva.suggest import DEFAULT_PROFILE_TARGETS, ProfileTargets
from cva.title_filter import (
    BACKEND_TITLE_FILTER,
    DEFAULT_COMPANY_BLOCKLIST,
    DEFAULT_TITLE_FILTER,
    TitleFilter,
    with_company_blocklist,
)


@dataclass(frozen=True)
class TitleFilterPrefs:
    """The title/company pre-filter of each offer profile."""

    ai: TitleFilter = DEFAULT_TITLE_FILTER
    backend: TitleFilter = BACKEND_TITLE_FILTER
    company_blocklist: tuple[str, ...] = DEFAULT_COMPANY_BLOCKLIST

    def to_json(self) -> dict[str, Any]:
        """The extension's shape (``enabled``/``includeGroups``/``excludeTerms``)."""
        return {
            name: {
                "enabled": filt.enabled,
                "includeGroups": [list(g) for g in filt.include_groups],
                "excludeTerms": list(filt.exclude_terms),
            }
            for name, filt in (("ai", self.ai), ("backend", self.backend))
        }


@dataclass(frozen=True)
class ProfilePrefs:
    """Every preference the engine reads from the profile."""

    stack_wall: StackWallPrefs = DEFAULT_STACK_WALL_PREFS
    gates: GatePrefs = DEFAULT_GATE_PREFS
    search_targets: ProfileTargets = DEFAULT_PROFILE_TARGETS
    title_filters: TitleFilterPrefs = field(default_factory=TitleFilterPrefs)
    # Persona text: its defaults are the owner's, so a public profile sets them.
    cover_letter: CoverLetterPersona = OWNER_COVER_LETTER_PERSONA


DEFAULT_PREFS: Final[ProfilePrefs] = ProfilePrefs()

_TABLES: Final[frozenset[str]] = frozenset(
    {
        "stack_wall",
        "gates",
        "location",
        "search_targets",
        "title_filters",
        "cover_letter",
    }
)


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ProfileError(f"preferences.toml [{name}] must be a table")
    return value


def _only(table: dict[str, Any], keys: set[str], where: str) -> None:
    unknown = sorted(set(table) - keys)
    if unknown:
        raise ProfileError(
            f"preferences.toml [{where}] unknown key(s): {', '.join(unknown)}"
        )


def _strings(table: dict[str, Any], key: str, where: str) -> tuple[str, ...] | None:
    """The list of non-empty strings at ``key``; ``None`` when absent."""
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, list) or not all(
        isinstance(v, str) and v.strip() for v in value
    ):
        raise ProfileError(
            f"preferences.toml [{where}] {key} must be a list of non-empty strings"
        )
    return tuple(v.strip() for v in value)


def _string(table: dict[str, Any], key: str, where: str) -> str | None:
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(
            f"preferences.toml [{where}] {key} must be a non-empty string"
        )
    return value.strip()


def _flag(table: dict[str, Any], key: str, where: str) -> bool | None:
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, bool):
        raise ProfileError(f"preferences.toml [{where}] {key} must be true or false")
    return value


def _stack_wall(data: dict[str, Any]) -> StackWallPrefs:
    table = _table(data, "stack_wall")
    _only(table, {"core_language", "wall_languages"}, "stack_wall")
    core = _string(table, "core_language", "stack_wall")
    walls = _strings(table, "wall_languages", "stack_wall")
    default = DEFAULT_STACK_WALL_PREFS
    core = core or default.core_language
    known = ", ".join(LANGUAGE_PATTERNS)
    if core not in LANGUAGE_PATTERNS:
        raise ProfileError(
            f"preferences.toml [stack_wall] core_language {core!r} is unknown "
            f"(known: {known})"
        )
    for label in walls or ():
        if label not in LANGUAGE_PATTERNS or label == core:
            raise ProfileError(
                f"preferences.toml [stack_wall] wall_languages: {label!r} is "
                f"unknown or the core language (known: {known})"
            )
    return StackWallPrefs(
        core_language=core,
        wall_languages=walls if walls is not None else default.wall_languages,
    )


def _gates(data: dict[str, Any]) -> GatePrefs:
    gates = _table(data, "gates")
    _only(gates, {"allowed_languages", "flag_english_above_c1"}, "gates")
    location = _table(data, "location")
    _only(location, {"home_region"}, "location")
    default = DEFAULT_GATE_PREFS
    codes = _strings(gates, "allowed_languages", "gates")
    allowed = default.allowed_languages
    if codes is not None:
        allowed = tuple(dict.fromkeys(c.lower() for c in codes))
        unknown = [c for c in allowed if c not in LANGUAGE_CODES]
        if not allowed or unknown:
            raise ProfileError(
                "preferences.toml [gates] allowed_languages must be a non-empty "
                f"list of language codes (unknown: {unknown}; known: "
                f"{', '.join(sorted(LANGUAGE_CODES))})"
            )
    flag = _flag(gates, "flag_english_above_c1", "gates")
    region = _string(location, "home_region", "location") or default.home_region
    try:
        get_region(region)
    except UnknownRegionError as exc:
        raise ProfileError(f"preferences.toml [location] {exc}") from exc
    return GatePrefs(
        allowed_languages=allowed,
        flag_english_above_c1=default.flag_english_above_c1 if flag is None else flag,
        home_region=region,
    )


def _search_targets(data: dict[str, Any]) -> ProfileTargets:
    table = _table(data, "search_targets")
    _only(table, {"ai_roles", "backend_roles", "locations"}, "search_targets")
    default = DEFAULT_PROFILE_TARGETS
    ai = _strings(table, "ai_roles", "search_targets")
    backend = _strings(table, "backend_roles", "search_targets")
    locations = _strings(table, "locations", "search_targets")
    if locations is not None and not locations:
        raise ProfileError("preferences.toml [search_targets] locations is empty")
    return ProfileTargets(
        ai_roles=default.ai_roles if ai is None else ai,
        backend_roles=default.backend_roles if backend is None else backend,
        locations=default.locations if locations is None else locations,
    )


def _groups(table: dict[str, Any], where: str) -> list[list[str]] | None:
    if "include_groups" not in table:
        return None
    raw = table["include_groups"]
    if not isinstance(raw, list):
        raise ProfileError(f"preferences.toml [{where}] include_groups must be a list")
    return [list(_strings({"g": group}, "g", where) or ()) for group in raw]


def _profile_filter(
    table: dict[str, Any], base: TitleFilter, blocklist: tuple[str, ...], where: str
) -> TitleFilter:
    _only(table, {"enabled", "include_groups", "exclude_terms"}, where)
    enabled = _flag(table, "enabled", where)
    groups = _groups(table, where)
    terms = _strings(table, "exclude_terms", where)
    exclude = base.exclude_terms
    if terms is not None:
        exclude = [*terms, *(t for t in blocklist if t not in terms)]
    return TitleFilter(
        enabled=base.enabled if enabled is None else enabled,
        include_groups=base.include_groups if groups is None else groups,
        exclude_terms=exclude,
    )


def _title_filters(data: dict[str, Any]) -> TitleFilterPrefs:
    table = _table(data, "title_filters")
    _only(table, {"company_blocklist", "ai", "backend"}, "title_filters")
    given = _strings(table, "company_blocklist", "title_filters")
    blocklist = DEFAULT_COMPANY_BLOCKLIST if given is None else given
    filters: dict[str, TitleFilter] = {}
    for name, builtin in (
        ("ai", DEFAULT_TITLE_FILTER),
        ("backend", BACKEND_TITLE_FILTER),
    ):
        base = with_company_blocklist(builtin, blocklist)
        sub = table.get(name)
        if sub is not None and not isinstance(sub, dict):
            raise ProfileError(
                f"preferences.toml [title_filters.{name}] must be a table"
            )
        filters[name] = (
            base
            if sub is None
            else _profile_filter(sub, base, blocklist, f"title_filters.{name}")
        )
    return TitleFilterPrefs(
        ai=filters["ai"], backend=filters["backend"], company_blocklist=blocklist
    )


def _cover_letter(data: dict[str, Any]) -> CoverLetterPersona:
    table = _table(data, "cover_letter")
    _only(table, {"career_change", "branch_emphasis"}, "cover_letter")
    default = OWNER_COVER_LETTER_PERSONA
    emphasis = dict(default.branch_emphasis)
    given = table.get("branch_emphasis", {})
    if not isinstance(given, dict):
        raise ProfileError(
            "preferences.toml [cover_letter.branch_emphasis] must be a table"
        )
    where = "cover_letter.branch_emphasis"
    _only(given, set(default.branch_emphasis), where)
    for branch in given:
        # _string rejects an empty value, so a set key never falls back to the
        # owner's text.
        emphasis[branch] = _string(given, branch, where) or emphasis[branch]
    career = _string(table, "career_change", "cover_letter")
    return CoverLetterPersona(
        branch_emphasis=emphasis,
        career_change=career if career is not None else default.career_change,
    )


def load_preferences(preferences_toml: Path | None) -> ProfilePrefs:
    """The preferences in ``preferences_toml``; defaults when ``None``/absent."""
    if preferences_toml is None or not preferences_toml.is_file():
        return DEFAULT_PREFS
    try:
        data = tomllib.loads(preferences_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(
            f"preferences.toml unreadable ({preferences_toml}): {exc}"
        ) from exc
    unknown = sorted(set(data) - _TABLES)
    if unknown:
        raise ProfileError(f"preferences.toml unknown table(s): {', '.join(unknown)}")
    return ProfilePrefs(
        stack_wall=_stack_wall(data),
        gates=_gates(data),
        search_targets=_search_targets(data),
        title_filters=_title_filters(data),
        cover_letter=_cover_letter(data),
    )
