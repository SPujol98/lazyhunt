"""Deterministic scorer over the curated synonym map (design D6/D7).

Requirement→claim mapping flows ONLY through claim tags and
``synonyms.json`` aliases — the LLM never maps requirements to claims
directly, so every claim id in the output exists in the claims DB by
construction. Weights: must_have 3, nice 1; credit ✓=1.0, ~=0.5, ✗=0;
``score = round(100·Σ(w_req·w_status)/Σw_req)``.
"""

import json
import unicodedata
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, Field, ValidationError

from cva.claimsdb import ClaimsDB
from cva.match.extraction import OfferExtraction, Requirement
from cva.match.gates import GateResult

Strength = Literal["strong", "weak"]
MatchStatus = Literal["✓", "~", "✗"]

_MUST_HAVE_WEIGHT: Final[float] = 3.0
_NICE_WEIGHT: Final[float] = 1.0
_CREDIT: Final[dict[str, float]] = {"✓": 1.0, "~": 0.5, "✗": 0.0}
_DEFAULT_THRESHOLD: Final[int] = 65

# Soft-framing wrappers around an otherwise-matchable requirement: an offer
# that asks for "interest in X", "knowledge of X" or "good X practices" is
# asking about the underlying skill X, which real demonstrated experience
# satisfies just as well as (or better than) mere interest/knowledge would.
# Stripped only as a fallback when the literal requirement name has no
# tag/synonym mapping of its own (never overrides a direct match).
_SOFT_PREFIXES: Final[tuple[str, ...]] = (
    "interest in ",
    "knowledge of ",
    "knowledge in ",
    "experience with ",
    "experience in ",
    "familiarity with ",
    "good ",
)
_SOFT_SUFFIXES: Final[tuple[str, ...]] = (
    " interest",
    " knowledge",
    " experience",
)


def _normalize(value: str) -> str:
    """Casefold, strip accents (NFD) and collapse whitespace — deterministic."""
    decomposed = unicodedata.normalize("NFD", value.casefold())
    letters = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(letters.split())


def alias_key(value: str) -> str:
    """``value`` normalized as the scorer looks up ``synonyms.json`` keys."""
    return _normalize(value)


def junk_alias(key: str) -> bool:
    """Whether ``key`` can never be a real ``synonyms.json`` alias.

    Blank, or holding a format/control character (zero-width space, tab,
    newline) or a letter outside the Latin script (a Cyrillic homoglyph in
    "sеniоr"). Curated keys with symbols ("c++", "node.js") are fine.
    """
    if not key.strip():
        return True
    return any(
        unicodedata.category(ch) in ("Cf", "Cc")
        or (ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN "))
        for ch in key
    )


def _strip_soft_framing(name: str) -> str | None:
    """Strip one soft-framing wrapper from an already-normalized ``name``.

    Returns ``None`` when no known wrapper applies, so the caller can tell
    "no framing to strip" apart from "framing stripped to an empty string".
    """
    for prefix in _SOFT_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :].strip()
    for suffix in _SOFT_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)].strip()
    return None


class SynonymEntry(BaseModel):
    """One ``synonyms.json`` value: target claim + curation strength."""

    claim_id: str
    strength: Strength


class SynonymMap(BaseModel):
    """Normalized alias → entry index (the only requirement→claim path)."""

    entries: dict[str, SynonymEntry]


class SynonymError(ValueError):
    """Raised when ``synonyms.json`` violates the curated-map contract."""


def load_synonyms(path: Path, db: ClaimsDB) -> SynonymMap:
    """Load the synonym map fail-fast.

    Rejects duplicate aliases (JSON duplicate keys), invalid strengths and
    any alias referencing a claim id absent from the claims DB — errors
    name the offending alias.
    """

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        seen: set[str] = set()
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise SynonymError(f"duplicate alias '{key}'")
            seen.add(key)
            result[key] = value
        return result

    raw: object = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
    )
    if not isinstance(raw, dict):
        raise SynonymError("synonyms.json must contain a JSON object")
    entries: dict[str, SynonymEntry] = {}
    for alias, spec in raw.items():
        try:
            entry = SynonymEntry.model_validate(spec)
        except ValidationError as exc:
            raise SynonymError(f"alias '{alias}': {exc}") from exc
        if entry.claim_id not in db.by_id:
            raise SynonymError(
                f"alias '{alias}' references unknown claim_id '{entry.claim_id}'"
            )
        entries[alias] = entry
    return SynonymMap(entries=entries)


