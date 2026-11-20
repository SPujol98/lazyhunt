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
    city = "Valencia"                  # home city, free text; default: none
    country = "España"                 # home country NAME, free text; default: none

    [search]                           # onboarding answers (card warnings)
    work_modes = ["remote", "hybrid"]  # subset of remote/hybrid/onsite; default: all
    level = "mid"                      # junior | mid | senior; default: none
    min_salary = 30000                 # gross per year, integer >= 0; default: none
    currency = "EUR"                   # ISO 4217 code of min_salary; default EUR

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

    [families.frontend]                # cva/families.py; role families
    label = "Frontend"                 # default: the id
    languages = ["es", "en"]           # CV languages (es/en/ca); default es+en
    title_terms = ["Frontend", "React"]  # titles with them route here
    threshold = 55                     # 0-100; default: the Settings gate
    search_roles = ["Frontend Developer"]
    cover_letter_emphasis = "Frontend branch: emphasise ..."
    default = true                     # at most one; default: the first family
    [families.frontend.title_filter]   # same keys as [title_filters.ai]
    include_groups = [["Frontend", "React"]]
    exclude_terms = ["Senior"]         # the blocklist is appended

``[location] city``/``country`` are matched by NAME against the offer's
location text (no distances, no ISO codes: offers say "Madrid, España", not
"ES"); ``home_region`` keeps selecting the detailed region pack.
``[search_targets] locations`` is not reused for the city: it lists the search
locations sent to the portals (it may hold "remoto"), not where the candidate
lives. When it is not written, a profile that states ``[search] work_modes``
or ``[location] city``/``country`` (the ``/setup`` answers) gets it derived
when read: the city (else the country) if on-site or hybrid work is accepted,
then "remoto" (Spanish or Catalan offers accepted) or "remote" if remote work
is; a hand-written ``locations`` always wins, and a profile with neither keeps
the engine default ``["Barcelona", "remoto"]``. Case is normalised (``work_modes``/``level`` lower, ``currency`` upper)
and repeated work modes are dropped. ``[search]`` drives the ⚠️ offer-card
warnings (work mode, salary below the minimum; never a discard), and
``[location] city``/``country`` the generic by-name location gate when the
city is outside ``home_region`` (see ``cva/match/warnings.py`` and
``cva/match/gates.py``).

Family ids are ``[a-z][a-z0-9_]*`` (the CV role key is ``id.upper()``). With no
``[families]`` table the built-in ``ai`` and ``backend`` families apply, filled
from ``[title_filters.ai|backend]``, ``[search_targets] ai_roles|backend_roles``
and ``[cover_letter.branch_emphasis] AI|BACKEND`` (that table also takes the
role of any declared family, e.g. ``FRONTEND``; a family's own
``cover_letter_emphasis`` wins over it). Declared families replace
the built-ins; a declared ``ai`` or ``backend`` starts from its built-in values.
An offer title goes to the first family whose ``title_terms`` match, else to
the first family without ``title_terms``, else to the default family.

