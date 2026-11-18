"""Extraction tests: literal evidence, cache hit ⇒ zero LLM calls, key shape.

FakeLlmClient with a call counter (design D8, testing strategy): no network,
no real LLM. Cache key = ``sha256(normalized_text + model + prompt_version)``.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from cva.llm import LlmResponseError
from cva.match.extraction import (
    _SYSTEM_PROMPT,
    PROMPT_VERSION,
    ExtractionCache,
    OfferExtraction,
    Requirement,
    cache_key,
    extract,
)

OFFER_TEXT = (
    "AI Engineer en Acme (Barcelona). Requisitos: experiencia con Python y "
    "RAG. Imprescindible: FastAPI. Se valoran conocimientos de vector "
    "databases. Ofrecemos formación en LLMs."
)


class FakeLlmClient:
    """Call-counting stand-in for the LlmClient protocol (zero network)."""

    def __init__(
        self,
        reply: OfferExtraction | None = None,
        *,
        model: str = "test-model",
        error: Exception | None = None,
    ) -> None:
        self.model = model
        self.calls = 0
        self.last_system: str | None = None
        self.last_model: str | None = None
        self._reply = reply if reply is not None else _literal_reply()
        self._error = error

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        self.calls += 1
        self.last_system = system
        self.last_model = model
        if self._error is not None:
            raise self._error
        return self._reply


class _Clock:
    def __call__(self) -> str:
        return "2026-08-30"


def _literal_reply() -> OfferExtraction:
    return OfferExtraction(
        title="AI Engineer",
        company="Acme",
        requirements=[
            Requirement(
                name="Python",
                category="skill",
                must_have=True,
                evidence="experiencia con Python",
            ),
            Requirement(
                name="FastAPI",
                category="tool",
                must_have=True,
                evidence="Imprescindible: FastAPI",
            ),
        ],
        keywords=["Python", "RAG", "FastAPI"],
    )


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "cache.db")


@pytest.fixture()
def cache(conn: sqlite3.Connection) -> ExtractionCache:
    return ExtractionCache(conn, clock=_Clock())


# --- extraction happy path ---


def test_extract_returns_literal_evidence_with_one_llm_call(
    cache: ExtractionCache,
) -> None:
    llm = FakeLlmClient()
    ex = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert [r.name for r in ex.requirements] == ["Python", "FastAPI"]
    for r in ex.requirements:
        assert r.evidence in OFFER_TEXT
    assert ex.keywords == ["Python", "RAG", "FastAPI"]
    assert ex.title == "AI Engineer"
    assert ex.company == "Acme"
    assert llm.calls == 1


def test_second_identical_analysis_is_cache_hit_zero_calls(
    cache: ExtractionCache,
) -> None:
    llm = FakeLlmClient()
    first = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    second = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert second == first
    assert llm.calls == 1


def test_whitespace_variant_of_same_text_hits_cache(
    cache: ExtractionCache,
) -> None:
    llm = FakeLlmClient()
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    variant = f"\n\n  {OFFER_TEXT} \t"
    asyncio.run(extract(variant, llm, cache=cache))
    assert llm.calls == 1


def test_different_text_misses_cache(cache: ExtractionCache) -> None:
    llm = FakeLlmClient()
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    asyncio.run(extract("Senior Data Scientist role in Madrid.", llm, cache=cache))
    assert llm.calls == 2


def test_model_override_is_forwarded_and_used_as_cache_key(
    conn: sqlite3.Connection,
) -> None:
    """extraction_model (per-step model selection) overrides llm.model for
    this call AND for the cache key/stamp — a later call without the
    override (a different effective model) must miss the cache."""
    llm = FakeLlmClient(model="claude-code:default")
    cache_a = ExtractionCache(conn, clock=_Clock())
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache_a, model="haiku"))
    assert llm.calls == 1
    assert llm.last_model == "haiku"
    cache_b = ExtractionCache(conn, clock=_Clock())
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache_b))  # no override → llm.model
    assert llm.calls == 2  # different effective model → cache miss
    assert llm.last_model is None


def test_no_override_omits_model_kwarg_for_backward_compatible_fakes(
    cache: ExtractionCache,
) -> None:
    """When ``model`` is not overridden, ``extract`` calls the client exactly
    as before this feature — no ``model=`` keyword — so a fake written
    against the pre-override protocol (only 3 positional params) keeps
    working unmodified."""

    class LegacyFakeLlmClient:
        def __init__(self) -> None:
            self.model = "legacy-model"
            self.calls = 0

        async def json_completion(
            self, system: str, user: str, schema: type[object]
        ) -> object:
            self.calls += 1
            return _literal_reply()

    llm = LegacyFakeLlmClient()
    ex = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert ex.title == "AI Engineer"
    assert llm.calls == 1


def test_different_model_misses_cache(conn: sqlite3.Connection) -> None:
    llm_a = FakeLlmClient(model="model-a")
    cache_a = ExtractionCache(conn, clock=_Clock())
    asyncio.run(extract(OFFER_TEXT, llm_a, cache=cache_a))
    llm_b = FakeLlmClient(model="model-b")
    cache_b = ExtractionCache(conn, clock=_Clock())
    asyncio.run(extract(OFFER_TEXT, llm_b, cache=cache_b))
    assert llm_a.calls == 1
    assert llm_b.calls == 1


# --- evidence grounding ---


def test_non_literal_evidence_requirement_dropped(
    cache: ExtractionCache,
) -> None:
    hallucinated = OfferExtraction(
        requirements=[
            Requirement(
                name="Kubernetes",
                category="tool",
                must_have=True,
                evidence="experiencia orquestando Kubernetes",  # not in offer
            ),
            Requirement(
                name="Python",
                category="skill",
                must_have=True,
                evidence="experiencia con Python",
            ),
        ],
        keywords=[],
    )
    llm = FakeLlmClient(reply=hallucinated)
    ex = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert [r.name for r in ex.requirements] == ["Python"]
    assert llm.calls == 1


def test_all_evidence_non_literal_yields_empty_requirements(
    cache: ExtractionCache,
) -> None:
    hallucinated = OfferExtraction(
        requirements=[
            Requirement(
                name="Kubernetes",
                category="tool",
                must_have=True,
                evidence="totally invented quote",
            ),
        ],
        keywords=[],
    )
    llm = FakeLlmClient(reply=hallucinated)
    ex = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert ex.requirements == []
    again = asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert again == ex
    assert llm.calls == 1  # the emptied extraction is cached too


def test_requirement_model_rejects_empty_evidence() -> None:
    with pytest.raises(ValidationError):
        Requirement(name="Python", category="skill", must_have=True, evidence="")


# --- LLM error propagation ---


def test_llm_response_error_propagates_and_nothing_cached(
    cache: ExtractionCache,
) -> None:
    llm = FakeLlmClient(error=LlmResponseError("malformed chat-completion"))
    with pytest.raises(LlmResponseError, match="malformed"):
        asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert llm.calls == 1
    assert cache.get(cache_key(OFFER_TEXT, "test-model")) is None


# --- cache write-through and key shape ---


def test_cache_write_through_persists_row(
    conn: sqlite3.Connection, cache: ExtractionCache
) -> None:
    llm = FakeLlmClient()
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    key = cache_key(OFFER_TEXT, "test-model")
    rows = conn.execute(
        "SELECT model, created_at, extraction_json FROM extraction_cache "
        "WHERE cache_key = ?",
        (key,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "test-model"
    assert rows[0][1] == "2026-08-30"
    reloaded = ExtractionCache(conn, clock=_Clock()).get(key)
    assert reloaded == _literal_reply()


def test_cache_key_is_stable_and_component_sensitive() -> None:
    assert cache_key("text", "m") == cache_key("text", "m")
    assert cache_key("text", "m") != cache_key("other text", "m")
    assert cache_key("text", "m") != cache_key("text", "m2")
    assert cache_key("text", "m", prompt_version="3") != cache_key(
        "text", "m", prompt_version=PROMPT_VERSION
    )
    assert cache_key("  text  ", "m") == cache_key("text", "m")


# --- hardened prompt contract (v2: JSON-only mandate + inline schema) ---


def test_prompt_version_is_bumped_past_v1() -> None:
    """The v1 prompt (no JSON mandate) produced pure-prose completions on a
    Catalan offer; v2 must change the cache key so cached failures never
    mask the fix."""
    assert PROMPT_VERSION == "2"


def test_system_prompt_carries_json_only_mandate() -> None:
    assert "ONLY one JSON object" in _SYSTEM_PROMPT
    assert "no markdown" in _SYSTEM_PROMPT
    assert "no code fences" in _SYSTEM_PROMPT
    assert "no explanations before or after" in _SYSTEM_PROMPT


def test_system_prompt_carries_inline_schema_matching_model() -> None:
    """The inline shape literal must name every OfferExtraction/Requirement
    field so the prompt stays in sync with the grounding models."""
    for field in ("title", "company", "requirements", "keywords"):
        assert f'"{field}"' in _SYSTEM_PROMPT, field
    for field in ("name", "category", "must_have", "evidence"):
        assert f'"{field}"' in _SYSTEM_PROMPT, field
    for literal in ("skill", "tool", "domain", "soft"):
        assert f'"{literal}"' in _SYSTEM_PROMPT, literal
    properties = OfferExtraction.model_json_schema()["properties"]
    for field in properties:
        assert field in _SYSTEM_PROMPT, field


def test_system_prompt_carries_any_language_clause() -> None:
    assert "any language" in _SYSTEM_PROMPT
    assert "verbatim" in _SYSTEM_PROMPT


def test_extract_sends_hardened_system_prompt(
    cache: ExtractionCache,
) -> None:
    llm = FakeLlmClient()
    asyncio.run(extract(OFFER_TEXT, llm, cache=cache))
    assert llm.last_system is not None
    assert "ONLY one JSON object" in llm.last_system
    assert '"must_have"' in llm.last_system
