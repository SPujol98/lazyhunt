"""Cover letter v2: base letter from the profile + generated parts, assembled by code.

No network. The base is the demo profile's (Alex Rivera); the fixtures below are
valid generated parts for it (ES and EN) and the single source other modules reuse.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from cva.claimsdb import load_claims
from cva.llm import LlmResponseError
from cva.match.apply_instructions import ApplyInstruction
from cva.match.cover_letter import (
    LETTER_MAX_WORDS,
    LETTER_MIN_WORDS,
    LetterParts,
    assemble_letter,
    build_letter_parts_prompt,
    generate_cover_letter,
    normalize_company,
    normalize_register,
    render_salutation,
    validate_parts,
)
from cva.match.interview_prep import CompanyFact
from cva.match.judge import build_claims_digest
from cva.profile_identity import ProfileError
from cva.profile_persona import LetterBase, load_letter_bases
from tests.demo import DEMO_CLAIMS, DEMO_DIR

BASES = load_letter_bases(DEMO_DIR / "cover_letter_base.toml")
ES_BASE, EN_BASE, CA_BASE = BASES["es"], BASES["en"], BASES["ca"]
CLAIMS = load_claims(DEMO_CLAIMS)

# Sized for the beta.8 budgets (linking <= 20, paragraph 2 25-45, paragraph 3
# 25-50, whole letter 150-250 words), strongest evidence first and no
# confessed gap (the previous "Todavía no he operado..." sentence is now a
# quality finding).
ES_PARTS = {
    "tone": "cercano",
    "linking": (
        "Vi que en Acme llevaréis agentes con LLM a producción desde Valencia, "
        "y es lo que hago."
    ),
    "why_company": (
        "Acme busca llevar sistemas con RAG a producción desde Valencia. En "
        "DocSearch comparé dos tipos de búsqueda con 150 preguntas escritas a mano. "
        "Es mi terreno. Además, he mantenido cargas nocturnas de un cliente de "
        "logística y sé lo que cuesta mantener algo en marcha."
    ),
    "why_role": (
        "El puesto pide Python y RAG sobre datos propios. En DocSearch llevé la "
        "recuperación híbrida de 0,61 a 0,78 de recall. En Metrics API construí un "
        "servicio con FastAPI y PostgreSQL, con tests al 91 %. Quiero aplicar ese "
        "método con vosotros."
    ),
}
EN_PARTS = {
    "tone": "cercano",
    "linking": (
        "I saw that Acme is taking LLM agents to production from Valencia, which "
        "is what I do."
    ),
    "why_company": (
        "Acme wants to bring RAG systems to production from Valencia. In DocSearch I "
        "compared two kinds of search on 150 hand-written questions. It is my "
        "ground. I have also kept a logistics client's nightly loads running."
    ),
    "why_role": (
        "The role asks for Python and RAG over your own data. In DocSearch I lifted "
        "hybrid retrieval recall from 0.61 to 0.78. In Metrics API I built a FastAPI "
        "service on PostgreSQL with 91% test coverage. I want to apply that method "
        "with you."
    ),
}
ACME_FACT = CompanyFact(
    fact="Acme desarrolla agentes de IA para logística.",
    source_url="https://acme.example.com/about",
)


def _letter(parts: dict[str, str], base: LetterBase, language: str, company="Acme"):
    return assemble_letter(
        base, LetterParts(**parts), language=language, company=company
    )


# --- base letter loading (profile data) -------------------------------------------


def test_demo_base_has_every_language_and_is_alex_not_the_owner() -> None:
    assert set(BASES) == {"es", "en", "ca"}
    for base in BASES.values():
        assert base.signoff.endswith("Alex Rivera")
        assert base.paragraph1 and base.closing


def test_resolver_points_the_demo_profile_at_its_own_base() -> None:
    from cva.profile_paths import resolve_profile_paths

    assert resolve_profile_paths().letter_base == DEMO_DIR / "cover_letter_base.toml"


@pytest.mark.parametrize(
    "toml",
    [
        "",  # no language tables
        "[es]\nsalutation = 'x'\n",  # incomplete es, no en/ca
        "[xx]\nsalutation = 'x'\n",  # unknown language
    ],
)
def test_invalid_base_fails_loudly(tmp_path: Path, toml: str) -> None:
    path = tmp_path / "cover_letter_base.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ProfileError):
        load_letter_bases(path)


def test_missing_base_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ProfileError):
        load_letter_bases(tmp_path / "nope.toml")


def test_unknown_key_in_a_language_table_is_rejected(tmp_path: Path) -> None:
    text = (DEMO_DIR / "cover_letter_base.toml").read_text(encoding="utf-8")
    path = tmp_path / "cover_letter_base.toml"
    path.write_text(text.replace("[ca]", '[ca]\nbogus = "x"', 1), encoding="utf-8")
    with pytest.raises(ProfileError, match="bogus"):
        load_letter_bases(path)


# --- salutation, register, language selection ---------------------------------------


def test_salutation_by_register_language_and_company() -> None:
    assert render_salutation(ES_BASE, "es", "Acme", "cercano") == "Hola equipo de Acme,"
    assert (
        render_salutation(ES_BASE, "es", "Acme", "formal")
        == "Estimado equipo de selección de Acme:"
    )
    assert render_salutation(ES_BASE, "es", "", "cercano") == "Hola equipo,"
    assert (
        render_salutation(ES_BASE, "es", "(unknown company)", "formal")
        == "Estimado equipo de selección:"
    )
    assert render_salutation(EN_BASE, "en", "Acme", "cercano") == "Dear Acme team,"
    assert render_salutation(EN_BASE, "en", "", "cercano") == "Dear hiring team,"


def test_catalan_salutation_elides_before_a_vowel() -> None:
    assert render_salutation(CA_BASE, "ca", "Acme", "cercano") == "Hola equip d'Acme,"
    assert render_salutation(CA_BASE, "ca", "Foo", "cercano") == "Hola equip de Foo,"
    assert (
        render_salutation(CA_BASE, "ca", "Foo", "formal")
        == "Benvolgut equip de selecció de Foo:"
    )


def test_language_picks_its_own_base_text() -> None:
    es = _letter(ES_PARTS, ES_BASE, "es")
    en = _letter(EN_PARTS, EN_BASE, "en")
    ca = _letter(ES_PARTS, CA_BASE, "ca")
    assert ES_BASE.paragraph1 in es and EN_BASE.paragraph1 not in es
    assert EN_BASE.paragraph1 in en and en.endswith("Best regards,\nAlex Rivera")
    assert CA_BASE.paragraph1 in ca and ca.startswith("Hola equip d'Acme,")


def test_register_normalisation_only_trusts_formal() -> None:
    assert normalize_register("formal") == "formal"
    assert normalize_register(" Formal ") == "formal"
    for junk in ("cercano", "", "casual", "FORMAL!!", "usted"):
        assert normalize_register(junk) == "cercano"


def test_formal_register_switches_salutation_and_closing() -> None:
    formal = _letter({**ES_PARTS, "tone": "formal"}, ES_BASE, "es")
    plain = _letter(ES_PARTS, ES_BASE, "es")
    assert formal.startswith("Estimado equipo de selección de Acme:")
    assert ES_BASE.closing_formal in formal and ES_BASE.closing not in formal
    assert plain.startswith("Hola equipo de Acme,")
    assert ES_BASE.closing in plain


# --- assembly keeps the base text byte-identical ------------------------------------


def test_assembly_order_and_fixed_text_are_byte_identical() -> None:
    letter = _letter(ES_PARTS, ES_BASE, "es")
    paragraphs = letter.split("\n\n")
    assert paragraphs[0] == "Hola equipo de Acme,"
    assert paragraphs[1] == f"{ES_PARTS['linking']} {ES_BASE.paragraph1}"
    assert paragraphs[2] == ES_PARTS["why_company"]
    assert paragraphs[3] == ES_PARTS["why_role"]
    assert paragraphs[4] == ES_BASE.closing
    assert paragraphs[5] == ES_BASE.signoff == "Un saludo,\nAlex Rivera"
    assert len(paragraphs) == 6


def test_generated_line_breaks_cannot_split_the_paragraphs() -> None:
    parts = {**ES_PARTS, "why_company": ES_PARTS["why_company"].replace(". ", ".\n\n")}
    assert len(_letter(parts, ES_BASE, "es").split("\n\n")) == 6


def test_sample_letters_are_valid_and_inside_the_length_bounds() -> None:
    for parts, base, language in ((ES_PARTS, ES_BASE, "es"), (EN_PARTS, EN_BASE, "en")):
        letter = _letter(parts, base, language)
        assert LETTER_MIN_WORDS <= len(letter.split()) <= LETTER_MAX_WORDS
        assert (
            validate_parts(
                LetterParts(**parts), base, language=language, company="Acme"
            )
            == []
        )
        assert len(parts["linking"].split()) <= 25


# --- validation ---------------------------------------------------------------------


def _problems(language="es", base=ES_BASE, **over: str) -> list[str]:
    src = ES_PARTS if language == "es" else EN_PARTS
    return validate_parts(
        LetterParts(**{**src, **over}), base, language=language, company="Acme"
    )


def test_empty_parts_are_reported_not_raised() -> None:
    problems = validate_parts(LetterParts(), ES_BASE, language="es", company="Acme")
    assert len(problems) == 3 and all("falta" in p for p in problems)


def test_too_short_letter_is_flagged() -> None:
    problems = _problems(
        why_company="Acme hace cosas. Es mi terreno.", why_role="Pido Python."
    )
    # beta.8: the minimum dropped from 250 to 150 words (the reader skims).
    assert any("palabras" in p and "150" in p for p in problems)


def test_too_long_letter_is_flagged() -> None:
    problems = _problems(
        why_role=ES_PARTS["why_role"] + " " + ES_PARTS["why_company"] * 3
    )
    assert any("la carta completa" in p for p in problems)


def test_linking_must_be_one_short_sentence() -> None:
    assert any(
        "una sola frase" in p for p in _problems(linking="Hola. Qué tal estáis.")
    )
    long = " ".join(["palabra"] * 40) + "."
    assert any("frase de enlace tiene" in p for p in _problems(linking=long))


def test_paragraph_budgets_are_checked() -> None:
    assert any("párrafo 2 tiene" in p for p in _problems(why_company="Es mi terreno."))
    assert any("párrafo 3 tiene" in p for p in _problems(why_role="Pido Python. " * 2))


@pytest.mark.parametrize("filler", ["empresa líder", "cultura"])
def test_generic_filler_is_flagged(filler: str) -> None:
    problems = _problems(
        why_company=ES_PARTS["why_company"] + f" Admiro vuestra {filler}."
    )
    assert any("relleno" in p for p in problems)


def test_banned_phrases_and_dashes_are_checked_on_generated_parts_only() -> None:
    assert any(
        "prohibida" in p
        for p in _problems(why_company=ES_PARTS["why_company"] + " Soy proactivo.")
    )
    assert any("guion largo" in p for p in _problems(linking="Vi Acme — y me gustó."))
    # The base is the owner's approved text: never policed, even if it breaks a rule.
    risky = LetterBase(**{**ES_BASE.__dict__, "paragraph1": "Soy proactivo — siempre."})
    assert validate_parts(
        LetterParts(**ES_PARTS), risky, language="es", company="Acme"
    ) == [
        p
        for p in validate_parts(
            LetterParts(**ES_PARTS), risky, language="es", company="Acme"
        )
        if "palabras" in p
    ]


def test_formal_uses_ustedes_and_cercano_uses_vosotros() -> None:
    assert any(
        "ustedes" in p
        for p in _problems(tone="formal")  # fixture says "con vosotros"
    )
    formal_ok = {
        "tone": "formal",
        "why_role": ES_PARTS["why_role"].replace("con vosotros", "con ustedes"),
    }
    assert not any("registro" in p for p in _problems(**formal_ok))
    assert any(
        "vosotros" in p
        for p in _problems(
            why_role=ES_PARTS["why_role"].replace("con vosotros", "con ustedes")
        )
    )


def test_tone_checks_still_apply_to_generated_parts() -> None:
    assert any(
        "no solo" in p
        for p in _problems(
            why_company=ES_PARTS["why_company"] + " No solo sé RAG sino medir."
        )
    )
    assert any(
        "apertura genérica" in p
        for p in _problems(linking="Les escribo para postularme.")
    )


# --- prompt -------------------------------------------------------------------------


def _prompt(facts=(), findings=(), language="es", base=ES_BASE):
    return build_letter_parts_prompt(
        offer_text="Buscamos AI Engineer en Valencia. Trabajarás con RAG.",
        title="AI Engineer",
        company="Acme",
        language=language,
        branch="AI",
        claims_digest=build_claims_digest(CLAIMS),
        apply_findings=list(findings),
        voice_sample="Ya tenéis mi CV.",
        base=base,
        facts=list(facts),
    )


def test_prompt_shows_facts_with_sources_and_the_fixed_text() -> None:
    system, user = _prompt([ACME_FACT])
    assert "Acme desarrolla agentes de IA para logística." in user
    assert "https://acme.example.com/about" in user
    assert ES_BASE.paragraph1 in user and ES_BASE.closing in user
    assert "<company_facts>" in user and "<offer>" in user
    assert "UNTRUSTED" in system and "linking" in system and "why_role" in system
    assert "250" in user or "Word budget" in user


def test_prompt_without_facts_tells_the_model_not_to_invent() -> None:
    _system, user = _prompt([])
    assert "none found" in user and "only from the offer" in user


def test_prompt_keeps_the_apply_instruction_findings() -> None:
    finding = ApplyInstruction(
        kind="cover_letter", label="Carta", snippet="adjunta una carta"
    )
    _system, user = _prompt(findings=[finding])
    assert 'cover_letter: "adjunta una carta"' in user


def test_prompt_keeps_the_guards_and_the_persona_rules() -> None:
    system, _user = _prompt()
    assert "me apasiona" in system  # banned phrases listed
    assert "Never invent" in system and "Backtrack test" in system
    # beta.8 budgets (shorter letters): linking <= 20, P2 25-45, P3 25-50.
    assert "ONE sentence of at most 20 words" in system
    assert "25-45 words" in system and "25-50 words" in system
    assert "career change" in system or "systems administration" in system


# --- generation ---------------------------------------------------------------------


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


def _generate(llm: _Llm, *, facts=(), language="es", base=ES_BASE, review=False):
    return asyncio.run(
        generate_cover_letter(
            llm,  # type: ignore[arg-type]
            offer_text="Buscamos AI Engineer en Valencia.",
            title="AI Engineer",
            company="Acme",
            language=language,
            branch="AI",
            claims_digest=build_claims_digest(CLAIMS),
            apply_findings=[],
            voice_sample="",
            base=base,
            facts=list(facts),
            review=review,
        )
    )


def test_generate_assembles_one_letter_with_one_call() -> None:
    llm = _Llm(ES_PARTS)
    result = _generate(llm)
    assert result.text == _letter(ES_PARTS, ES_BASE, "es")
    assert result.warnings == [] and llm.users.__len__() == 1
    assert result.register == "cercano" and result.thin_company is True


def test_generate_english_uses_the_english_base() -> None:
    result = _generate(_Llm(EN_PARTS), language="en", base=EN_BASE)
    assert result.text.startswith("Dear Acme team,")
    assert EN_BASE.paragraph1 in result.text and result.warnings == []


def test_generate_formal_register_comes_from_the_model() -> None:
    parts = {
        **ES_PARTS,
        "tone": "formal",
        "why_role": ES_PARTS["why_role"].replace("con vosotros", "con ustedes"),
    }
    result = _generate(_Llm(parts))
    assert result.register == "formal"
    assert result.text.startswith("Estimado equipo de selección de Acme:")
    assert result.warnings == []


def test_generate_reports_facts_and_thin_flag() -> None:
    with_facts = _generate(_Llm(ES_PARTS), facts=[ACME_FACT])
    assert with_facts.facts == [ACME_FACT] and with_facts.thin_company is False
    assert _generate(_Llm(ES_PARTS)).thin_company is True


def test_generate_retries_once_with_problems_listed() -> None:
    bad = {**ES_PARTS, "why_company": "Acme es mi terreno."}
    llm = _Llm(bad, ES_PARTS)
    result = _generate(llm)
    assert len(llm.users) == 2 and "previous answer had these problems" in llm.users[1]
    assert "párrafo 2 tiene" in llm.users[1]
    assert result.warnings == [] and result.text == _letter(ES_PARTS, ES_BASE, "es")


def test_generate_warns_when_the_retry_still_fails_and_never_retries_twice() -> None:
    bad = {**ES_PARTS, "why_company": "Acme es mi terreno."}
    llm = _Llm(bad, bad)
    result = _generate(llm)
    assert len(llm.users) == 2 and result.warnings
    assert ES_BASE.paragraph1 in result.text  # still a letter, with a warning


def test_generate_keeps_the_first_parts_when_the_retry_call_fails() -> None:
    bad = {**ES_PARTS, "why_company": "Acme es mi terreno."}
    result = _generate(_Llm(bad, LlmResponseError("boom")))
    assert result.warnings and "Acme es mi terreno." in result.text


def test_generate_first_call_failure_propagates() -> None:
    with pytest.raises(LlmResponseError):
        _generate(_Llm(LlmResponseError("boom")))


def test_empty_model_answer_degrades_to_a_warning_not_an_exception() -> None:
    result = _generate(_Llm({}, {}))
    assert result.warnings and ES_BASE.paragraph1 in result.text


# --- reviewer pass: only the generated parts, fixed text untouched --------------------


def test_review_is_off_by_default() -> None:
    llm = _Llm(ES_PARTS)
    _generate(llm)
    assert len(llm.users) == 1


def test_review_replaces_the_parts_with_a_passing_revision() -> None:
    revised = {**ES_PARTS, "linking": ES_PARTS["linking"].replace("Vi que", "Leí que")}
    llm = _Llm(ES_PARTS, revised)
    result = _generate(llm, review=True)
    assert len(llm.users) == 2 and "REVIEW TASK" in llm.users[1]
    assert "Leí que en Acme" in result.text and result.warnings == []


def test_reviewer_cannot_alter_the_fixed_text_or_the_register() -> None:
    sneaky = {
        **ES_PARTS,
        "tone": "formal",  # the register was decided by the first answer
        "linking": ES_PARTS["linking"].replace("Vi que", "Leí que"),
    }
    result = _generate(_Llm(ES_PARTS, sneaky), review=True)
    assert result.register == "cercano"
    assert result.text.startswith("Hola equipo de Acme,")
    paragraphs = result.text.split("\n\n")
    assert paragraphs[1].endswith(ES_BASE.paragraph1)
    assert ES_BASE.paragraph1 in result.text
    assert paragraphs[4] == ES_BASE.closing and paragraphs[5] == ES_BASE.signoff


def test_reviewer_only_sees_the_generated_parts_as_editable() -> None:
    llm = _Llm(ES_PARTS, ES_PARTS)
    _generate(llm, review=True)
    assert "ONLY these parts" in llm.users[1]
    assert llm.users[1].count(ES_BASE.paragraph1) == 1  # shown once, as context


def test_review_keeps_the_original_when_the_revision_fails_validation() -> None:
    bad = {**ES_PARTS, "why_company": "Acme es mi terreno."}
    result = _generate(_Llm(ES_PARTS, bad), review=True)
    assert result.text == _letter(ES_PARTS, ES_BASE, "es") and result.warnings == []


def test_review_error_keeps_the_original_and_never_raises() -> None:
    for error in (LlmResponseError("boom"), ConnectionError("down")):
        result = _generate(_Llm(ES_PARTS, error), review=True)
        assert result.text == _letter(ES_PARTS, ES_BASE, "es")


# --- company key normalisation ------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme", "  acme "),
        ("Acme S.L.", "ACME"),
        ("Éxito, Inc.", "exito"),
        ("Foo-Bar", "foo bar"),
    ],
)
def test_company_key_is_normalised(a: str, b: str) -> None:
    assert normalize_company(a) == normalize_company(b)


def test_different_companies_have_different_keys() -> None:
    assert normalize_company("Acme") != normalize_company("Acme Labs")


def _words(n: int) -> str:
    return " ".join(["dato"] * (n - 1)) + " final."


@pytest.mark.parametrize(
    ("field", "count", "flagged"),
    [
        # beta.8 budgets: paragraph 2 25-45, paragraph 3 25-50 words.
        ("why_company", 24, True),
        ("why_company", 25, False),
        ("why_company", 45, False),
        ("why_company", 46, True),
        ("why_role", 24, True),
        ("why_role", 50, False),
        ("why_role", 51, True),
    ],
)
def test_paragraph_budgets_are_exact_with_no_hidden_margin(
    field: str, count: int, flagged: bool
) -> None:
    label = "párrafo 2 tiene" if field == "why_company" else "párrafo 3 tiene"
    problems = _problems(**{field: _words(count)})
    assert any(label in p for p in problems) is flagged


# beta.8: the linking sentence budget dropped from 25 to 20 words.
@pytest.mark.parametrize(("count", "flagged"), [(20, False), (21, True)])
def test_linking_budget_is_exact_with_no_hidden_margin(
    count: int, flagged: bool
) -> None:
    problems = _problems(linking=_words(count))
    assert any("frase de enlace tiene" in p for p in problems) is flagged


def _letter_with_total(total: int) -> list[str]:
    # beta.8: 149 words is shorter than the fixture, so both paragraphs flex.
    empty = {**ES_PARTS, "why_company": "", "why_role": ""}
    rest = total - len(_letter(empty, ES_BASE, "es").split())
    return _problems(why_company=_words(rest // 2), why_role=_words(rest - rest // 2))


@pytest.mark.parametrize(
    # beta.8: the whole letter is 150-250 words (was 250-350).
    ("total", "flagged"),
    [(149, True), (150, False), (250, False), (251, True)],
)
def test_letter_total_is_exact_with_no_hidden_margin(total: int, flagged: bool) -> None:
    assert any("la carta completa" in p for p in _letter_with_total(total)) is flagged
