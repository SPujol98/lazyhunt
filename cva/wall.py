"""Wall classification for beacon-site HTTP responses (design D1–D3, D6).

Every response (offer ingest or beacon search) passes through pure
:func:`classify` before use — walls are the normal case, not the exception
(spike evidence: guest search serves 404 + JS shell). No I/O, no retries,
ever; callers own backoff/pause policy.

Shell-marker heuristics are PER-SOURCE (infojobs-source D6) — a body is a JS
shell when ALL markers of its source hold:

LinkedIn (default, unchanged):

1. ``<title>LinkedIn</title>`` exact title present,
2. zero ``data-entity-urn`` attributes,
3. zero ``application/ld+json`` script blocks containing ``"JobPosting"``.

InfoJobs (``source="infojobs"``):

1. ``<title>InfoJobs</title>`` exact title present,
2. zero JobPosting JSON-LD blocks,
3. zero ``ij-Template``/``ij-Box`` content tokens.

Priority order (first match wins, unchanged for every source):

1. status 999 or 429 → ``RATE_LIMITED``
2. status 3xx, or ``authwall``/``login`` in the final URL → ``LOGIN_WALL``
3. status 404 → ``JS_SHELL`` with shell markers, else ``NOT_FOUND``
4. status 5xx or < 200 (status 0 encodes a transport failure) → ``TRANSIENT``
5. shell markers on any other status (e.g. 200) → ``JS_SHELL``
6. otherwise → ``OK`` (parseable content)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cva.ingest.sources import Source

_SHELL_TITLE = "<title>LinkedIn</title>"
_ENTITY_URN_MARKER = "data-entity-urn"
_JOBPOSTING_TOKEN = '"JobPosting"'

_INFOJOBS_SHELL_TITLE = "<title>InfoJobs</title>"
_INFOJOBS_CONTENT_TOKENS = ("ij-Template", "ij-Box")

_LDJSON_BLOCK_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


class WallKind(StrEnum):
    """Exactly one verdict kind per response (offer-ingest spec)."""

    OK = "ok"
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    LOGIN_WALL = "login_wall"
    JS_SHELL = "js_shell"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class WallVerdict:
    """Classification outcome: the kind plus human-readable evidence."""

    kind: WallKind
    detail: str


class WallError(RuntimeError):
    """Ingest degradation (design D3): raised for any non-OK verdict except NOT_FOUND."""

    def __init__(self, verdict: WallVerdict) -> None:
        super().__init__(f"{verdict.kind.value}: {verdict.detail}")
        self.verdict = verdict


def ldjson_blocks(body: str) -> list[str]:
    """Return every ``application/ld+json`` script block content in ``body``."""
    return _LDJSON_BLOCK_RE.findall(body)


def has_jobposting_jsonld(body: str) -> bool:
    """True when any ld+json block in ``body`` mentions ``"JobPosting"``."""
    return any(_JOBPOSTING_TOKEN in block for block in ldjson_blocks(body))


def _is_shell(body: str, *, source: Source = "linkedin") -> bool:
    if source == "infojobs":
        # D6: shell ⇔ InfoJobs title AND zero JSON-LD AND zero ij- tokens.
        if _INFOJOBS_SHELL_TITLE not in body:
            return False
        if has_jobposting_jsonld(body):
            return False
        return not any(token in body for token in _INFOJOBS_CONTENT_TOKENS)
    if _SHELL_TITLE not in body:
        return False
    if body.count(_ENTITY_URN_MARKER) > 0:
        return False
    return not has_jobposting_jsonld(body)


def classify(
    status: int, body: str, final_url: str, *, source: Source = "linkedin"
) -> WallVerdict:
    """Classify one HTTP response into exactly one :class:`WallVerdict` (pure).

    ``source`` selects the shell-marker set (D6); the status priority order is
    identical for every source.
    """
    if status in (429, 999):
        return WallVerdict(WallKind.RATE_LIMITED, f"HTTP {status} rate-limit status")
    lowered = final_url.lower()
    if 300 <= status < 400 or "authwall" in lowered or "login" in lowered:
        return WallVerdict(WallKind.LOGIN_WALL, f"HTTP {status} final_url={final_url}")
    shell = _is_shell(body, source=source)
    if status == 404:
        if shell:
            return WallVerdict(
                WallKind.JS_SHELL, f"HTTP 404 with {source} shell markers"
            )
        return WallVerdict(
            WallKind.NOT_FOUND, f"HTTP 404 without shell markers ({final_url})"
        )
    if 500 <= status < 600 or status < 200:
        return WallVerdict(
            WallKind.TRANSIENT, f"HTTP {status} transport/server failure"
        )
    if shell:
        return WallVerdict(
            WallKind.JS_SHELL, f"HTTP {status} with {source} shell markers"
        )
    return WallVerdict(WallKind.OK, f"HTTP {status} parseable content")
