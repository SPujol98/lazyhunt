"""Deterministic cover-letter quality gate (telegram-improvements I3/I4, S2/S4).

No network, no real LLM. ATS systems do not score cover letters and AI
detectors are unreliable, so quality is measured with deterministic checks
and a fixed regression set of plain and florid letters
(``tests/fixtures/letters``).
"""

import asyncio
import json
import unicodedata
from pathlib import Path

import pytest
from pydantic import BaseModel

from cva.llm import LlmResponseError
from cva.match.cover_letter import (
    BANNED_PHRASES,
    FLORID_PHRASES,
    LetterParts,
    assemble_letter,
    build_letter_parts_prompt,
    generate_cover_letter,
    validate_parts,
)
from cva.match.judge import build_claims_digest
from cva.match.letter_quality import QUALITY_THRESHOLD, LetterQuality, letter_quality
from tests.test_cover_letter_v2 import CLAIMS, ES_BASE, ES_PARTS

LETTERS = Path(__file__).resolve().parent / "fixtures" / "letters"
MANIFEST: dict[str, dict[str, object]] = json.loads(
    (LETTERS / "manifest.json").read_text(encoding="utf-8")
)
# The regression letters are shorter than an assembled v2 letter; the same
# bounds apply to every one of them so length never decides the ranking.
FIXTURE_BOUNDS = {"min_words": 100, "max_words": 280}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _codes(quality: LetterQuality) -> set[str]:
    return {finding.code for finding in quality.findings}


