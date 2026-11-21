"""Salary reference table + lookup + card line (salary-estimate T2).

The table (``data/salary_reference.toml``, tracked, no personal data) holds
source-cited ranges keyed by branch (a role family's CV role: AI, BACKEND or
any role the profile declares, role-families T4), level (junior/mid) and
city (barcelona/madrid/spain_remote). A card shows, in order of preference:

1. the salary PUBLISHED in the offer text (``cva.match.salary``), or
2. the reference range for the offer's branch/level/city, labelled
   "Referencia" with source and date (never presented as a fact), or
3. nothing (no data, or a senior-or-above offer).

All Spanish strings are user-facing on purpose; code and comments are English.
"""

import re
import tomllib
import unicodedata
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field

from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.i18n import LEGACY_LANG, Lang, t
from cva.match.salary import PublishedSalary, extract_published_salary

# A family's CV role (``Family.role``: the id upper-cased).
Branch = Annotated[str, Field(pattern="^[A-Z][A-Z0-9_]*$")]
Level = Literal["junior", "mid"]
City = Literal["barcelona", "madrid", "spain_remote"]

CITY_LABEL: Final[dict[str, str]] = {
    "barcelona": "Barcelona",
    "madrid": "Madrid",
    "spain_remote": "España remoto",
}
_SOURCE_MAX: Final[int] = 60


class ReferenceRange(BaseModel):
    """One ``[[range]]`` block of the reference table (EUR gross per year)."""

    branch: Branch
    level: Level
    city: City
    low: int
    median: int
    high: int
    ask: int
    sources: list[str]
    confidence: str
    as_of: str
    placeholder: bool = False  # example rows: parsed but never shown on a card


def load_reference_table(path: Path) -> list[ReferenceRange]:
    """Parse the TOML table; a missing file is an empty table (no line shown)."""
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    rows = [ReferenceRange.model_validate(block) for block in data.get("range", [])]
    return [row for row in rows if not row.placeholder]


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


_SEMI_SENIOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:semi[\s-]?senior|ssr|mid|medior)\b"
)
_SENIOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:senior|sr|lead|staff|principal|head|manager|architect|arquitect[oa])\b"
)
_JUNIOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:junior|jr|trainee|intern|internship|becari[oa]|practicas|graduate|entry[\s-]level)\b"
)
_YEARS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<lo>\d{1,2})\s*(?:[-–]\s*\d{1,2}|\s(?:a|to|y|and|i)\s+\d{1,2})?\s*\+?\s*"
    r"(?:anos|years?|anys)(?:\s+de)?(?:\s+of)?\s+(?:experiencia|experience)"
)


def infer_level(title: str | None, text: str | None) -> Level | None:
    """junior | mid | None (senior and above: no reference shown).

    Title markers win; then the years-of-experience requirement (<2 junior,
    2-4 mid, 5+ none); no marker at all counts as junior (owner decision).
    """
    folded_title = _fold(title or "")
    if _SEMI_SENIOR_RE.search(folded_title):
        return "mid"
    if _SENIOR_RE.search(folded_title):
        return None
    if _JUNIOR_RE.search(folded_title):
        return "junior"
    years = _YEARS_RE.search(_fold(text or ""))
    if years is not None:
        low = int(years.group("lo"))
        if low < 2:
            return "junior"
        return "mid" if low <= 4 else None
    return "junior"


_FULL_REMOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"100\s*%\s*(?:remot|teletrabaj)|full(?:y)?[\s-]remote|totalmente remot|"
    r"trabajo remoto|remote[\s-]first|remote position|puesto remoto"
)
_ANY_REMOTE_RE: Final[re.Pattern[str]] = re.compile(r"remot|teletrabaj|\bremote\b")
_CITY_RES: Final[dict[str, re.Pattern[str]]] = {
    "barcelona": re.compile(r"\bbarcelona\b|\bbcn\b"),
    "madrid": re.compile(r"\bmadrid\b"),
}


def infer_city(title: str | None, text: str | None) -> tuple[City, bool]:
    """(city, detected). Full-remote wins; else the earliest named city;
    else any remote hint; else spain_remote flagged as undetected."""
    folded = _fold(f"{title or ''}\n{text or ''}")
    if _FULL_REMOTE_RE.search(folded):
        return "spain_remote", True
    hits = [
        (m.start(), name)
        for name, pattern in _CITY_RES.items()
        if (m := pattern.search(folded)) is not None
    ]
    if hits:
        city = min(hits)[1]
        return ("barcelona" if city == "barcelona" else "madrid"), True
    return "spain_remote", bool(_ANY_REMOTE_RE.search(folded))


