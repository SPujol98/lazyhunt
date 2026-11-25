"""Shorter, sharper cover letters (beta.8 owner feedback, 2026-11-25).

A reader with little time skims the letter: 150-250 words, the strongest
evidence first, no explanations of basics to a technical team, no confessed
gaps, and the letter addresses the company on the card, never its parent group
or a recruiter. No network, no real LLM: fake replies only.
"""

import asyncio

import pytest
from pydantic import BaseModel

from cva.i18n import t
from cva.match.cover_letter import (
    LETTER_MAX_WORDS,
    LETTER_MIN_WORDS,
    LetterParts,
    assemble_letter,
    build_letter_parts_prompt,
    generate_cover_letter,
)
from cva.match.judge import build_claims_digest
from cva.match.letter_quality import (
    QUALITY_THRESHOLD,
    LetterQuality,
    letter_quality,
    related_companies,
)
from tests.test_cover_letter_v2 import CLAIMS, EN_BASE, EN_PARTS, ES_BASE


def _codes(quality: LetterQuality) -> set[str]:
    return {finding.code for finding in quality.findings}


def _words(n: int, word: str = "word") -> str:
    """``n`` words in short plain sentences of five words each."""
    return " ".join(" ".join([word] * 5).capitalize() + "." for _ in range(n // 5))


def _score(text: str) -> LetterQuality:
    return letter_quality(
        text, offer_requirements=(), language="en", min_words=1, max_words=500
    )


# --- S1 length: 150-250 words -------------------------------------------------------


def test_letter_bounds_are_150_to_250_words() -> None:
    assert (LETTER_MIN_WORDS, LETTER_MAX_WORDS) == (150, 250)


@pytest.mark.parametrize(
    ("words", "flagged"), [(110, True), (125, False), (200, False), (255, True)]
)
def test_quality_length_window_is_about_120_to_250(words: int, flagged: bool) -> None:
    quality = letter_quality(_words(words), offer_requirements=(), language="en")
    assert ("length" in _codes(quality)) is flagged


def test_a_370_word_letter_fails_the_gate_on_length_alone() -> None:
    quality = letter_quality(_words(370), offer_requirements=(), language="en")
    assert _codes(quality) == {"length"}
    assert quality.score < QUALITY_THRESHOLD


def _prompt(
    company: str = "Acme", offer: str = "Buscamos AI Engineer."
) -> tuple[str, str]:
    return build_letter_parts_prompt(
        offer_text=offer,
        title="AI Engineer",
        company=company,
        language="es",
        branch="AI",
        claims_digest="c1: x",
        apply_findings=(),
        voice_sample="",
        base=ES_BASE,
        facts=(),
    )


def test_prompt_asks_for_a_short_letter_led_by_the_strongest_evidence() -> None:
    system, user = _prompt()
    assert "150-250 words" in user
    assert "Lead with the strongest evidence" in system
    assert "2-3 most offer-relevant concrete achievements" in system


# --- S2 no explaining basics ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "RAG is a technique that lets a model search documents first.",
        "I built tool calling, which lets the model call external APIs.",
        "My pipeline uses embeddings, which allows semantic search.",
        "RAG es una técnica que combina búsqueda y generación.",
        "Usé tool calling, que permite al modelo llamar a herramientas.",
        "El RAG és una tècnica que combina cerca i generació.",
        "Vaig fer servir tool calling, que permet al model cridar eines.",
    ],
)
def test_definitional_sentences_are_flagged(text: str) -> None:
    assert "definition" in _codes(_score(text))


@pytest.mark.parametrize(
    "text",
    [
        "I built a RAG pipeline with a 200-question benchmark.",
        "Construí un RAG híbrido con su propio benchmark de 200 preguntas.",
        "Vaig construir un motor de tool calling amb 139 tests.",
    ],
)
def test_plain_statements_about_the_stack_are_not_definitions(text: str) -> None:
    assert "definition" not in _codes(_score(text))


def test_prompt_forbids_explaining_basics() -> None:
    system, _user = _prompt()
    assert "Do not explain what" in system


# --- S3 never state gaps --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I have not used Prefect yet.",
        "I haven't worked with Airflow yet.",
        "I have no experience with Kafka.",
        "Aún no he usado Prefect.",
        "No tengo experiencia con Kafka.",
        "Todavía no he trabajado con Airflow.",
        "Encara no he fet servir Prefect.",
        "No tinc experiència amb Kafka.",
    ],
)
def test_gap_phrases_are_flagged(text: str) -> None:
    assert "gap" in _codes(_score(text))


@pytest.mark.parametrize(
    "text",
    ["I used Airflow at Globex for two years.", "Ya he usado Prefect en producción."],
)
def test_real_experience_is_not_a_gap(text: str) -> None:
    assert "gap" not in _codes(_score(text))


def test_prompt_never_asks_to_confess_a_missing_skill() -> None:
    system, _user = _prompt()
    assert "never claim the missing skill" not in system
    assert "do not mention tools or skills the claims do not back" in system
    assert "closest real experience" in system


# --- S5 the company on the card, never its parent group ------------------------------

