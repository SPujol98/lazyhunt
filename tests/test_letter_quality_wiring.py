"""The letter quality reaches the chat (telegram-improvements I3 wiring)."""

from cva.match.letter_quality import LetterQuality
from cva.notifier import letter_quality_line


def _quality(score: int, missing: tuple[str, ...] = ()) -> LetterQuality:
    return LetterQuality(score=score, missing=missing)


def test_quality_line_shows_the_score_in_both_languages() -> None:
    assert (
        letter_quality_line(_quality(92), lang="es") == "🧪 Calidad de la carta: 92/100"
    )
    assert letter_quality_line(_quality(92), lang="en") == "🧪 Letter quality: 92/100"


def test_quality_line_lists_the_missing_must_haves() -> None:
    line = letter_quality_line(_quality(64, ("Python", "RAG")), lang="es")
    assert line == "🧪 Calidad de la carta: 64/100 · no menciona: Python, RAG"
    line = letter_quality_line(_quality(64, ("Python",)), lang="en")
    assert line == "🧪 Letter quality: 64/100 · not mentioned: Python"


def test_no_quality_no_line() -> None:
    assert letter_quality_line(None, lang="es") == ""


def test_service_reads_must_haves_from_the_cached_extraction(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No extra LLM call: the names come from the analysis cache, [] if absent."""
    from cva.match.extraction import OfferExtraction, Requirement, cache_key
    from tests.test_service import FakeFetcher, FakeLlmClient, make_service

    service = make_service(tmp_path, FakeLlmClient([]), FakeFetcher(None))[0]  # type: ignore[arg-type]
    text = "Offer text with Python and RAG."
    model = service._extraction_model or service._llm.model
    assert service._cached_must_haves(text) == []
    service._cache.put(
        cache_key(text, model),
        OfferExtraction(
            requirements=[
                Requirement(
                    name="Python", category="skill", must_have=True, evidence="Python"
                ),
                Requirement(
                    name="RAG", category="skill", must_have=False, evidence="RAG"
                ),
            ]
        ),
        model=model,
    )
    assert service._cached_must_haves(text) == ["Python"]
