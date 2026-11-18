"""Salary reference table + lookup + card line (salary-estimate T2).

The table (``data/salary_reference.toml``, tracked, no personal data) holds
source-cited ranges keyed by branch (AI/BACKEND), level (junior/mid) and
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
from typing import Final, Literal

from pydantic import BaseModel

from cva.match.salary import PublishedSalary, extract_published_salary
from cva.profile import normalize_profile

Branch = Literal["AI", "BACKEND"]
Level = Literal["junior", "mid"]
City = Literal["barcelona", "madrid", "spain_remote"]

CITY_LABEL: Final[dict[str, str]] = {
    "barcelona": "Barcelona",
    "madrid": "Madrid",
    "spain_remote": "España remoto",
}
_BRANCH_LABEL: Final[dict[Branch, str]] = {"AI": "AI", "BACKEND": "Backend"}
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


_SEMI_SENIOR_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:semi[\s-]?senior|ssr|mid|medior)\b")
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


def eur_text(amount: int) -> str:
    """Spanish thousands separator: 28000 -> ``28.000``."""
    return f"{amount:,}".replace(",", ".")


def _k(amount: int) -> str:
    value = round(amount / 1000, 1)
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')}k"


def _short_source(source: str) -> str:
    return source if len(source) <= _SOURCE_MAX else source[: _SOURCE_MAX - 1] + "…"


def published_line(found: PublishedSalary) -> str:
    """``💶 Salario publicado: 28.000–34.000 € brutos/año`` (+ conversion note)."""
    amount = (
        eur_text(found.min_eur)
        if found.min_eur == found.max_eur
        else f"{eur_text(found.min_eur)}–{eur_text(found.max_eur)}"
    )
    note = f" ({found.note})" if found.note else ""
    return f"💶 Salario publicado: {amount} € brutos/año{note}"


def reference_line(row: ReferenceRange, *, location_note: str = "") -> str:
    """``💶 Referencia: 28–34k (mediana 31k) · AI junior · Barcelona · ...``"""
    location = CITY_LABEL[row.city] + location_note
    source = _short_source(row.sources[0]) if row.sources else "sin fuente"
    line = (
        f"💶 Referencia: {_k(row.low)[:-1]}–{_k(row.high)} (mediana {_k(row.median)}) · "
        f"{_BRANCH_LABEL[row.branch]} {row.level} · {location} · "
        f"fuente: {source}, {row.as_of} · pide ~{_k(row.ask)}"
    )
    if row.confidence == "low":
        line += " · confianza baja"
    return line


def salary_line(
    *,
    title: str | None,
    text: str | None,
    perfil: object,
    table: list[ReferenceRange],
) -> str:
    """The card line for an offer, or ``""`` when there is nothing to show."""
    published = extract_published_salary(text)
    if published is not None:
        return published_line(published)
    level = infer_level(title, text)
    if level is None:
        return ""
    branch: Branch = "BACKEND" if normalize_profile(perfil) == "backend" else "AI"
    city, detected = infer_city(title, text)
    row = lookup(table, branch, level, city)
    if row is None:
        return ""
    note = ""
    if not detected:
        note = " (ubicación no detectada)"
    elif row.city != city:  # fell back to the spain_remote row
        note = f" (sin dato de {CITY_LABEL[city]})"
    return reference_line(row, location_note=note)