Thresholds (match score, gray zone, ...) are NOT here on purpose: they are
operational knobs that ``Settings`` (``config.toml`` -> ``config.local.toml``
-> ``CVA_*`` env) already layers per machine, and the bot's cards, daily caps
and tests read them from there.
"""

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

from cva.families import (
    BUILTIN_REGISTRY,
    CV_LANGUAGES,
    FAMILY_ID_MAX_LEN,
    FAMILY_ID_RE,
    Family,
    FamilyRegistry,
)
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


WORK_MODES: Final[tuple[str, ...]] = ("remote", "hybrid", "onsite")
LEVELS: Final[tuple[str, ...]] = ("junior", "mid", "senior")
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True)
class SearchPrefs:
    """``[search]``: what the candidate accepts (onboarding-v2 S4/S7)."""

    # Accepted work modes, in WORK_MODES order of first mention; all = no warning.
    work_modes: tuple[str, ...] = WORK_MODES
    level: str | None = None  # one of LEVELS; None = not stated
    min_salary: int | None = None  # gross per year in ``currency``; None = any
    currency: str = "EUR"  # ISO 4217


@dataclass(frozen=True)
class LocationPrefs:
    """``[location] city``/``country``: where the candidate lives (S6)."""

    city: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class ProfilePrefs:
    """Every preference the engine reads from the profile."""

    stack_wall: StackWallPrefs = DEFAULT_STACK_WALL_PREFS
    gates: GatePrefs = DEFAULT_GATE_PREFS
    search_targets: ProfileTargets = DEFAULT_PROFILE_TARGETS
    title_filters: TitleFilterPrefs = field(default_factory=TitleFilterPrefs)
    # Persona text: its defaults are the owner's, so a public profile sets them.
    cover_letter: CoverLetterPersona = OWNER_COVER_LETTER_PERSONA
    # Role families ([families.<id>]); the built-ins when none are declared.
    families: FamilyRegistry = field(default_factory=lambda: _BUILTINS)
    # Questionnaire answers (onboarding-v2): [search] and [location] city/country.
    search: SearchPrefs = SearchPrefs()
    location: LocationPrefs = LocationPrefs()


def _with_builtin_values(
    filters: TitleFilterPrefs, targets: ProfileTargets, persona: CoverLetterPersona
) -> dict[str, Family]:
    """The built-in families filled from the profile's other tables."""
    emphasis = persona.branch_emphasis
    values = {
        "ai": (filters.ai, targets.ai_roles),
        "backend": (filters.backend, targets.backend_roles),
    }
    return {
        fam.id: replace(
            fam,
            title_filter=values[fam.id][0],
            search_roles=values[fam.id][1],
            cover_letter_emphasis=emphasis.get(fam.role),
        )
        for fam in BUILTIN_REGISTRY.families
    }


_BUILTINS: Final[FamilyRegistry] = FamilyRegistry(
    _with_builtin_values(
        TitleFilterPrefs(), DEFAULT_PROFILE_TARGETS, OWNER_COVER_LETTER_PERSONA
    ).values(),
    BUILTIN_REGISTRY.default.id,
)

DEFAULT_PREFS: Final[ProfilePrefs] = ProfilePrefs()

_TABLES: Final[frozenset[str]] = frozenset(
    {
        "stack_wall",
        "gates",
        "location",
        "search_targets",
        "title_filters",
        "cover_letter",
        "families",
        "search",
    }
)

_FAMILY_KEYS: Final[set[str]] = {
    "label",
    "languages",
    "title_terms",
    "title_filter",
    "threshold",
    "search_roles",
    "cover_letter_emphasis",
    "default",
}


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
    _only(location, {"home_region", "city", "country"}, "location")
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


def _search(data: dict[str, Any]) -> SearchPrefs:
    table = _table(data, "search")
    _only(table, {"work_modes", "level", "min_salary", "currency"}, "search")
    default = SearchPrefs()
    modes = _strings(table, "work_modes", "search")
    if modes is not None:
        modes = tuple(dict.fromkeys(m.lower() for m in modes))
        unknown = [m for m in modes if m not in WORK_MODES]
        if not modes or unknown:
            raise ProfileError(
                "preferences.toml [search] work_modes must be a non-empty list of "
                f"{', '.join(WORK_MODES)} (unknown: {unknown})"
            )
    level = _string(table, "level", "search")
    if level is not None:
        level = level.lower()
        if level not in LEVELS:
            raise ProfileError(
                f"preferences.toml [search] level must be one of {', '.join(LEVELS)}"
            )
    salary = table.get("min_salary")
    if salary is not None and (
        isinstance(salary, bool) or not isinstance(salary, int) or salary < 0
    ):
        raise ProfileError(
            "preferences.toml [search] min_salary must be a whole number >= 0 "
            "(gross per year)"
        )
    currency = _string(table, "currency", "search")
    if currency is not None:
        currency = currency.upper()
        if _CURRENCY_RE.fullmatch(currency) is None:
            raise ProfileError(
                "preferences.toml [search] currency must be a 3-letter ISO 4217 "
                "code such as EUR"
            )
    return SearchPrefs(
        work_modes=default.work_modes if modes is None else modes,
        level=level,
        min_salary=salary,
        currency=default.currency if currency is None else currency,
    )


