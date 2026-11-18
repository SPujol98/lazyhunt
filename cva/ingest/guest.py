"""Guest-endpoint offer ingest (design D3, task 5.5): ``fetch_offer`` + ``paste_offer``.

Link ingest prefers the JSON-LD JobPosting parse and falls back to a
best-effort HTML-fragment scrape. Spike evidence (docs/spike_guest_evidence.md)
proves the guest ``jobPosting/{id}`` endpoint serves a ~20 KB HTML fragment
with NO JSON-LD and no shell markers — the fragment path below scrapes
title/company/description conservatively:

- title: ``<h1>/<h2>`` whose class list contains ``top-card-layout__title``
  or ``topcard__title``;
- company: ``<span>/<a>`` whose class list contains ``topcard__flavor`` or
  ``top-card-layout__second-subtitle``;
- description: ``<div>`` whose class list contains
  ``show-more-less-html__markup``.

Extraction uses a stdlib ``html.parser.HTMLParser`` subclass with explicit
depth tracking (live-body evidence, 2026-09): the previous single-pass regex
skipped a deeply nested target element whenever a shallower match's span
ended at the target's own closing tag.

This is deliberately best-effort: fields may be ``None``, text is mandatory,
and absence never crashes. An OK verdict whose body yields no text raises
:class:`OfferParseError` (design is silent on this case; fail-fast beats an
empty offer).

Fetch strategy (live production evidence, owner probe 2026-09): job-view URLs
are fetched guest-endpoint-first — ``linkedin.com/jobs/view/{id}`` in any host
form serves a reCAPTCHA challenge shell, while
``/jobs-guest/jobs/api/jobPosting/{id}`` serves a clean parseable fragment.
One fallback GET to ``https://www.linkedin.com/jobs/view/{id}/`` covers a
walled or unparseable guest response (challenge shells classify OK today and
fail in the parse path — the fallback covers them without reclassification).
Hard cap: 2 GETs per offer. URLs without a job id (search shapes) keep the
single-fetch behavior. ``FetchedOffer.url`` keeps the ORIGINAL caller URL for
traceability and bridge dedupe; only the fetch target is normalized.
"""

import re
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, Field

from cva.fetch import Fetcher
from cva.ingest.jsonld import JobPostingData, parse_jobposting, strip_html
from cva.ingest.posting_guard import scan_hidden_html
from cva.ingest.sources import (
    Source,
    allows_server_fetch,
    is_inline_source,
    job_id_from_url,
    source_from_url,
)
from cva.wall import WallError, WallKind, WallVerdict, classify

_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")

_GUEST_JOBPOSTING_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
)
_WWW_JOB_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}/"

# (tag, class token) probes for the fragment path, in priority order.
_TITLE_PROBES: tuple[tuple[str, str], ...] = (
    ("h2", "top-card-layout__title"),
    ("h1", "top-card-layout__title"),
    ("h2", "topcard__title"),
)
_COMPANY_PROBES: tuple[tuple[str, str], ...] = (
    ("span", "topcard__flavor"),
    ("a", "topcard__flavor"),
    ("span", "top-card-layout__second-subtitle"),
)
_DESCRIPTION_PROBES: tuple[tuple[str, str], ...] = (
    ("div", "show-more-less-html__markup"),
)


class FetchedOffer(BaseModel):
    """Offer as ingested — the tracker's ``OfferInput`` mirrors this shape.

    ``source="bridge-inline"`` (infojobs-source D3) marks an offer built from
    offer text carried inline in the bridge payload (Path B): the server-side
    fetch is dead for InfoJobs, so the payload text IS the offer text.
    """

    job_id: str | None
    title: str | None
    company: str | None
    url: str | None
    text: str = Field(min_length=1)
    source: Literal["link", "paste", "bridge-inline"]
    # Raw JSON-LD dates when the page carried them (see cva.ingest.dates).
    date_posted: str | None = None
    valid_through: str | None = None
    # Posting guard: hidden HTML blocks the JSON-LD description carried.
    hidden_html: tuple[str, ...] = ()


