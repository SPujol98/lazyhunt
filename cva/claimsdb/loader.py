"""Load ``claims.json`` into a typed :class:`ClaimsDB` (claims-db spec).

Fail-fast contract: a JSON array whose items violate the claim schema, carry
a duplicate id, or contain empty text raises :class:`ClaimsError` naming the
offending claim.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

ClaimCategory = Literal[
    "identity", "education", "experience", "project", "skill", "language", "soft"
]


class Claim(BaseModel):
    """One verified statement from ``ofertas_tracker.md`` §A."""

    id: str
    text: str
    category: ClaimCategory
    tags: list[str]


class ClaimsDB(BaseModel):
    """The compiled claims list plus its id index."""

    claims: list[Claim]
    by_id: dict[str, Claim]


class ClaimsError(ValueError):
    """Raised when ``claims.json`` violates the claims contract."""


def load_claims(path: Path) -> ClaimsDB:
    """Parse ``claims.json`` fail-fast; errors name the offending claim."""
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ClaimsError("claims.json must contain a JSON array of claims")
    claims: list[Claim] = []
    by_id: dict[str, Claim] = {}
    for index, item in enumerate(raw):
        claim = _parse_claim(item, index)
        if not claim.text.strip():
            raise ClaimsError(f"claim '{claim.id}': empty text")
        if claim.id in by_id:
            raise ClaimsError(f"duplicate claim id '{claim.id}'")
        claims.append(claim)
        by_id[claim.id] = claim
    return ClaimsDB(claims=claims, by_id=by_id)


def _parse_claim(item: object, index: int) -> Claim:
    try:
        return Claim.model_validate(item)
    except ValidationError as exc:
        raise ClaimsError(f"invalid claim {_claim_label(item, index)}: {exc}") from exc


def _claim_label(item: object, index: int) -> str:
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        return f"'{item['id']}'"
    return f"<index {index}>"