def lookup(
    table: list[ReferenceRange], branch: Branch, level: Level, city: City
) -> ReferenceRange | None:
    """Exact (branch, level, city) row, else the same branch/level spain_remote row."""
    for wanted in (city, "spain_remote"):
        for row in table:
            if (row.branch, row.level, row.city) == (branch, level, wanted):
                return row
    return None


_SPAIN_NAMES: Final[frozenset[str]] = frozenset(
    {"spain", "espana", "es", "esp", "reino de espana", "kingdom of spain"}
)


def profile_city(
    work_city: str | None, country: str | None, work_modes: list[str]
) -> City | None:
    """The table city for a ``/setup`` profile (onboarding salary hint):
    Barcelona or Madrid by name; another Spanish city counts as
    ``spain_remote`` only when remote work is accepted. ``None`` (no
    reference at all) outside Spain or when nothing honest maps: the table
    only holds Spanish EUR ranges."""
    if _fold((country or "").strip()) not in _SPAIN_NAMES:
        return None
    city = _fold((work_city or "").strip())
    if city in ("barcelona", "bcn"):
        return "barcelona"
    if city == "madrid":
        return "madrid"
    return "spain_remote" if "remote" in work_modes else None


def profile_reference(
    table: list[ReferenceRange], branches: list[str], level: str, city: City | None
) -> ReferenceRange | None:
    """The first of ``branches`` (CV roles, in the profile's order) with a
    reference row for ``level``/``city`` (:func:`lookup`); ``None`` for a
    senior level, an unknown city or no row: never an invented range."""
    if city is None or level not in ("junior", "mid"):
        return None
    wanted: Level = "junior" if level == "junior" else "mid"
    for branch in branches:
        row = lookup(table, branch, wanted, city)
        if row is not None:
            return row
    return None


def eur_text(amount: int) -> str:
    """Spanish thousands separator: 28000 -> ``28.000``."""
    return f"{amount:,}".replace(",", ".")


def _k(amount: int) -> str:
    value = round(amount / 1000, 1)
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')}k"


def _short_source(source: str) -> str:
    return source if len(source) <= _SOURCE_MAX else source[: _SOURCE_MAX - 1] + "…"


def city_label(city: str, lang: Lang) -> str:
    """Display name of a reference city (``bot.salary.city.<id>``)."""
    return t(f"bot.salary.city.{city}", lang) if city in CITY_LABEL else city


def published_line(found: PublishedSalary, lang: Lang = LEGACY_LANG) -> str:
    """``💶 Salario publicado: 28.000–34.000 € brutos/año`` (+ conversion note)."""
    amount = (
        eur_text(found.min_eur)
        if found.min_eur == found.max_eur
        else f"{eur_text(found.min_eur)}–{eur_text(found.max_eur)}"
    )
    note = f" ({found.note})" if found.note else ""
    return t("bot.salary.published", lang, amount=amount, note=note)


def reference_line(
    row: ReferenceRange,
    *,
    location_note: str = "",
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> str:
    """``💶 Referencia: 28–34k (mediana 31k) · AI junior · Barcelona · ...``

    The branch is shown with its family's label (the role itself when no
    family of ``families`` has it).
    """
    location = city_label(row.city, lang) + location_note
    family = families.get(row.branch)
    branch = family.label if family is not None else row.branch
    source = (
        _short_source(row.sources[0])
        if row.sources
        else t("bot.salary.no_source", lang)
    )
    line = t(
        "bot.salary.reference",
        lang,
        low=_k(row.low)[:-1],
        high=_k(row.high),
        median=_k(row.median),
        branch=branch,
        level=row.level,
        location=location,
        source=source,
        as_of=row.as_of,
        ask=_k(row.ask),
    )
    if row.confidence == "low":
        line += t("bot.salary.low_confidence", lang)
    return line


def salary_line(
    *,
    title: str | None,
    text: str | None,
    perfil: object,
    table: list[ReferenceRange],
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> str:
    """The card line for an offer, or ``""`` when there is nothing to show.

    The branch is the CV role of the offer's family in ``families`` (unknown
    or NULL: the default family); a family without rows shows no reference.
    """
    published = extract_published_salary(text, lang=lang)
    if published is not None:
        return published_line(published, lang)
    level = infer_level(title, text)
    if level is None:
        return ""
    branch = families.role_for(perfil)
    city, detected = infer_city(title, text)
    row = lookup(table, branch, level, city)
    if row is None:
        return ""
    note = ""
    if not detected:
        note = t("bot.salary.no_location", lang)
    elif row.city != city:  # fell back to the spain_remote row
        note = t("bot.salary.no_city_data", lang, city=city_label(city, lang))
    return reference_line(row, location_note=note, families=families, lang=lang)
