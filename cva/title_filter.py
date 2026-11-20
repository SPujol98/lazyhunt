"""Python port of the title/company pre-filter in ``extension/filter.js``
(feature: rejudge-backlog, ``odd/tasks/rejudge-backlog.md``).

Ports ``extension/filter.js``'s ``normalize``/``matchesTerm``/``passesFilter``
and its ``DEFAULT_TITLE_FILTER`` constant — this module exists so
``cli.py rejudge --title-filter`` can apply the SAME include/exclude
semantics the LinkedIn beacon extension already uses, without a Node
dependency at judge time. ``tests/test_title_filter.py`` checks the two
implementations agree on a REPRESENTATIVE title/company bank (not an
exhaustive proof of equivalence) by running both the node harness
(``extension/filter.js``) and this port over the same inputs (skipped when
``node`` is not on PATH, like the existing ``tests/test_extension_filter.py``).

Whole-word semantics (ported verbatim from filter.js's own comment): after
:func:`normalize`, text and terms are sequences of alphanumeric tokens
separated by single spaces. A term matches only when it appears as one or
more WHOLE tokens bounded by the start/end of the string or another space —
never as a substring inside a longer token ("AI" does not match "maintain").
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Final

IncludeGroups = list[list[str]]

_NON_ALNUM: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


def normalize(text: str | None) -> str:
    """Lowercase, strip diacritics, turn every non-alnum run into one space."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _NON_ALNUM.sub(" ", without_marks).strip()


def matches_term(normalized_text: str, term: str) -> bool:
    """Whole-word/phrase match of ``term`` inside an already-normalized text."""
    norm_term = normalize(term)
    if not norm_term:
        return False
    pattern = re.compile(rf"(?:^|\s){re.escape(norm_term)}(?:$|\s)")
    return pattern.search(normalized_text) is not None


@dataclass(frozen=True)
class TitleFilter:
    """Same shape as filter.js's plain object: enabled + include/exclude lists."""

    enabled: bool = True
    include_groups: IncludeGroups = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)


def passes_filter(title: str | None, company: str | None, filter_: TitleFilter) -> bool:
    """True when ``(title, company)`` passes ``filter_`` (disabled -> always True).

    Include groups are ANDed; terms inside one group are ORed and checked
    against the TITLE only. Exclude terms are checked against TITLE and
    COMPANY. Mirrors ``passesFilter`` in ``extension/filter.js``.
    """
    if not filter_.enabled:
        return True
    norm_title = normalize(title)
    norm_company = normalize(company)
    groups = [group for group in filter_.include_groups if group]
    for group in groups:
        if not any(matches_term(norm_title, term) for term in group):
            return False
    for term in filter_.exclude_terms:
        if matches_term(norm_title, term) or matches_term(norm_company, term):
            return False
    return True


# Verbatim copy of extension/filter.js's DEFAULT_TITLE_FILTER (odd/tasks/
# beacon-title-filter.md) — kept in sync by the node-vs-python parity test
# rather than by importing the JS file (no JS runtime in production).
DEFAULT_TITLE_FILTER: Final[TitleFilter] = TitleFilter(
    enabled=True,
    include_groups=[
        [
            "AI",
            "IA",
            "Artificial Intelligence",
            "Inteligencia Artificial",
            "GenAI",
            "Generative AI",
            "LLM",
            "LLMOps",
            "MLOps",
            "RAG",
            "Agentic",
            "AI Agent",
            "Machine Learning",
            "NLP",
            "Prompt",
        ],
        [
            "Engineer",
            "Developer",
            "Ingeniero",
            "Desarrollador",
            "Programador",
            "Scientist",
            "Científico",
        ],
    ],
    exclude_terms=[
        "Full Stack",
        "Freelance",
        "Java",
        "JavaScript",
        "TypeScript",
        "Senior",
        "Lead",
        "Staff",
        "Principal",
        "Architect",
        "Arquitecto",
        "Bending Spoons",
        "Crossing Hurdles",
        "Joinrs",
        "Evolve",
        "Hire Feed",
        "Quik Hire Staffing",
        "micro1",
        "BJAK",
    ],
)


# Verbatim copy of extension/filter.js's BACKEND_TITLE_FILTER (the `backend`
# profile, odd/tasks/backend-profile.md); same parity rule as the default above.
BACKEND_TITLE_FILTER: Final[TitleFilter] = TitleFilter(
    enabled=True,
    include_groups=[
        [
            "Backend",
            "Back-end",
            "Back End",
            "Software",
            "Python",
            "Django",
            "FastAPI",
            "Developer",
            "Engineer",
            "Desarrollador",
            "Programador",
            "Ingeniero",
            "Desenvolupador",
            "Enginyer",
        ],
    ],
    exclude_terms=[
        "Senior",
        "Sr",
        "Lead",
        "Staff",
        "Principal",
        "Architect",
        "Arquitecto",
        "Head",
        "Manager",
        "Director",
        "Java Developer",
        "Java Engineer",
        "Desarrollador Java",
        ".NET",
        "C#",
        "PHP",
        "Frontend",
        "Front-end",
        "Front End",
        "React",
        "Angular",
        "Vue",
        "iOS",
        "Android",
        "Mobile",
        "Salesforce",
        "SAP",
        "Golang",
        "Ruby",
        "Scala",
        "Bending Spoons",
        "Crossing Hurdles",
        "Joinrs",
        "Evolve",
        "Hire Feed",
        "Quik Hire Staffing",
        "micro1",
        "BJAK",
        "Freelance",
    ],
)

# The company blocklist both filters above end with (backend: before
# "Freelance"); a profile's ``company_blocklist`` replaces exactly these terms.
DEFAULT_COMPANY_BLOCKLIST: Final[tuple[str, ...]] = (
    "Bending Spoons",
    "Crossing Hurdles",
    "Joinrs",
    "Evolve",
    "Hire Feed",
    "Quik Hire Staffing",
    "micro1",
    "BJAK",
)


def with_company_blocklist(
    filter_: TitleFilter, blocklist: tuple[str, ...]
) -> TitleFilter:
    """``filter_`` with the default company blocklist swapped for ``blocklist``.

    The new terms go where the first default term was, so a filter keeps its
    shape; an unchanged blocklist returns ``filter_`` itself.
    """
    if blocklist == DEFAULT_COMPANY_BLOCKLIST:
        return filter_
    terms: list[str] = []
    placed = False
    for term in filter_.exclude_terms:
        if term in DEFAULT_COMPANY_BLOCKLIST:
            if not placed:
                terms.extend(t for t in blocklist if t not in terms)
                placed = True
            continue
        terms.append(term)
    if not placed:
        terms.extend(blocklist)
    return TitleFilter(
        enabled=filter_.enabled,
        include_groups=[list(g) for g in filter_.include_groups],
        exclude_terms=terms,
    )
