"""On-demand salary web research (salary-estimate T3): schema, prompt, validation.

The LLM provider runs a web-search completion (explicit owner button press
only); this module defines what it must return and decides whether the answer
is reliable enough to show. Anything unsourced, non-EUR or implausible becomes
``no_data`` -- an estimate is never fabricated.
"""

from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel

_MIN_ANNUAL: Final[int] = 12_000
_MAX_ANNUAL: Final[int] = 400_000

SYSTEM_PROMPT: Final[str] = (
    "You research salary ranges for a job seeker in Spain using web search. "
    "Find GROSS ANNUAL salary figures in EUR for the given company/position "
    "(or, failing company data, for the same role, level and city) from "
    "reliable published sources: salary surveys, public salary reports, job "
    "boards with disclosed ranges, official statistics. Do not scrape or "
    "quote Glassdoor pages. Never invent a figure or a source: only cite "
    "pages you actually consulted, with their real URL. For each source give "
    "publisher, url, the date of the data (YYYY or YYYY-MM) and the sample "
    "size n when the source states it (else null). "
    "Answer with JSON only: {min, max, median (or null), currency, period "
    '("annual" or "monthly"), sources: [{publisher, url, date, n}], '
    "confidence (high|medium|low|none), notes}. If you cannot find at least "
    'one reliable source, return min/max null, sources [] and confidence "none".'
)


class SalarySource(BaseModel):
    publisher: str
    url: str
    date: str | None = None
    n: int | None = None


class SalaryResearch(BaseModel):
    """Structured answer required from the provider."""

    min: int | None = None
    max: int | None = None
    median: int | None = None
    currency: str = "EUR"
    period: Literal["annual", "monthly"] = "annual"
    sources: list[SalarySource] = []
    confidence: Literal["high", "medium", "low", "none"] = "none"
    notes: str = ""


@dataclass(frozen=True)
class SalaryOutcome:
    """What a research press produced (or the cached result of an earlier one)."""

    status: Literal["ok", "no_data", "unavailable"]
    research: SalaryResearch | None = None
    cached: bool = False
    checked_at: str = ""
    company: str = ""
    position: str = ""
    city: str = ""
    level: str = ""


def build_research_prompt(
    *, company: str, position: str, city: str, level: str
) -> tuple[str, str]:
    """(system, user) prompt pair for the web-research completion."""
    user = (
        f"Company: {company}\n"
        f"Position: {position}\n"
        f"City: {city}\n"
        f"Level: {level}\n"
        "Return the gross annual salary range in EUR for this role in Spain."
    )
    return SYSTEM_PROMPT, user


def to_outcome(parsed: SalaryResearch) -> SalaryOutcome:
    """Validate a provider answer: reliable -> ``ok`` (annualised), else ``no_data``."""
    sources = [
        s for s in parsed.sources if s.url.lower().startswith(("http://", "https://"))
    ]
    if (
        parsed.confidence == "none"
        or parsed.currency.upper() != "EUR"
        or parsed.min is None
        or parsed.max is None
        or not sources
    ):
        return SalaryOutcome(status="no_data")
    factor = 12 if parsed.period == "monthly" else 1
    low, high = parsed.min * factor, parsed.max * factor
    median = parsed.median * factor if parsed.median is not None else None
    if low > high or low < _MIN_ANNUAL or high > _MAX_ANNUAL:
        return SalaryOutcome(status="no_data")
    research = parsed.model_copy(
        update={
            "min": low,
            "max": high,
            "median": median,
            "period": "annual",
            "sources": sources,
        }
    )
    return SalaryOutcome(status="ok", research=research)