class NotFoundError(LookupError):
    """NOT_FOUND verdict: a distinct not-found outcome, not a wall (spec wording)."""


class OfferParseError(RuntimeError):
    """OK verdict whose body yielded no offer text (no JSON-LD, no fragment fields)."""


async def fetch_offer(url: str, *, fetcher: Fetcher) -> FetchedOffer:
    """Fetch one offer through the wall classifier; raise WallError on non-OK.

    Per-source routing (infojobs-source D5): inline portals (infojobs.net and
    every portal registered after it) take ONE GET
    of the URL as-given (anti-ban invariant: one GET, no auth — the live body
    is normally the challenge shell, which classifies JS_SHELL per D6 and
    raises; the extension's inline text is the production ingest path).
    LinkedIn job-view URLs (any host form) keep the guest-endpoint-first flow
    with a single fallback GET to the www view page — never more than 2 HTTP
    attempts; URLs without a job id keep exactly one attempt. NOT_FOUND
    surfaces as :class:`NotFoundError`, every other non-OK verdict as
    :class:`WallError` carrying the verdict detail. The returned offer's
    ``url`` is the ORIGINAL ``url`` argument, not the normalized fetch target.
    """
    source = source_from_url(url)
    if not allows_server_fetch(source):
        # In-tab-only portal (owner decision): no HTTP call, ask for the text.
        raise WallError(
            WallVerdict(WallKind.JS_SHELL, f"{source} is read in the owner's tab only")
        )
    if source is not None and is_inline_source(source):
        return await _fetch_single(url, offer_url=url, fetcher=fetcher, source=source)
    job_id = job_id_from_url(url)
    if job_id is None:
        return await _fetch_single(url, offer_url=url, fetcher=fetcher)
    try:
        return await _fetch_single(
            _GUEST_JOBPOSTING_URL.format(job_id=job_id),
            offer_url=url,
            fetcher=fetcher,
        )
    except (NotFoundError, WallError, OfferParseError):
        return await _fetch_single(
            _WWW_JOB_VIEW_URL.format(job_id=job_id), offer_url=url, fetcher=fetcher
        )


async def _fetch_single(
    fetch_url: str,
    *,
    offer_url: str,
    fetcher: Fetcher,
    source: Source = "linkedin",
) -> FetchedOffer:
    """One classified GET; NOT_FOUND → NotFoundError, other walls → WallError."""
    response = await fetcher.get(fetch_url)
    verdict = classify(
        response.status, response.body, response.final_url, source=source
    )
    if verdict.kind is WallKind.OK:
        return _offer_from_body(
            url=offer_url, final_url=response.final_url, body=response.body
        )
    if verdict.kind is WallKind.NOT_FOUND:
        raise NotFoundError(f"offer not found at {fetch_url} ({verdict.detail})")
    raise WallError(verdict)


def paste_offer(text: str, url: str | None = None) -> FetchedOffer:
    """Accept pasted offer text verbatim (light normalization): paste always works."""
    normalized = _EXCESS_BLANK_LINES_RE.sub("\n\n", text.strip())
    if not normalized:
        raise ValueError("pasted offer text is empty")
    return FetchedOffer(
        job_id=None, title=None, company=None, url=url, text=normalized, source="paste"
    )


def parse_fragment(body: str) -> JobPostingData | None:
    """Best-effort scrape of a guest HTML fragment; ``None`` when no text found."""
    body = scan_hidden_html(body)[0]
    description = _scrape(body, _DESCRIPTION_PROBES)
    if description is None:
        return None
    return JobPostingData(
        title=_scrape(body, _TITLE_PROBES),
        company=_scrape(body, _COMPANY_PROBES),
        text=description,
    )