POWENS_OFFER = (
    "Powens (part of the Crédit Mutuel Arkéa group) is hiring a Backend Engineer "
    "in Paris. You will work with Python and PostgreSQL."
)
ARKEA = "Crédit Mutuel Arkéa"


@pytest.mark.parametrize(
    ("offer", "company", "expected"),
    [
        (POWENS_OFFER, "Powens", (ARKEA,)),
        (
            "Globex, filial de Grupo Initech, busca ingeniero.",
            "Globex",
            ("Grupo Initech",),
        ),
        (
            "Hiring on behalf of Umbrella Corp: a data engineer.",
            "Acme",
            ("Umbrella Corp",),
        ),
        ("Buscamos en nombre de Hooli un backend.", "Acme", ("Hooli",)),
        ("Acme forma part del grup Vandelay.", "Acme", ("Vandelay",)),
        ("We are part of our parent's mission.", "Acme", ()),
        # The card already names the group: nothing to guard against.
        (POWENS_OFFER, ARKEA, ()),
    ],
)
def test_related_companies_come_from_parent_and_recruiter_phrases(
    offer: str, company: str, expected: tuple[str, ...]
) -> None:
    assert related_companies(offer, company) == expected


def _company_quality(text: str, company: str = "Powens") -> LetterQuality:
    return letter_quality(
        text,
        offer_requirements=(),
        language="en",
        min_words=1,
        max_words=500,
        company=company,
        offer_text=POWENS_OFFER,
    )


def test_letter_naming_the_parent_group_is_flagged() -> None:
    quality = _company_quality(f"Dear Powens team,\n\nI admire what {ARKEA} builds.")
    finding = next(f for f in quality.findings if f.code == "company")
    assert ARKEA in finding.detail and "Powens" in finding.detail
    assert quality.score < QUALITY_THRESHOLD


def test_letter_naming_only_the_card_company_passes() -> None:
    quality = _company_quality("Dear Powens team,\n\nI admire what Powens builds.")
    assert "company" not in _codes(quality)


def test_letter_that_never_names_the_card_company_is_flagged() -> None:
    quality = _company_quality("Dear hiring team,\n\nI admire what you build.")
    assert "company" in _codes(quality)


def test_unknown_card_company_skips_the_company_check() -> None:
    quality = _company_quality(f"I admire {ARKEA}.", company="(unknown company)")
    assert "company" not in _codes(quality)


def test_prompt_pins_the_hiring_company_and_forbids_other_employers() -> None:
    system, user = _prompt(company="Powens", offer=POWENS_OFFER)
    assert "Hiring company: Powens" in user
    assert "never name another employer" in user
    assert "parent group" in system and "recruiter" in system


ARKEA_PARTS = {key: value.replace("Acme", ARKEA) for key, value in EN_PARTS.items()}
POWENS_PARTS = {key: value.replace("Acme", "Powens") for key, value in EN_PARTS.items()}


class _Llm:
    model = "fake"

    def __init__(self, *answers: dict[str, str]) -> None:
        self.answers = list(answers)
        self.users: list[str] = []

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.users.append(user)
        return schema.model_validate(self.answers.pop(0))


def _generate(llm: _Llm):
    return asyncio.run(
        generate_cover_letter(
            llm,  # type: ignore[arg-type]
            offer_text=POWENS_OFFER,
            title="Backend Engineer",
            company="Powens",
            language="en",
            branch="AI",
            claims_digest=build_claims_digest(CLAIMS),
            apply_findings=[],
            voice_sample="",
            base=EN_BASE,
            ui_lang="en",
        )
    )


def _text(parts: dict[str, str]) -> str:
    return assemble_letter(
        EN_BASE, LetterParts(**parts), language="en", company="Powens"
    )


def test_wrong_company_reply_is_rewritten_once_with_an_explicit_instruction() -> None:
    llm = _Llm(ARKEA_PARTS, POWENS_PARTS)
    result = _generate(llm)
    assert len(llm.users) == 2
    assert "Powens" in llm.users[1] and ARKEA in llm.users[1]
    assert "do not name" in llm.users[1]
    assert result.text == _text(POWENS_PARTS) and ARKEA not in result.text
    assert result.quality is not None and "company" not in _codes(result.quality)
    assert result.warnings == []


def test_wrong_company_that_survives_the_rewrite_is_flagged() -> None:
    llm = _Llm(ARKEA_PARTS, ARKEA_PARTS)
    result = _generate(llm)
    assert len(llm.users) == 2  # one rewrite, never a loop
    assert result.quality is not None and "company" in _codes(result.quality)
    assert result.warnings == [
        t("notifier.letter_company_mismatch", "en", company="Powens", other=ARKEA)
    ]


def test_quality_rewrite_tightens_instead_of_padding() -> None:
    llm = _Llm(ARKEA_PARTS, POWENS_PARTS)
    _generate(llm)
    assert "never pad" in llm.users[1] and "cut" in llm.users[1]


@pytest.mark.parametrize("lang", ["en", "es"])
def test_company_mismatch_warning_is_translated(lang: str) -> None:
    text = t("notifier.letter_company_mismatch", lang, company="Powens", other=ARKEA)
    assert "Powens" in text and ARKEA in text and "{" not in text
