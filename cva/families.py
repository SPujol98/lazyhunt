"""Role families: the kinds of job a profile is looking for (role-families T1).

A role family groups everything the engine does per kind of job: which offer
titles route to it, its title pre-filter, its CV languages, its score
threshold, its search roles and its cover-letter emphasis. A profile declares
its own families in ``preferences.toml`` (``[families.<id>]``, parsed in
``cva/profile_prefs.py``); with none declared, the built-in ``ai`` and
``backend`` families reproduce the engine's historical behaviour exactly.

Two spellings name the same family: the offer profile is the lowercase id
(``ai``, ``backend``; the DB ``perfil`` column, the bridge ``profile`` field)
and the CV role is ``id.upper()`` (``AI``, ``BACKEND``; base CV manifest keys
``<ROLE>_<LANG>``, Telegram callbacks).

This module imports nothing from ``cva`` but :mod:`cva.title_filter`, so
:mod:`cva.profile` can delegate to it without an import cycle.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from cva.title_filter import BACKEND_TITLE_FILTER, DEFAULT_TITLE_FILTER, TitleFilter

# MUST mirror the first include group of DEFAULT_TITLE_FILTER in
# extension/filter.js (the extension `ai` profile); a test pins the two together.
AI_TITLE_TERMS: Final[tuple[str, ...]] = (
    "AI", "IA", "Artificial Intelligence", "Inteligencia Artificial", "GenAI",
    "Generative AI", "LLM", "LLMOps", "MLOps", "RAG", "Agentic", "AI Agent",
    "Machine Learning", "NLP", "Prompt",
)  # fmt: skip

# CV languages a family may declare (the base CV manifest keys' suffixes).
CV_LANGUAGES: Final[tuple[str, ...]] = ("es", "en", "ca")

FAMILY_ID_RE: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]*")
# Longest family id: the role rides in Telegram callback data (64 bytes max),
# e.g. ``variant:<offer id>:<ROLE>:<lang>`` (role-families T5).
FAMILY_ID_MAX_LEN: Final[int] = 32

# Old CV role spelling (Data Science) that legacy rows and buttons still carry.
_LEGACY_ALIASES: Final[dict[str, str]] = {"ds": "ai"}


def normalize_title(text: str) -> str:
    """Lowercase, strip diacritics, collapse non-alphanumerics (as filter.js)."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