def _fragment_hidden(body: str) -> tuple[str, ...]:
    """Hidden blocks that emptied the fragment description (else empty)."""
    cleaned, hidden = scan_hidden_html(body)
    emptied = (
        bool(hidden)
        and _scrape(cleaned, _DESCRIPTION_PROBES) is None
        and _scrape(body, _DESCRIPTION_PROBES) is not None
    )
    return tuple(hidden) if emptied else ()


def _offer_from_body(*, url: str, final_url: str, body: str) -> FetchedOffer:
    data = parse_jobposting(body)
    if data is None or not data.text:
        # JSON-LD without visible text (absent, or all hidden): the fragment
        # may still carry the description; otherwise keep the hidden report so
        # the error below can name it.
        fragment = parse_fragment(body)
        if fragment is not None and fragment.text:
            data = fragment
    if data is None:
        hidden = _fragment_hidden(body)
        if hidden:
            data = JobPostingData(title=None, company=None, text="", hidden_html=hidden)
    if data is not None and not data.text and data.hidden_html:
        raise OfferParseError(
            f"the offer description at {url} is entirely hidden text "
            f"({len(data.hidden_html)} hidden block(s) dropped by the posting guard)"
        )
    if data is None or not data.text:
        raise OfferParseError(
            f"no parseable JobPosting JSON-LD or fragment description at {url}"
        )
    return FetchedOffer(
        job_id=job_id_from_url(final_url) or job_id_from_url(url),
        title=data.title,
        company=data.company,
        url=url,
        text=data.text,
        source="link",
        date_posted=data.date_posted,
        valid_through=data.valid_through,
        hidden_html=data.hidden_html,
    )


# The multi-source job-id registry in ``cva.ingest.sources`` owns the id
# shapes now (infojobs-source D1): this module imports ``job_id_from_url``
# from there and re-exports it for backwards compatibility.


# Elements that never take a closing tag: they must not unbalance the
# extractor's nesting depth (a ``<br>`` inside the target element is common in
# LinkedIn descriptions).
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _TokenTextExtractor(HTMLParser):
    """Collect the text of every ``<tag>`` whose ``class`` attribute contains
    ``class_token`` (whitespace-split), nesting-safely.

    A single-pass non-greedy regex cannot do this: its match span can END at a
    nested element's closing tag, so scanning resumes PAST a deeper target
    element's opening tag — the live LinkedIn production failure behind
    ``no parseable JobPosting JSON-LD or fragment description``. Here the
    target element is tracked by explicit depth: data accumulates until its
    own closing tag (depth back to zero) or EOF, so deep nesting and
    truncated/malformed HTML (unclosed tags) both still yield the collected
    text. Class attributes with embedded newlines or multiple tokens match via
    the whitespace split. Void and self-closing elements never unbalance the
    depth counter.
    """

    def __init__(self, tag: str, class_token: str) -> None:
        super().__init__(convert_charrefs=True)
        self._tag = tag
        self._class_token = class_token
        self._depth = 0
        self._chunks: list[str] = []
        self._captures: list[list[str]] = []

    @property
    def captures(self) -> list[str]:
        return ["".join(chunks) for chunks in self._captures]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._depth:
            if tag not in _VOID_TAGS:
                self._depth += 1
            return
        if tag != self._tag:
            return
        classes = next((value for name, value in attrs if name == "class"), None)
        if classes is not None and self._class_token in classes.split():
            self._depth = 1
            self._chunks = []
            self._captures.append(self._chunks)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Self-closing form (``<br/>``) never unbalances the depth counter."""

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in _VOID_TAGS:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._chunks.append(data)


def _scrape(body: str, probes: tuple[tuple[str, str], ...]) -> str | None:
    """First non-empty probe hit, via nesting-safe extraction; else ``None``."""
    for tag, class_token in probes:
        parser = _TokenTextExtractor(tag, class_token)
        parser.feed(body)
        parser.close()
        for raw in parser.captures:
            text = strip_html(raw)
            if text:
                return text
    return None
