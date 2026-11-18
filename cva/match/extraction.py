"""LLM offer extraction: ``OfferExtraction`` grounding models (design D8).

The models live here so the scorer can reference :class:`Requirement`;
``extract`` and the SQLite-backed cache are defined in this same module
(tasks 6.5/6.6). Every requirement's ``evidence`` MUST be a literal quote
from the offer text — grounding the extraction and making it auditable.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, Field

from cva.llm import LlmClient

RequirementCategory = Literal["skill", "tool", "domain", "soft"]


class Requirement(BaseModel):
    """One requirement extracted from an offer, grounded in literal evidence."""

    name: str = Field(min_length=1)
    category: RequirementCategory
    must_have: bool
    evidence: str = Field(min_length=1)  # literal quote from the offer text


class OfferExtraction(BaseModel):
    """Structured extraction of one job offer."""

    title: str | None = None
    company: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


PROMPT_VERSION: Final[str] = "2"

_SYSTEM_PROMPT: Final[str] = (
    "You extract structured requirements from job offers. For each "
    "requirement: name (canonical short skill/tool/domain/soft-skill name), "
    "category (skill|tool|domain|soft), must_have (true only when the offer "
    "marks it required/mandatory, false when valued/nice-to-have), and "
    "evidence — an EXACT literal substring copied verbatim from the offer "
    "text that supports the requirement; never paraphrase or translate it. "
    "Also list the offer's salient keywords. Requirements whose evidence "
    "is not a verbatim quote are discarded by the caller.\n"
    "\n"
    "Respond with ONLY one JSON object — no markdown, no code fences, no "
    "explanations before or after. The exact expected shape is:\n"
    "\n"
    '{"title": str|null, "company": str|null, "requirements": '
    '[{"name": str, "category": "skill"|"tool"|"domain"|"soft", '
    '"must_have": bool, "evidence": str}], "keywords": [str]}\n'
    "\n"
    "The job offer may be in any language (Spanish, Catalan, English, "
    "…). Keep evidence quotes verbatim in the offer's language; the JSON "
    "structure and field names stay exactly as specified."
)
# Keep the inline shape literal in sync with Requirement/OfferExtraction;
# tests/test_extraction.py::test_system_prompt_carries_inline_schema_matching_model
# enforces it.

_CACHE_DDL: Final[str] = (
    "CREATE TABLE IF NOT EXISTS extraction_cache ("
    "cache_key TEXT PRIMARY KEY, extraction_json TEXT NOT NULL, "
    "model TEXT, created_at TEXT)"
)


def _default_clock() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _normalize_text(text: str) -> str:
    """Collapse every whitespace run to a single space and strip the ends."""
    return " ".join(text.split())


def cache_key(text: str, model: str, *, prompt_version: str = PROMPT_VERSION) -> str:
    """``sha256(normalized_text + model + prompt_version)`` — NUL-separated."""
    joined = "\x00".join((_normalize_text(text), model, prompt_version))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ExtractionCache:
    """SQLite-backed cache over the ``extraction_cache`` table (write-through)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._conn.execute(_CACHE_DDL)
        self._conn.commit()
        self._clock = clock or _default_clock

    def get(self, key: str) -> OfferExtraction | None:
        row = self._conn.execute(
            "SELECT extraction_json FROM extraction_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return OfferExtraction.model_validate_json(row[0])

    def put(self, key: str, extraction: OfferExtraction, *, model: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO extraction_cache "
            "(cache_key, extraction_json, model, created_at) VALUES (?, ?, ?, ?)",
            (key, extraction.model_dump_json(), model, self._clock()),
        )
        self._conn.commit()


async def extract(
    text: str,
    llm: LlmClient,
    *,
    cache: ExtractionCache,
    model: str | None = None,
) -> OfferExtraction:
    """Extract an offer's requirements via the LLM, cache-write-through.

    A cache hit makes zero LLM calls (determinism + cost control). The
    client runs at temperature 0 with a JSON schema; ``LlmResponseError``
    from the client propagates untouched and nothing is cached. Any
    requirement whose ``evidence`` is not a verbatim substring of the
    offer text is rejected here — grounding survives a hallucinating LLM.

    ``model`` (per-step model selection, e.g. ``Settings.extraction_model``)
    overrides the client's configured default for this call only; the cache
    key/stamp use the EFFECTIVE model (the override when given, else
    ``llm.model``) so a model change never collides with a differently
    generated cached extraction. When ``model`` is ``None`` the call is made
    exactly as before this override existed — no ``model=`` keyword reaches
    ``llm.json_completion`` — so a fake LLM client written against the
    pre-override protocol keeps working unmodified.
    """
    effective_model = model if model is not None else llm.model
    key = cache_key(text, effective_model)
    cached = cache.get(key)
    if cached is not None:
        return cached
    extraction = (
        await llm.json_completion(_SYSTEM_PROMPT, text, OfferExtraction, model=model)
        if model is not None
        else await llm.json_completion(_SYSTEM_PROMPT, text, OfferExtraction)
    )
    grounded = [r for r in extraction.requirements if r.evidence in text]
    result = extraction.model_copy(update={"requirements": grounded})
    cache.put(key, result, model=effective_model)
    return result
