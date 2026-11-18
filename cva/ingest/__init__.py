"""Offer ingest: guest fetch (JSON-LD or HTML-fragment parse) and paste path.

Public surface (design Interfaces): ``FetchedOffer``, ``fetch_offer``,
``paste_offer``, plus the multi-source URL registry (infojobs-source D1):
``Source``, ``source_from_url``, ``job_id_from_url`` (now multi-source —
one seam, no caller churn) and ``canonical_url``. Wall degradation:
``fetch_offer`` raises ``WallError`` on any non-OK verdict except NOT_FOUND,
which surfaces as ``NotFoundError`` (design D3).
"""

from cva.ingest.guest import (
    FetchedOffer,
    NotFoundError,
    OfferParseError,
    fetch_offer,
    paste_offer,
)
from cva.ingest.jsonld import JobPostingData, parse_jobposting
from cva.ingest.sources import (
    Source,
    canonical_url,
    job_id_from_url,
    source_from_url,
)
from cva.wall import WallError

__all__ = [
    "FetchedOffer",
    "JobPostingData",
    "NotFoundError",
    "OfferParseError",
    "Source",
    "WallError",
    "canonical_url",
    "fetch_offer",
    "job_id_from_url",
    "parse_jobposting",
    "paste_offer",
    "source_from_url",
]
