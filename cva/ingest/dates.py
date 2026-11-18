"""Offer posting date and application deadline extraction (pure, never guesses).

Sources, in order of trust: structured JSON-LD (``datePosted`` /
``validThrough``) and then explicit, marker-anchored text patterns in
ES/EN/CA ("fecha límite: 30/11/2026", "deadline: November 30, 2026",
"data límit ...", "publicada el ...", "publicada hace 3 días").

Anything unclear yields ``None``: impossible dates, dates without a year,
ambiguous numeric dates under an English marker, months-old relative ages,
unmarked dates. Past deadlines ARE returned (they signal a closed offer).
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

_MIN_YEAR: Final[int] = 2000
_MAX_DEADLINE_YEARS_AHEAD: Final[int] = 2

_MONTHS: Final[dict[str, int]] = {}
for _number, _names in enumerate(
    (
        ("enero", "gener", "january", "jan"),
        ("febrero", "febrer", "february", "feb"),
        ("marzo", "marc", "march", "mar"),
        ("abril", "april", "apr"),
        ("mayo", "maig", "may"),
        ("junio", "juny", "june", "jun"),
        ("julio", "juliol", "july", "jul"),
        ("agosto", "agost", "august", "aug"),
        ("septiembre", "setiembre", "setembre", "september", "sept", "sep"),
        ("octubre", "october", "oct"),
        ("noviembre", "novembre", "november", "nov"),
        ("diciembre", "desembre", "december", "dec"),
    ),
    start=1,
):
    for _name in _names:
        _MONTHS[_name] = _number
_MONTH_RE: Final[str] = "|".join(sorted(_MONTHS, key=len, reverse=True))

# English markers allow month/day only when it is unambiguous; ES/CA markers
# read numeric dates day-first.
_DEADLINE_ES_CA: Final[str] = (
    r"fecha\s+(?:l[ií]mite|de\s+cierre)(?:\s+de\s+(?:inscripci[oó]n|solicitud|"
    r"candidatura)s?)?|plazo(?:\s+de\s+(?:inscripci[oó]n|solicitud|presentaci[oó]n))?"
    r"(?:\s+hasta)?|cierre\s+de\s+(?:candidaturas|inscripciones|ofertas)|"
    r"hasta\s+el\s+d[ií]a|"
    r"data\s+l[ií]mit(?:\s+d['’]inscripci[oó])?|data\s+de\s+tancament|"
    r"termini(?:\s+d['’]inscripci[oó])?(?:\s+fins)?"
)
_DEADLINE_EN: Final[str] = (
    r"(?:application\s+)?deadline|apply\s+(?:by|before)|"
    r"applications?\s+(?:close|closes|due)(?:\s+on)?|closing\s+date|"
    r"valid\s+(?:until|through)|expires?\s+on"
)
_POSTED_ES_CA: Final[str] = (
    r"publicad[ao]\s+el|fecha\s+de\s+publicaci[oó]n|"
    r"data\s+de\s+publicaci[oó]|publicat\s+el|publicada\s+el"
)
_POSTED_EN: Final[str] = r"posted\s+on|published\s+on|date\s+posted|posting\s+date"

_DATE_BODY: Final[str] = (
    r"(?P<iso>\d{4})-(?P<iso_m>\d{1,2})-(?P<iso_d>\d{1,2})"
    r"|(?P<a>\d{1,2})[/.\-](?P<b>\d{1,2})[/.\-](?P<year>\d{4})"
    rf"|(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:de\s+|d['’])?(?P<m1>{_MONTH_RE})\.?"
    r"(?:\s+(?:de|del))?,?\s+(?P<y1>\d{4})"
    rf"|(?P<m2>{_MONTH_RE})\.?\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y2>\d{{4}})"
)
_GAP: Final[str] = r"[\s:\-–]{0,4}(?:(?:el|on|before|until|the)\s+)?"


def _marker_re(markers: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{markers})\b{_GAP}(?:{_DATE_BODY})")


_DEADLINE_ES_RE: Final = _marker_re(_DEADLINE_ES_CA)
_DEADLINE_EN_RE: Final = _marker_re(_DEADLINE_EN)
_POSTED_ES_RE: Final = _marker_re(_POSTED_ES_CA)
_POSTED_EN_RE: Final = _marker_re(_POSTED_EN)

_RELATIVE: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bpublicad[ao]\s+hace\s+(\d{1,3})\s+(dias?|semanas?)\b(?!\s*\+)"),
    re.compile(r"(?<![\w+])(?<!re)\bposted\s+(\d{1,3})\s+(days?|weeks?)\s+ago\b"),
    re.compile(r"\bpublicad[ao]\s+fa\s+(\d{1,3})\s+(dies|dia|setmanes?)\b"),
)


@dataclass(frozen=True)
class OfferDates:
    """ISO ``YYYY-MM-DD`` strings, each ``None`` when not reliably known."""

    publicacion: str | None = None
    limite: str | None = None


def _fold(text: str) -> str:
    """Lowercase + strip accents keeping the length (indices stay valid)."""
    out: list[str] = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[0]
        lowered = base.lower()
        out.append(lowered if len(lowered) == 1 else base)
    return "".join(out)


def _make_date(year: int, month: int, day: int) -> date | None:
    if year < _MIN_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_iso_date(value: object) -> date | None:
    """Date part of an ISO date/datetime string (``2026-10-01T09:00:00Z``)."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?\s*", value)
    if match is None:
        return None
    return _make_date(int(match[1]), int(match[2]), int(match[3]))


