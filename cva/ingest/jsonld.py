"""JSON-LD ``JobPosting`` extraction from an HTML body (best-effort, never raises).

Preferred parse path for link ingest: a 200 body carrying an
``application/ld+json`` JobPosting block yields title/company/text. Returns
``None`` when no parseable JobPosting block exists — absence is not an error
(the caller falls back to the HTML-fragment path or the wall classifier has
already flagged the body).
"""

import html
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from cva.ingest.posting_guard import strip_hidden_html
from cva.wall import ldjson_blocks

_TAG_RE = re.compile(r"<[^>]+>")
_HORIZONTAL_WS_RE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class JobPostingData:
    """Parsed JobPosting fields.

    ``text`` is non-empty unless the whole description was hidden markup: then
    it is ``""`` and ``hidden_html`` carries the guard report (callers must not
    treat that as a usable offer).
    """

    title: str | None
    company: str | None
    text: str
    # Raw JSON-LD ``datePosted`` / ``validThrough`` (ISO strings, unvalidated).
    date_posted: str | None = None
    valid_through: str | None = None
    # Posting guard: one short excerpt per hidden HTML block dropped from the
    # description (see cva.ingest.posting_guard).
    hidden_html: tuple[str, ...] = ()


def strip_html(markup: str) -> str:
    """Convert description markup to plain text (tags → space, entities decoded)."""
    without_tags = _TAG_RE.sub(" ", markup)
    decoded = html.unescape(without_tags)
    collapsed = _HORIZONTAL_WS_RE.sub(" ", decoded)
    return collapsed.strip()


def parse_jobposting(body: str) -> JobPostingData | None:
    """Return the first JobPosting node with visible text, or ``None``.

    Never raises: malformed JSON blocks are skipped, missing optional fields
    stay ``None``; a missing/empty description yields no result. A node whose
    description is entirely hidden never stops the scan (a decoy must not
    shadow a later real node): it is returned, with ``text == ""`` and its
    guard report, only when no node yields visible text. Callers must test
    ``data.text``, not just truthiness of the result.
    """
    hidden_only: JobPostingData | None = None
    for block in ldjson_blocks(body):
        if '"JobPosting"' not in block:
            continue
        try:
            data: object = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _jobposting_nodes(data):
            found = _node_data(node)
            if found is None:
                continue
            if found.text:
                return found
            hidden_only = hidden_only or found
    return hidden_only


def _node_data(node: dict[str, object]) -> JobPostingData | None:
    """Fields of one node; ``None`` when it has neither text nor hidden blocks."""
    organization = node.get("hiringOrganization")
    description = node.get("description")
    visible, excerpts = (
        strip_hidden_html(description) if isinstance(description, str) else ("", [])
    )
    text = strip_html(visible)
    if not text and not excerpts:
        return None
    return JobPostingData(
        title=_optional_text(node.get("title")),
        company=(
            _optional_text(organization.get("name"))
            if isinstance(organization, dict)
            else None
        ),
        text=text,
        date_posted=_optional_text(node.get("datePosted")),
        valid_through=_optional_text(node.get("validThrough")),
        hidden_html=tuple(excerpts),
    )


def _jobposting_nodes(data: object) -> Iterator[dict[str, object]]:
    """Yield every JobPosting node (lists and ``@graph`` flattened), in order."""
    if isinstance(data, list):
        for item in data:
            yield from _jobposting_nodes(item)
        return
    if not isinstance(data, dict):
        return
    node_type: object = data.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(candidate == "JobPosting" for candidate in types):
        yield data
        return
    graph = data.get("@graph")
    if graph is not None:
        yield from _jobposting_nodes(graph)


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