@lru_cache(maxsize=64)
def _terms_pattern(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    alternatives = [re.escape(n) for n in (normalize_title(t) for t in terms) if n]
    if not alternatives:
        return None
    return re.compile("(?:^|\\s)(?:" + "|".join(alternatives) + ")(?:$|\\s)")


@dataclass(frozen=True)
class Family:
    """One role family.

    ``threshold`` ``None`` keeps the ``Settings`` gate (``threshold`` for the
    default family, ``backend_threshold`` for ``backend``); ``search_roles``
    empty and ``cover_letter_emphasis`` ``None`` keep the profile's
    ``[search_targets]`` / ``[cover_letter.branch_emphasis]`` values.
    """

    id: str
    label: str
    languages: tuple[str, ...]
    title_terms: tuple[str, ...] = ()
    title_filter: TitleFilter = DEFAULT_TITLE_FILTER
    threshold: int | None = None
    search_roles: tuple[str, ...] = ()
    cover_letter_emphasis: str | None = None

    @property
    def role(self) -> str:
        """The CV role key: ``id.upper()``."""
        return self.id.upper()

    def matches_title(self, title: str | None) -> bool:
        """True when ``title`` contains one of ``title_terms`` (whole word,
        case/accent-blind)."""
        pattern = _terms_pattern(self.title_terms)
        if pattern is None or not title:
            return False
        return pattern.search(normalize_title(title)) is not None


class FamilyRegistry:
    """The profile's role families, in declared order, with one default."""

    def __init__(self, families: Iterable[Family], default: str | None = None) -> None:
        self._families: tuple[Family, ...] = tuple(families)
        if not self._families:
            raise ValueError("a family registry needs at least one family")
        ids = [f.id for f in self._families]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate family ids: {ids}")
        for fid in ids:
            if not FAMILY_ID_RE.fullmatch(fid):
                raise ValueError(f"invalid family id {fid!r}")
            if len(fid) > FAMILY_ID_MAX_LEN:
                raise ValueError(
                    f"family id {fid!r} too long (max {FAMILY_ID_MAX_LEN})"
                )
        self._by_id: dict[str, Family] = {f.id: f for f in self._families}
        chosen = ids[0] if default is None else default
        if chosen not in self._by_id:
            raise ValueError(f"default family {chosen!r} is not declared")
        self._default = self._by_id[chosen]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FamilyRegistry):
            return NotImplemented
        return (self._families, self._default.id) == (
            other._families,
            other._default.id,
        )

    def __hash__(self) -> int:
        return hash((self._families, self._default.id))

    def __repr__(self) -> str:
        return f"FamilyRegistry({list(self.ids)!r}, default={self._default.id!r})"

    @property
    def families(self) -> tuple[Family, ...]:
        """Every family, in declared order."""
        return self._families

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(f.id for f in self._families)

    @property
    def default(self) -> Family:
        return self._default

    def get(self, value: object) -> Family | None:
        """The family named by ``value`` (id or role, case-insensitive; legacy
        ``DS`` -> ``ai``); ``None`` when unknown."""
        if not isinstance(value, str):
            return None
        key = value.strip().lower()
        if key in self._by_id:
            return self._by_id[key]
        alias = _LEGACY_ALIASES.get(key)
        return self._by_id.get(alias) if alias is not None else None

    def normalize(self, value: object) -> str:
        """The offer profile stored for ``value``.

        Exactly a declared id keeps it; legacy ``ds``/``DS`` maps to ``ai``
        (when declared); anything else (``None``, unknown, other spellings) is
        the default family id. Deliberately case-sensitive on ids: a persisted
        ``perfil`` is always lowercase, and ``"Backend"`` has always meant the
        default.
        """
        if isinstance(value, str):
            if value in self._by_id:
                return value
            alias = _LEGACY_ALIASES.get(value.lower())
            if alias is not None and alias in self._by_id:
                return alias
        return self._default.id

    def resolve(self, value: object) -> Family:
        """The family named by ``value`` (as :meth:`get`), else the default:
        unknown roles from old Telegram buttons degrade, never crash."""
        found = self.get(value)
        return found if found is not None else self._default

    def by_role(self, role: object) -> Family | None:
        """The family whose CV role is exactly ``role`` (case-insensitive, no
        legacy aliases): what ``/cv`` and the CLI accept."""
        if not isinstance(role, str):
            return None
        return next((f for f in self._families if f.role == role.upper()), None)

    @property
    def languages(self) -> tuple[str, ...]:
        """Every CV language some family declares, in ``CV_LANGUAGES`` order."""
        return tuple(
            code
            for code in CV_LANGUAGES
            if any(code in f.languages for f in self._families)
        )

    def next_role(self, role: object) -> str:
        """Role after ``role`` in declared order (unknown -> the default's)."""
        found = self.get(role)
        if found is None:
            return self._default.role
        index = self._families.index(found)
        return self._families[(index + 1) % len(self._families)].role

    def route(self, profile: object, title: str | None) -> str:
        """Offer profile of a beacon job: a family whose ``title_terms`` match
        the title wins (built-ins: an AI title is ``ai``); otherwise the
        beacon's ``profile`` (normalized)."""
        for family in self._families:
            if family.matches_title(title):
                return family.id
        return self.normalize(profile)

    def role_for(self, profile: object) -> str:
        """CV role a card starts on for an offer of ``profile``."""
        return self._by_id[self.normalize(profile)].role

    def infer(self, title: str | None) -> str:
        """Offer profile from a title.

        A blank title is the default family. Otherwise the first family (in
        declared order) whose ``title_terms`` match; else the first family
        without ``title_terms`` (the catch-all); else the default family.
        """
        if title is None or not title.strip():
            return self._default.id
        for family in self._families:
            if family.matches_title(title):
                return family.id
        for family in self._families:
            if not family.title_terms:
                return family.id
        return self._default.id


BUILTIN_AI: Final[Family] = Family(
    id="ai",
    label="AI",
    languages=("es", "en", "ca"),
    title_terms=AI_TITLE_TERMS,
    title_filter=DEFAULT_TITLE_FILTER,
)
BUILTIN_BACKEND: Final[Family] = Family(
    id="backend",
    label="Backend",
    languages=("es", "en"),
    title_filter=BACKEND_TITLE_FILTER,
)
BUILTIN_FAMILIES: Final[tuple[Family, ...]] = (BUILTIN_AI, BUILTIN_BACKEND)
BUILTIN_REGISTRY: Final[FamilyRegistry] = FamilyRegistry(BUILTIN_FAMILIES, "ai")