class RequirementMatch(BaseModel):
    """One requirement scored against the claims DB."""

    requirement: Requirement
    claim_ids: list[str]
    status: MatchStatus


class MatchResult(BaseModel):
    """Weighted coverage result; ``threshold`` gates the Top-Choice badge."""

    score: int
    threshold: int
    breakdown: list[RequirementMatch]
    # backend-profile: short Spanish reason when the offer's core language is
    # not Python (None = not walled / not evaluated). Set by the service only.
    stack_wall: str | None = None
    # Pre-scoring gate outcomes (eligibility, language). Optional so a stored
    # breakdown_json written before the gates existed still loads.
    gates: list[GateResult] = Field(default_factory=list)

    @property
    def walled_reason(self) -> str | None:
        """Why the offer is hard-walled (stack wall or a gate FAIL), if it is."""
        if self.stack_wall:
            return self.stack_wall
        return next((g.reason for g in self.gates if g.verdict == "FAIL"), None)


def classify_keyword(keyword: str, db: ClaimsDB, syn: SynonymMap) -> MatchStatus:
    """§5 coverage status for one demand keyword, using the ``score`` rules.

    The keyword resolves exactly like a requirement name: exact claim tag or
    strong alias → ✓, weak alias only → ~, unmapped → ✗. Pure and
    deterministic; used by the service layer to stamp ``keyword_registry``
    status without ever touching ``synonyms.json`` (design D6).
    """
    cleaned = keyword.strip()
    if not cleaned:
        return "✗"
    probe = Requirement(
        name=cleaned, category="skill", must_have=False, evidence=cleaned
    )
    return _match_requirement(probe, _tag_index(db), syn).status


def _tag_index(db: ClaimsDB) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for claim in db.claims:
        for tag in claim.tags:
            index.setdefault(_normalize(tag), []).append(claim.id)
    return index


def _resolve(
    name: str, index: dict[str, list[str]], syn: SynonymMap
) -> tuple[list[str], MatchStatus]:
    tag_claims = index.get(name, [])
    entry = syn.entries.get(name)
    if tag_claims:
        # Exact claim tag (normalized): ✓, tags win over weak aliases.
        claim_ids = list(tag_claims)
        if (
            entry is not None
            and entry.strength == "strong"
            and entry.claim_id not in claim_ids
        ):
            claim_ids.append(entry.claim_id)
        return claim_ids, "✓"
    if entry is not None and entry.strength == "strong":
        return [entry.claim_id], "✓"
    if entry is not None:
        return [entry.claim_id], "~"
    return [], "✗"


def _match_requirement(
    requirement: Requirement,
    index: dict[str, list[str]],
    syn: SynonymMap,
) -> RequirementMatch:
    name = _normalize(requirement.name)
    claim_ids, status = _resolve(name, index, syn)
    if status == "✗":
        # Fallback only: an unmatched requirement may still be a real skill
        # wrapped in soft framing ("interest in X", "good X practices").
        core = _strip_soft_framing(name)
        if core:
            claim_ids, status = _resolve(core, index, syn)
    return RequirementMatch(requirement=requirement, claim_ids=claim_ids, status=status)


def score(
    ex: OfferExtraction,
    db: ClaimsDB,
    syn: SynonymMap,
    *,
    threshold: int = _DEFAULT_THRESHOLD,
) -> MatchResult:
    """Pure deterministic scoring of one extraction against the claims DB.

    An extraction with zero requirements has nothing to cover: its score
    is 0 by definition (empty numerator and denominator) — documented,
    explicit, and distinct from any real coverage.
    """
    index = _tag_index(db)
    breakdown: list[RequirementMatch] = []
    total_weight = 0.0
    weighted_credit = 0.0
    for requirement in ex.requirements:
        match = _match_requirement(requirement, index, syn)
        weight = _MUST_HAVE_WEIGHT if requirement.must_have else _NICE_WEIGHT
        total_weight += weight
        weighted_credit += weight * _CREDIT[match.status]
        breakdown.append(match)
    final_score = round(100 * weighted_credit / total_weight) if total_weight else 0
    return MatchResult(score=final_score, threshold=threshold, breakdown=breakdown)
