"""Pure parser for LinkedIn guest search-result fragments (tasks 8b.1–8b.2).

SPIKE GATE (docs/spike_guest_evidence.md): the live guest search endpoint is
WALLED (404 + ``<title>LinkedIn</title>`` JS shell). This parser therefore runs
against SAVED HTML FIXTURES shaped like the real guest search results (``<li>``
job cards carrying ``data-entity-urn="urn:li:jobPosting:{id}"``, ``<h3>`` title,
``<h4>`` company, ``<time>`` age). It is tolerant by contract: any body without
job-card urns — walled shells, empty pages, malformed markup — parses to an
empty list, never an error. No I/O, no network, no dependencies beyond stdlib.
"""

import html
import re

from pydantic import BaseModel

_URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL | re.IGNORECASE)
_COMPANY_RE = re.compile(r"<h4[^>]*>(.*?)</h4>", re.DOTALL | re.IGNORECASE)
_AGE_RE = re.compile(r"<time[^>]*>(.*?)</time>", re.DOTALL | re.IGNORECASE)


class JobCard(BaseModel):
    """One visible job card extracted from a search fragment."""

    job_id: str
    title: str | None = None
    company: str | None = None
    age: str | None = None


def _first_match(chunk: str, pattern: re.Pattern[str]) -> str | None:
    found = pattern.search(chunk)
    if found is None:
        return None
    cleaned = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", found.group(1))).split())
    return cleaned or None


def parse_search_fragment(body: str) -> list[JobCard]:
    """Extract visible job cards from a search fragment; zero urns → ``[]``.

    Pure and total: walled shells, empty result pages, and malformed markup all
    yield an empty list (the slice-1 gate makes live zero-ids an acceptable
    outcome; the value path is paste ingest).
    """
    urns = list(_URN_RE.finditer(body))
    cards: list[JobCard] = []
    for index, urn in enumerate(urns):
        chunk_end = urns[index + 1].start() if index + 1 < len(urns) else len(body)
        chunk = body[urn.end() : chunk_end]
        cards.append(
            JobCard(
                job_id=urn.group(1),
                title=_first_match(chunk, _TITLE_RE),
                company=_first_match(chunk, _COMPANY_RE),
                age=_first_match(chunk, _AGE_RE),
            )
        )
    return cards