def _location(data: dict[str, Any]) -> LocationPrefs:
    # Unknown keys are rejected by _gates, which owns home_region.
    table = _table(data, "location")
    return LocationPrefs(
        city=_string(table, "city", "location"),
        country=_string(table, "country", "location"),
    )


# Offer languages whose portals search remote work as "remoto" (the spelling
# the extension's portal readers and the engine default already use).
_REMOTO_LANGUAGES: Final[frozenset[str]] = frozenset({"es", "ca"})


def _answer_locations(
    data: dict[str, Any], search: SearchPrefs, location: LocationPrefs, gates: GatePrefs
) -> tuple[str, ...] | None:
    """Search locations derived from the onboarding answers (onboarding-v2 T6).

    Only for a profile that states where the candidate works (``[search]
    work_modes`` or ``[location] city``/``country``); ``None`` keeps the
    engine default. The city (else the country) when on-site or hybrid work
    is accepted, then a remote search when remote work is: "remoto" if
    Spanish or Catalan offers are accepted, else "remote".
    """
    stated = "work_modes" in _table(data, "search")
    place = location.city or location.country
    if not stated and place is None:
        return None
    out: list[str] = []
    if place is not None and {"onsite", "hybrid"} & set(search.work_modes):
        out.append(place)
    if "remote" in search.work_modes:
        spanish = _REMOTO_LANGUAGES & set(gates.allowed_languages)
        out.append("remoto" if spanish else "remote")
    return tuple(out) or None


def _search_targets(
    data: dict[str, Any], answered: tuple[str, ...] | None = None
) -> ProfileTargets:
    """``[search_targets]``; ``answered`` (derived from the onboarding answers)
    replaces the default ``locations`` only, never a hand-written list."""
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
        locations=(answered or default.locations) if locations is None else locations,
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
    # The built-in roles, plus the role (id upper-cased) of every declared
    # family (role-families T4).
    declared = data.get("families")
    roles = set(default.branch_emphasis)
    if isinstance(declared, dict):
        roles |= {str(fid).upper() for fid in declared}
    _only(given, roles, where)
    for branch in given:
        # _string rejects an empty value, so a set key never falls back to the
        # owner's text.
        text = _string(given, branch, where)
        if text is not None:
            emphasis[branch] = text
    career = _string(table, "career_change", "cover_letter")
    return CoverLetterPersona(
        branch_emphasis=emphasis,
        career_change=career if career is not None else default.career_change,
    )


