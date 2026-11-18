"""Published-salary extractor (salary-estimate T1): pure, precision over recall.

``extract_published_salary(text)`` returns the FIRST salary stated inside the
offer description as EUR gross per year, or ``None``. Supported shapes (ES/EN/CA):
``28.000 - 34.000 € brutos/año``, ``entre 28.000 y 34.000 euros``,
``€45K - €55K``, ``45k-55k``, ``30.000€``, ``55K €/yr - 75K €/yr``.

Rules:
- Pasted LinkedIn pages end with a "Más empleos"/similar-jobs block holding
  OTHER offers' salaries: everything from that heading on is ignored.
- A bare number is never a salary: it needs a euro marker, or (ranges only)
  ``k`` on both bounds. ``$``/``£`` amounts, net (``neto``) pay, hourly rates,
  bonuses, funding rounds and headcount-like ``10k usuarios`` are rejected.
- Monthly figures become annual with x12, or x14 only when the text says
  "14 pagas" explicitly; ``PublishedSalary.note`` always states which.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

Period = Literal["annual", "monthly"]

_MIN_ANNUAL: Final[int] = 12_000
_MAX_ANNUAL: Final[int] = 400_000
_MAX_RANGE_RATIO: Final[float] = 4.0

# Heading of the similar-jobs block in pasted LinkedIn pages (line start).
_SIMILAR_JOBS_RE: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(?:más empleos|mas empleos|empleos similares|ofertas similares|"
    r"ofertas de empleo similares|otras ofertas|similar jobs|more jobs|"
    r"jobs you may be interested in|empleos que te pueden interesar|"
    r"explora empleos|ofertes similars|més ofertes)\b"
)

_NUM: Final[str] = r"(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<cb>€\s?)?(?P<num>{_NUM})(?P<k>\s?[kK](?![A-Za-z]))?"
    r"(?P<ca>\s?(?:€|EUR\b|eur\b|euros?\b))?"
)
_CONNECTOR_RE: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:/\s*(?:yr|year|año|anual|any|mes|month)\w*)?"
    r"(?:\s*(?:brutos?|bruts?|gross))?\s*(?:[-–—]|\s(?:a|y|i|to|and|hasta)\s)\s*",
    re.IGNORECASE,
)
_FOREIGN_CUR_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:USD|GBP|CHF|\$|£|dólares|dollars)", re.IGNORECASE
)
_HOURLY_RE: Final[re.Pattern[str]] = re.compile(
    r"/\s*h\b|/\s*hora|por hora|/\s*hour|per hour|hourly|a l'hora", re.IGNORECASE
)
_MONTHLY_RE: Final[re.Pattern[str]] = re.compile(
    r"/\s*mes\b|al mes|por mes|mensual|per month|/\s*month|monthly|/\s*mo\b|"
    r"al més|/\s*mensual",
    re.IGNORECASE,
)
_ANNUAL_RE: Final[re.Pattern[str]] = re.compile(
    r"año|anual|year|yr\b|annual|p\.\s?a\.|\bany\b|/\s*a\b", re.IGNORECASE
)
# "(?<!\.)" keeps the ".NET" stack name from reading as net pay.
_NET_RE: Final[re.Pattern[str]] = re.compile(r"\bnet[oa]s?\b|(?<!\.)\bnet\b", re.IGNORECASE)
_NEGATIVE_BEFORE_RE: Final[re.Pattern[str]] = re.compile(
    r"bonus|bono\b|signing|ronda|funding|facturaci|revenue|inversi|capital|"
    r"millones|million|raised|\barr\b",
    re.IGNORECASE,
)
_NON_SALARY_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:\+\s*)?(?:users?|usuarios?|requests?|rows?|filas|clientes|customers|"
    r"employees|empleados|downloads|calls|tokens|documents?|docs|records|events|"
    r"messages|mensajes|req\b|people|personas|stars)",
    re.IGNORECASE,
)
_PAGAS_14_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:14|catorce)\s*(?:pagas|payments|pays|pagues)", re.IGNORECASE
)


@dataclass(frozen=True)
class PublishedSalary:
    """A salary stated in the offer text, normalised to EUR gross per year."""

    min_eur: int
    max_eur: int
    period: Period  # period the offer used (monthly figures are annualised)
    snippet: str  # raw text the figures came from
    note: str = ""  # e.g. "mensual x12" when annualised


@dataclass(frozen=True)
class _Amount:
    start: int
    end: int
    value: float
    has_k: bool
    has_eur: bool


def _parse_number(raw: str, has_k: bool) -> float | None:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _amounts(text: str) -> list[_Amount]:
    found: list[_Amount] = []
    for match in _TOKEN_RE.finditer(text):
        has_k = match.group("k") is not None
        value = _parse_number(match.group("num"), has_k)
        if value is None:
            continue
        if has_k:
            value *= 1000
        has_eur = match.group("cb") is not None or match.group("ca") is not None
        found.append(_Amount(match.start(), match.end(), value, has_k, has_eur))
    return found


def _period(text: str, first: _Amount, last: _Amount) -> Period | Literal["hourly"] | None:
    """Period named around the figures (nearest mention after, then before)."""
    after = text[last.end : last.end + 30]
    before = text[max(0, first.start - 30) : first.start]
    if _HOURLY_RE.search(after) or _HOURLY_RE.search(before):
        return "hourly"
    for window in (after, before):
        monthly = _MONTHLY_RE.search(window)
        annual = _ANNUAL_RE.search(window)
        if monthly and (annual is None or monthly.start() <= annual.start()):
            return "monthly"
        if annual:
            return "annual"
    return None


def _candidate(text: str, bounds: list[_Amount]) -> PublishedSalary | None:
    first, last = bounds[0], bounds[-1]
    if any(_FOREIGN_CUR_RE.match(text[a.end :]) for a in bounds):
        return None
    if text[: first.start].rstrip().endswith(("$", "£")):
        return None
    before = text[max(0, first.start - 40) : first.start]
    if _NEGATIVE_BEFORE_RE.search(before):
        return None
    near = text[max(0, first.start - 25) : last.end + 25]
    if _NET_RE.search(near):
        return None
    if _NON_SALARY_UNIT_RE.match(text[last.end :]):
        return None
    has_eur = any(a.has_eur for a in bounds)
    both_k = len(bounds) == 2 and all(a.has_k for a in bounds)
    if not (has_eur or both_k):
        return None
    period = _period(text, first, last)
    if period == "hourly":
        return None
    values = [a.value for a in bounds]
    note = ""
    if period == "monthly":
        factor = 14 if _PAGAS_14_RE.search(text) else 12
        values = [v * factor for v in values]
        note = f"mensual x{factor}"
    elif period is None and any(v < _MIN_ANNUAL for v in values):
        return None  # ambiguous small figure with no stated period
    low, high = min(values), max(values)
    if low < _MIN_ANNUAL or high > _MAX_ANNUAL or high / low > _MAX_RANGE_RATIO:
        return None
    line_end = text.find("\n", last.end)
    tail_end = min(last.end + 20, line_end if line_end != -1 else len(text))
    snippet = " ".join(text[first.start : tail_end].split())
    return PublishedSalary(
        min_eur=round(low),
        max_eur=round(high),
        period="monthly" if period == "monthly" else "annual",
        snippet=snippet,
        note=note,
    )


def extract_published_salary(text: str | None) -> PublishedSalary | None:
    """First plausible salary in the offer description, or ``None``."""
    if not text:
        return None
    cut = _SIMILAR_JOBS_RE.search(text)
    if cut is not None and cut.start() > 0:
        text = text[: cut.start()]
    amounts = _amounts(text)
    index = 0
    while index < len(amounts):
        current = amounts[index]
        nxt = amounts[index + 1] if index + 1 < len(amounts) else None
        if nxt is not None and _CONNECTOR_RE.fullmatch(text[current.end : nxt.start]):
            found = _candidate(text, [current, nxt])
            if found is not None:
                return found
            index += 2
            continue
        if current.has_eur:
            found = _candidate(text, [current])
            if found is not None:
                return found
        index += 1
    return None
