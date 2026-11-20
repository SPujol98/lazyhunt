""" "Search elsewhere" links for an offer card: pure URL builders, no I/O.

The analysis card ends with a row of URL buttons that search the same offer
(title + company) on other sites, so an offer seen on one portal can be
applied to elsewhere. Built from the stored tracker fields only:

- LinkedIn jobs search (skipped for LinkedIn offers);
- a Google search for ``"<title>" "<company>"`` plus a careers hint;
- a Spanish portal keyword search: InfoJobs, or Indeed for InfoJobs offers.

Terms are capped so a pathological title cannot produce an oversized URL.
"""

from typing import Final, NamedTuple
from urllib.parse import urlencode

from cva.ingest.sources import SOURCES, source_from_url

# Max characters kept from the title and from the company name.
_TERM_CAP: Final[int] = 120
_CAREERS_HINT: Final[str] = "(careers OR jobs OR empleo)"


class SearchLink(NamedTuple):
    """One "search elsewhere" button: the site's name and the search URL."""

    site: str
    url: str


def _term(value: str | None) -> str:
    return " ".join((value or "").split())[:_TERM_CAP].strip()


def _origin(source: str | None, url: str | None) -> str | None:
    """The offer's portal: a registered stored ``origen`` wins, else the URL's
    (``origen`` may also be ``link``/``paste``, which name no portal)."""
    if source and source.lower() in SOURCES:
        return source.lower()
    return source_from_url(url) if url else None


def search_links(
    title: str | None,
    company: str | None,
    *,
    source: str | None = None,
    url: str | None = None,
) -> list[SearchLink]:
    """Links searching the offer elsewhere; empty when title and company are."""
    title_term, company_term = _term(title), _term(company)
    if not title_term and not company_term:
        return []
    origin = _origin(source, url)
    keywords = " ".join(t for t in (title_term, company_term) if t)
    quoted = " ".join(f'"{t}"' for t in (title_term, company_term) if t)
    links: list[SearchLink] = []
    if origin != "linkedin":
        links.append(
            SearchLink(
                "LinkedIn",
                "https://www.linkedin.com/jobs/search/?"
                + urlencode({"keywords": keywords}),
            )
        )
    links.append(
        SearchLink(
            "Google",
            "https://www.google.com/search?"
            + urlencode({"q": f"{quoted} {_CAREERS_HINT}"}),
        )
    )
    if origin == "infojobs":
        links.append(
            SearchLink(
                "Indeed", "https://es.indeed.com/jobs?" + urlencode({"q": keywords})
            )
        )
    else:
        links.append(
            SearchLink(
                "InfoJobs",
                "https://www.infojobs.net/jobsearch/search-results/list.xhtml?"
                + urlencode({"keyword": keywords}),
            )
        )
    return links