def _family(
    fid: str,
    table: dict[str, Any],
    builtin: Family | None,
    blocklist: tuple[str, ...],
) -> tuple[Family, bool]:
    where = f"families.{fid}"
    _only(table, _FAMILY_KEYS, where)
    base = builtin or Family(
        id=fid,
        label=fid,
        languages=("es", "en"),
        title_filter=TitleFilter(enabled=True, exclude_terms=list(blocklist)),
    )
    languages = _strings(table, "languages", where)
    if languages is not None:
        languages = tuple(dict.fromkeys(code.lower() for code in languages))
        unknown = [c for c in languages if c not in CV_LANGUAGES]
        if not languages or unknown:
            raise ProfileError(
                f"preferences.toml [{where}] languages must be a non-empty list "
                f"of {', '.join(CV_LANGUAGES)} (unknown: {unknown})"
            )
    threshold = table.get("threshold", base.threshold)
    if threshold is not None and (
        isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or not 0 <= threshold <= 100
    ):
        raise ProfileError(
            f"preferences.toml [{where}] threshold must be an integer 0-100"
        )
    sub = table.get("title_filter")
    if sub is not None and not isinstance(sub, dict):
        raise ProfileError(f"preferences.toml [{where}.title_filter] must be a table")
    title_filter = (
        base.title_filter
        if sub is None
        else _profile_filter(sub, base.title_filter, blocklist, f"{where}.title_filter")
    )
    label = _string(table, "label", where)
    terms = _strings(table, "title_terms", where)
    roles = _strings(table, "search_roles", where)
    emphasis = _string(table, "cover_letter_emphasis", where)
    family = Family(
        id=fid,
        label=base.label if label is None else label,
        languages=base.languages if languages is None else languages,
        title_terms=base.title_terms if terms is None else terms,
        title_filter=title_filter,
        threshold=threshold,
        search_roles=base.search_roles if roles is None else roles,
        cover_letter_emphasis=base.cover_letter_emphasis
        if emphasis is None
        else emphasis,
    )
    return family, bool(_flag(table, "default", where))


def _families(
    data: dict[str, Any],
    filters: TitleFilterPrefs,
    targets: ProfileTargets,
    persona: CoverLetterPersona,
) -> FamilyRegistry:
    builtins = _with_builtin_values(filters, targets, persona)
    if "families" not in data:
        return FamilyRegistry(builtins.values(), BUILTIN_REGISTRY.default.id)
    table = _table(data, "families")
    if not table:
        raise ProfileError(
            "preferences.toml [families] must declare at least one family"
        )
    families: list[Family] = []
    defaults: list[str] = []
    for fid, sub in table.items():
        if not FAMILY_ID_RE.fullmatch(fid):
            raise ProfileError(
                f"preferences.toml [families.{fid}] invalid id: use lowercase "
                "letters, digits and _, starting with a letter"
            )
        if len(fid) > FAMILY_ID_MAX_LEN:
            raise ProfileError(
                f"preferences.toml [families.{fid}] id too long: use at most "
                f"{FAMILY_ID_MAX_LEN} characters (it rides in Telegram buttons)"
            )
        if not isinstance(sub, dict):
            raise ProfileError(f"preferences.toml [families.{fid}] must be a table")
        family, is_default = _family(
            fid, sub, builtins.get(fid), filters.company_blocklist
        )
        families.append(family)
        if is_default:
            defaults.append(fid)
    if len(defaults) > 1:
        raise ProfileError(
            "preferences.toml [families] only one default family is allowed "
            f"(default = true in: {', '.join(defaults)})"
        )
    return FamilyRegistry(families, defaults[0] if defaults else None)


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
    return preferences_from_data(data)


def preferences_from_data(data: dict[str, Any]) -> ProfilePrefs:
    """Validate a parsed ``preferences.toml`` document (the strict loader).

    Raises :class:`~cva.profile_identity.ProfileError` on any unknown table or
    key and any wrongly typed value; used by :func:`load_preferences` and by
    the writer (``cva/profile_writer.py``) before it replaces the file.
    """
    unknown = sorted(set(data) - _TABLES)
    if unknown:
        raise ProfileError(f"preferences.toml unknown table(s): {', '.join(unknown)}")
    search = _search(data)
    location = _location(data)
    gates = _gates(data)
    targets = _search_targets(data, _answer_locations(data, search, location, gates))
    filters = _title_filters(data)
    persona = _cover_letter(data)
    families = _families(data, filters, targets, persona)
    if "families" in data:
        # Search suggestions follow the declared families (cva/suggest.py).
        targets = replace(targets, families=families)
    return ProfilePrefs(
        stack_wall=_stack_wall(data),
        gates=gates,
        search_targets=targets,
        title_filters=filters,
        cover_letter=persona,
        families=families,
        search=search,
        location=location,
    )