def _from_match(m: re.Match[str], *, day_first: bool) -> date | None:
    if m["iso"]:
        return _make_date(int(m["iso"]), int(m["iso_m"]), int(m["iso_d"]))
    if m["a"]:
        first, second, year = int(m["a"]), int(m["b"]), int(m["year"])
        if first > 12:
            return _make_date(year, second, first)
        if second > 12:
            return _make_date(year, first, second)
        # Both could be the month: day-first only under an ES/CA marker.
        return _make_date(year, second, first) if day_first else None
    if m["d1"]:
        return _make_date(int(m["y1"]), _MONTHS[m["m1"]], int(m["d1"]))
    return _make_date(int(m["y2"]), _MONTHS[m["m2"]], int(m["d2"]))


def _first_date(
    folded: str,
    es_ca: re.Pattern[str],
    en: re.Pattern[str],
    accept: Callable[[date], bool] = lambda _d: True,
) -> date | None:
    """Earliest-positioned date that ``accept`` allows (typo years are skipped)."""
    found: list[tuple[int, date]] = []
    for pattern, day_first in ((es_ca, True), (en, False)):
        for m in pattern.finditer(folded):
            parsed = _from_match(m, day_first=day_first)
            if parsed is not None and accept(parsed):
                found.append((m.start(), parsed))
    return min(found)[1] if found else None


def _relative_posting(folded: str, today: date) -> date | None:
    for pattern in _RELATIVE:
        m = pattern.search(folded)
        if m is None:
            continue
        amount = int(m[1])
        days = amount * 7 if m[2].startswith(("semana", "week", "setmana")) else amount
        return today - timedelta(days=days)
    return None


def extract_dates(
    text: str,
    today: date,
    *,
    posted: object = None,
    valid_through: object = None,
) -> OfferDates:
    """Posting date and deadline for an offer; ``today`` is the receipt date.

    Structured values (JSON-LD ``datePosted``/``validThrough``) win over text.
    """
    folded = _fold(text)
    publicacion = parse_iso_date(posted) or _first_date(
        folded, _POSTED_ES_RE, _POSTED_EN_RE, lambda d: d <= today
    )
    if publicacion is None:
        publicacion = _relative_posting(folded, today)
    if publicacion is not None and publicacion > today:
        publicacion = None
    horizon = today.year + _MAX_DEADLINE_YEARS_AHEAD
    limite = parse_iso_date(valid_through) or _first_date(
        folded, _DEADLINE_ES_RE, _DEADLINE_EN_RE, lambda d: d.year <= horizon
    )
    if limite is not None and limite.year > horizon:
        limite = None
    return OfferDates(
        publicacion=publicacion.isoformat() if publicacion else None,
        limite=limite.isoformat() if limite else None,
    )