def _words(n: int, word: str = "dato") -> str:
    """``n`` words in short plain sentences of five words each."""
    sentences = [" ".join([word] * 5).capitalize() + "." for _ in range(n // 5)]
    return " ".join(sentences)


PLAIN_EN = (
    "Your posting asks for Python and PostgreSQL. I built a FastAPI service on "
    "PostgreSQL last year. It has tests for every endpoint. I can show you the "
    "repository in a short call."
)


# --- individual checks --------------------------------------------------------------


def test_plain_letter_scores_full_marks() -> None:
    quality = letter_quality(
        PLAIN_EN,
        offer_requirements=["Python", "PostgreSQL"],
        language="en",
        min_words=10,
        max_words=100,
    )
    assert quality.score == 100 and quality.findings == ()
    assert quality.passed and quality.covered == ("Python", "PostgreSQL")


@pytest.mark.parametrize(
    ("language", "text", "phrase"),
    [
        ("es", "Me complace presentar mi candidatura a Acme.", "me complace"),
        ("es", "Quiero llevar el producto al siguiente nivel.", "siguiente nivel"),
        (
            "es",
            "En el dinámico mundo del software, sigo aprendiendo.",
            "en el dinámico mundo",
        ),
        ("es", "Busco sinergia entre producto y datos.", "sinergia"),
        ("en", "I am thrilled to apply to Acme.", "i am thrilled"),
        ("en", "I build cutting-edge tools.", "cutting-edge"),
        ("en", "I enjoy a fast-paced team.", "fast-paced"),
        ("en", "I am excited to apply for this role.", "i am excited to apply"),
        ("ca", "Soc una persona apassionada per les dades.", "apassionada"),
    ],
)
def test_florid_phrases_are_flagged_in_every_language(
    language: str, text: str, phrase: str
) -> None:
    quality = letter_quality(
        text, offer_requirements=(), language=language, min_words=1, max_words=100
    )
    assert "florid" in _codes(quality)
    assert any(_fold(phrase) in _fold(found) for found in quality.florid)
    assert quality.score < 100


def test_florid_list_extends_the_shared_cliche_list() -> None:
    assert not set(BANNED_PHRASES) & set(FLORID_PHRASES)  # one source, no copies
    for phrase in (
        "me complace",
        "siguiente nivel",
        "sinergia",
        "en el dinámico mundo",
    ):
        assert phrase in FLORID_PHRASES
    # every banned cliché also lowers the quality score
    quality = letter_quality(
        "Soy proactivo y me apasiona Python.",
        offer_requirements=(),
        language="es",
        min_words=1,
        max_words=100,
    )
    assert {"soy proactivo", "me apasiona"} <= set(quality.florid)


def test_florid_phrase_inside_a_word_is_not_flagged() -> None:
    quality = letter_quality(
        "Mantengo el pipeline de Deleverage Labs.",
        offer_requirements=(),
        language="es",
        min_words=1,
        max_words=100,
    )
    assert "florid" not in _codes(quality)


def test_intensifier_density_is_penalised() -> None:
    text = (
        "It was a very hard project. The team was truly great. "
        "We did really deep work. The results were incredibly good."
    )
    quality = letter_quality(
        text, offer_requirements=(), language="en", min_words=1, max_words=100
    )
    assert "intensifiers" in _codes(quality)
    assert quality.intensifiers_per_sentence == pytest.approx(1.0)


def test_one_intensifier_in_a_plain_letter_is_tolerated() -> None:
    text = PLAIN_EN + " It was very useful."
    quality = letter_quality(
        text, offer_requirements=(), language="en", min_words=1, max_words=100
    )
    assert "intensifiers" not in _codes(quality)


def test_long_sentences_are_penalised() -> None:
    long_sentence = "Word " + " ".join(["word"] * 39) + "."
    quality = letter_quality(
        f"{long_sentence} {long_sentence}",
        offer_requirements=(),
        language="en",
        min_words=1,
        max_words=200,
    )
    assert {"avg_sentence", "long_sentence"} <= _codes(quality)
    assert quality.avg_sentence_words == 40 and quality.max_sentence_words == 40


def test_requirement_coverage_lists_the_missing_must_haves() -> None:
    quality = letter_quality(
        PLAIN_EN,
        offer_requirements=["Python", "Kubernetes", "postgresql"],
        language="en",
        min_words=10,
        max_words=100,
    )
    assert quality.covered == ("Python", "postgresql")
    assert quality.missing == ("Kubernetes",)
    finding = next(f for f in quality.findings if f.code == "coverage")
    assert "Kubernetes" in finding.detail and quality.score < 100


def test_coverage_is_skipped_without_requirements() -> None:
    quality = letter_quality(
        PLAIN_EN, offer_requirements=(), language="en", min_words=10, max_words=100
    )
    assert "coverage" not in _codes(quality) and quality.missing == ()


def test_length_outside_the_limits_is_a_finding() -> None:
    short = letter_quality(PLAIN_EN, offer_requirements=(), language="en")
    assert "length" in _codes(short)  # default limits: the assembled letter's 250-350
    ok = letter_quality(_words(300, "word"), offer_requirements=(), language="en")
    assert "length" not in _codes(ok)


def test_em_dash_and_punctuation_en_dash_are_findings() -> None:
    for text in ("I built it — and it works.", "I built it – and it works."):
        quality = letter_quality(
            text, offer_requirements=(), language="en", min_words=1, max_words=100
        )
        assert "dash" in _codes(quality), text
    ranged = letter_quality(
        "I worked there 2020–2023.",
        offer_requirements=(),
        language="en",
        min_words=1,
        max_words=100,
    )
    assert "dash" not in _codes(ranged)


def test_figures_must_be_grounded_in_the_sources() -> None:
    text = "I cut the build time by 40%. Recall went from 0.61 to 0.78."
    grounded = letter_quality(
        text,
        offer_requirements=(),
        language="en",
        grounding="build time -40%; recall 0,61 -> 0,78",
        min_words=1,
        max_words=100,
    )
    assert "ungrounded" not in _codes(grounded)
    invented = letter_quality(
        text,
        offer_requirements=(),
        language="en",
        grounding="recall 0.61 -> 0.78",
        min_words=1,
        max_words=100,
    )
    finding = next(f for f in invented.findings if f.code == "ungrounded")
    assert "40" in finding.detail and invented.score < grounded.score


def test_exempt_text_is_not_policed_but_still_counts_for_coverage() -> None:
    fixed = "Soy un apasionado de Django y me apasiona Python."
    text = f"Vuestra oferta pide Kubernetes. Lo he usado. {fixed}"
    quality = letter_quality(
        text,
        offer_requirements=["Django", "Kubernetes"],
        language="es",
        exempt=[fixed],
        min_words=1,
        max_words=100,
    )
    assert "florid" not in _codes(quality)
    assert quality.covered == ("Django", "Kubernetes")


def test_feedback_lines_name_every_finding() -> None:
    quality = letter_quality(
        "I am thrilled — truly.",
        offer_requirements=["Rust"],
        language="en",
        min_words=1,
        max_words=100,
    )
    feedback = "\n".join(quality.feedback())
    assert "i am thrilled" in feedback and "Rust" in feedback and "dash" in feedback


# --- regression set (S4) --------------------------------------------------------------


def _fixture_quality(name: str) -> LetterQuality:
    meta = MANIFEST[name]
    requirements = meta["requirements"]
    assert isinstance(requirements, list)
    return letter_quality(
        (LETTERS / name).read_text(encoding="utf-8"),
        offer_requirements=[str(r) for r in requirements],
        language=str(meta["language"]),
        **FIXTURE_BOUNDS,
    )


def _by_kind(kind: str) -> list[str]:
    return sorted(name for name, meta in MANIFEST.items() if meta["kind"] == kind)


def test_regression_set_has_plain_and_florid_letters_in_both_languages() -> None:
    files = sorted(p.name for p in LETTERS.glob("*.txt"))
    assert files == sorted(MANIFEST) and len(files) >= 10
    for kind in ("plain", "florid"):
        assert {MANIFEST[n]["language"] for n in _by_kind(kind)} == {"es", "en"}


def test_every_plain_letter_ranks_above_every_florid_letter() -> None:
    plain = {name: _fixture_quality(name).score for name in _by_kind("plain")}
    florid = {name: _fixture_quality(name).score for name in _by_kind("florid")}
    assert min(plain.values()) > max(florid.values()), (plain, florid)
    assert all(score >= QUALITY_THRESHOLD for score in plain.values()), plain
    assert all(score < QUALITY_THRESHOLD for score in florid.values()), florid


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_regression_letter_flags_exactly_its_florid_phrases(name: str) -> None:
    quality = _fixture_quality(name)
    expected = MANIFEST[name]["florid"]
    assert isinstance(expected, list)
    found = [_fold(phrase) for phrase in quality.florid]
    for phrase in expected:
        assert any(_fold(str(phrase)) in item for item in found), (phrase, found)
    if not expected:
        assert quality.florid == ()


# --- the gate inside generation (before/after) ---------------------------------------

FLORID_PARTS = {
    **ES_PARTS,
    "why_company": ES_PARTS["why_company"].replace(
        "Además, he mantenido cargas nocturnas de un cliente de logística y sé lo "
        "que cuesta mantener algo en marcha.",
        "Sin duda, es un proyecto de vanguardia e innovador donde puedo aportar valor.",
    ),
    "why_role": ES_PARTS["why_role"].replace(
        "y quiero dar ese paso con vosotros.",
        "y quiero llevarlo al siguiente nivel con gran entusiasmo.",
    ),
}


class _Llm:
    model = "fake"

    def __init__(self, *answers: dict[str, str] | Exception) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.users.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return schema.model_validate(answer)


def _generate(llm: _Llm, requirements: tuple[str, ...] = ()):
    return asyncio.run(
        generate_cover_letter(
            llm,  # type: ignore[arg-type]
            offer_text="Buscamos AI Engineer en Valencia con Python y RAG.",
            title="AI Engineer",
            company="Acme",
            language="es",
            branch="AI",
            claims_digest=build_claims_digest(CLAIMS),
            apply_findings=[],
            voice_sample="",
            base=ES_BASE,
            offer_requirements=requirements,
        )
    )


def _text(parts: dict[str, str]) -> str:
    return assemble_letter(ES_BASE, LetterParts(**parts), language="es", company="Acme")


def test_florid_draft_passes_the_hard_validator_but_fails_the_gate() -> None:
    parts = LetterParts(**FLORID_PARTS)
    assert validate_parts(parts, ES_BASE, language="es", company="Acme") == []


def test_gate_rewrites_a_florid_draft_and_keeps_the_plain_rewrite() -> None:
    llm = _Llm(FLORID_PARTS, ES_PARTS)
    result = _generate(llm, ("Python", "RAG"))
    assert len(llm.users) == 2
    assert "de vanguardia" in llm.users[1] and "siguiente nivel" in llm.users[1]
    assert result.text == _text(ES_PARTS) and result.warnings == []
    assert result.quality is not None and result.quality.passed
    assert result.quality.covered == ("Python", "RAG")


def test_gate_keeps_the_draft_when_the_rewrite_scores_worse() -> None:
    worse = {
        **FLORID_PARTS,
        "linking": "Sin duda, en el dinámico mundo de Acme quiero aportar valor innovador.",
    }
    llm = _Llm(FLORID_PARTS, worse)
    result = _generate(llm)
    assert len(llm.users) == 2 and result.text == _text(FLORID_PARTS)
    assert result.quality is not None and not result.quality.passed


def test_gate_keeps_the_draft_when_the_rewrite_call_fails() -> None:
    llm = _Llm(FLORID_PARTS, LlmResponseError("boom"))
    result = _generate(llm)
    assert result.text == _text(FLORID_PARTS) and result.quality is not None


def test_gate_rejects_a_rewrite_that_breaks_the_hard_validator() -> None:
    broken = {**ES_PARTS, "why_company": "Acme es mi terreno."}
    llm = _Llm(FLORID_PARTS, broken)
    result = _generate(llm)
    assert result.text == _text(FLORID_PARTS) and result.warnings == []


def test_a_good_draft_is_delivered_with_one_call_and_its_quality() -> None:
    llm = _Llm(ES_PARTS)
    result = _generate(llm, ("Python",))
    assert len(llm.users) == 1 and result.text == _text(ES_PARTS)
    assert result.quality is not None
    assert result.quality.score >= QUALITY_THRESHOLD and result.quality.findings == ()


def test_prompt_asks_for_plain_first_person_style() -> None:
    system, _user = build_letter_parts_prompt(
        offer_text="Buscamos AI Engineer.",
        title="AI Engineer",
        company="Acme",
        language="es",
        branch="AI",
        claims_digest="c1: x",
        apply_findings=(),
        voice_sample="",
        base=ES_BASE,
        facts=(),
    )
    assert "Plain style" in system and "first person" in system
    assert "superlatives" in system and "siguiente nivel" in system
    assert system.rstrip().endswith(
        "13. Write in the language requested by the user message."
    ) or ("Branch emphasis" in system)
