"""Pre-scoring gates: eligibility and required languages (pure, deterministic)."""

import pytest

from cva.match.gates import (
    GateResult,
    eligibility_gate,
    language_gate,
    run_gates,
)

# --- eligibility ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Candidates must be a U.S. citizen to apply.",
        "Requirements: US citizen or green card holder.",
        "You must hold a green card.",
        "Must be authorized to work in the US without sponsorship.",
        "Applicants should be legally authorised to work in the United Kingdom.",
        "UK right to work is required.",
        "Candidates must have the right to work in the UK.",
        "A Swiss work permit is required.",
        "You must reside in the United States.",
        "Must be based in Canada.",
        "Se requiere ciudadanía estadounidense.",
        "Imprescindible residir en Reino Unido.",
        "Permiso de trabajo en EEUU obligatorio.",
    ],
)
def test_eligibility_fails_on_non_eu_requirement(text: str) -> None:
    result = eligibility_gate(f"Software Engineer at Acme. {text} Great team.")
    assert result.gate == "eligibility"
    assert result.verdict == "FAIL"
    assert result.reason
    assert result.evidence


@pytest.mark.parametrize(
    "text",
    [
        "Security clearance required.",
        "Se requiere habilitación de seguridad.",
        "Debe poseer HPS en vigor.",
        "Active NATO secret clearance.",
    ],
)
def test_eligibility_flags_security_clearance(text: str) -> None:
    result = eligibility_gate(f"Engineer. {text}")
    assert result.verdict == "FLAG"
    assert result.evidence


@pytest.mark.parametrize(
    "text",
    [
        "Open to EU citizens.",
        "Se requiere nacionalidad española o permiso de trabajo en España.",
        "Must have the right to work in the EU.",
        "Ciudadano de la Unión Europea.",
        "",
        "Backend developer in Barcelona. Python and PostgreSQL.",
        "Work with us and grow. We are a US company with offices in Barcelona.",
    ],
)
def test_eligibility_passes_on_eu_wording_or_silence(text: str) -> None:
    result = eligibility_gate(text)
    assert result.verdict == "PASS"


def test_eligibility_mixed_eu_and_non_eu_is_flag_not_fail() -> None:
    text = "Must be authorized to work in the UK or the EU."
    assert eligibility_gate(text).verdict == "FLAG"


def test_eligibility_evidence_is_trimmed() -> None:
    text = "x " * 200 + "Must be a U.S. citizen. " + "y " * 200
    result = eligibility_gate(text)
    assert result.verdict == "FAIL"
    assert "U.S. citizen" in result.evidence
    assert len(result.evidence) <= 200


# --- languages -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Alemán imprescindible.", "alemán"),
        ("Idiomas: inglés y francés obligatorio.", "francés"),
        ("Fluent German is required.", "alemán"),
        ("You must speak French.", "francés"),
        ("Nivel C1 de italiano requerido.", "italiano"),
        ("Portuguese fluency is essential for this role.", "portugués"),
        ("Dutch required (native level).", "neerlandés"),
        ("Domini de l'alemany indispensable.", "alemán"),
        ("Es imprescindible parlar francès amb fluïdesa.", "francés"),
        ("Se requiere nivel nativo de polaco.", "polaco"),
        ("Mandarin Chinese language skills required.", "chino"),
        ("Japanese (JLPT N2) language level required.", "japonés"),
        ("Russian: native level required.", "ruso"),
        ("Inglés y alemán C1 requeridos.", "alemán"),
        ("Idioma alemán obligatorio, inglés valorable.", "alemán"),
    ],
)
def test_language_fails_on_mandatory_foreign_language(text: str, language: str) -> None:
    result = language_gate(f"Developer role in a startup. {text} Other things.")
    assert result.gate == "language"
    assert result.verdict == "FAIL", result
    assert language in result.reason
    assert result.evidence


@pytest.mark.parametrize(
    "text",
    [
        "Alemán valorable.",
        "Se valorará conocimiento de francés.",
        "German is a plus.",
        "Nice to have: French language skills.",
        "Idioma alemán deseable.",
        "Preferred languages: Spanish, English, Italian speaker is a bonus.",
        "Es valora el coneixement d'alemany.",
        "English required, German a plus.",
        "Inglés C1 y alemán valorable.",
    ],
)
def test_language_flags_desirable_foreign_language(text: str) -> None:
    result = language_gate(f"Developer role. {text}")
    assert result.verdict == "FLAG", result
    assert result.evidence


def test_language_flags_ambiguous_mention() -> None:
    result = language_gate("Languages: Spanish, English, German.")
    assert result.verdict == "FLAG"
    assert "alemán" in result.reason


def test_language_flags_english_above_c1() -> None:
    for text in [
        "English C2 required.",
        "Native English speaker needed.",
        "Inglés nativo imprescindible.",
        "Bilingual English and Spanish.",
        "Nivell C2 d'anglès.",
    ]:
        result = language_gate(f"Role. {text}")
        assert result.verdict == "FLAG", text
        assert "inglés" in result.reason


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Spanish and Catalan native, English C1 required.",
        "Castellano, catalán e inglés C1 imprescindible.",
        "Fluent English is required.",
        "Java and Go developer, Python required.",
        "Join French Tech Barcelona, our community.",
        "Contact German Garcia for details.",
        "We are a subsidiary of Deutsche Bank.",
        "The Chinese market is growing fast.",
        "Office in Paris and Berlin. Italian food on Fridays.",
        "Backend Python. Greek yogurt in the fridge.",
        "Swift and Kotlin experience required.",
    ],
)
def test_language_passes_without_foreign_language_requirement(text: str) -> None:
    result = language_gate(text)
    assert result.verdict == "PASS", result


def test_language_worst_verdict_wins_and_lists_languages() -> None:
    text = "Alemán y francés imprescindibles. Italiano valorable."
    result = language_gate(text)
    assert result.verdict == "FAIL"
    assert "alemán" in result.reason and "francés" in result.reason
    assert "italiano" not in result.reason


def test_language_heading_carries_context_to_bullets() -> None:
    text = "Idiomas:\n- Alemán C1\n- Inglés C1"
    assert language_gate(text).verdict == "FAIL"


def test_language_evidence_is_trimmed_and_quoted_from_offer() -> None:
    text = "blah " * 100 + "Alemán imprescindible." + " blah" * 100
    result = language_gate(text)
    assert result.verdict == "FAIL"
    assert "Alemán imprescindible" in result.evidence
    assert len(result.evidence) <= 200


# --- run_gates -----------------------------------------------------------------


def test_run_gates_returns_all_results_in_order() -> None:
    results = run_gates("Plain offer.")
    assert [r.gate for r in results] == ["eligibility", "language", "location"]
    assert all(isinstance(r, GateResult) and r.verdict == "PASS" for r in results)


# --- T1b: clause-aware negation, alias collisions, snippet centring -----------


@pytest.mark.parametrize(
    "text",
    [
        "You don't need to live in the US to apply.",
        "You do not need to be a US citizen.",
        "Candidates aren't required to hold a green card.",
        "This role doesn't require UK right to work.",
        "No es necesario residir en Reino Unido.",
        "No necesitas permiso de trabajo en EE.UU. para aplicar.",
        "No hace falta ser ciudadano estadounidense.",
        "No se requiere permiso de trabajo en Estados Unidos.",
        "No cal residir al Regne Unit; no cal permiso de trabajo en EE.UU.",
        "Not required to hold a security clearance.",
        "You won't need a security clearance for this job.",
        "No need for security clearance.",
        "Non-US citizens are welcome to apply.",
        "No security clearance required.",
        "No se requiere alemán ni permiso de trabajo en EE.UU.",
    ],
)
def test_eligibility_clause_aware_negation_downgrades_to_flag(text: str) -> None:
    assert eligibility_gate(text).verdict == "FLAG"


def test_eligibility_negation_does_not_leak_across_sentences() -> None:
    text = "You don't need a degree. You must be a U.S. citizen."
    assert eligibility_gate(text).verdict == "FAIL"


@pytest.mark.parametrize(
    "text",
    [
        "Con fines de formación, se requiere nivel B2 de inglés.",
        "A fines de mes se requiere nivel B2 de inglés.",
        "Fines of up to 10% apply; English B2 is required.",
    ],
)
def test_language_fines_is_not_finnish(text: str) -> None:
    assert language_gate(text).verdict == "PASS"


def test_language_finnish_is_still_detected() -> None:
    assert language_gate("Finnish is required.").verdict == "FAIL"
    assert language_gate("Se requiere finlandés (nivel C1).").verdict == "FAIL"


def test_snippet_is_centred_on_a_long_match_near_the_start() -> None:
    from cva.match.gates import _snippet

    text = "ab" + "M" * 100 + "z" * 400
    snippet = _snippet(text, 2, 102)
    assert len(snippet) <= 160
    assert "M" * 100 in snippet
    assert snippet.startswith("ab")


# --- T2b/T4b: a negation only downgrades a hit to FLAG, never to PASS ----------


@pytest.mark.parametrize(
    "text",
    [
        "Visa sponsorship is not available, applicants must be U.S. citizens.",
        "Please do not apply without a green card, you must be a US citizen.",
    ],
)
def test_eligibility_unrelated_negation_never_passes(text: str) -> None:
    assert eligibility_gate(text).verdict == "FLAG"


@pytest.mark.parametrize(
    "text",
    [
        "Please do not apply unless you are a US citizen.",
        "No apliques a menos que seas ciudadano estadounidense.",
    ],
)
def test_eligibility_unless_clause_is_a_requirement(text: str) -> None:
    assert eligibility_gate(text).verdict == "FAIL"


def test_eligibility_negation_followed_by_a_but_clause_is_never_pass() -> None:
    text = "You don't need sponsorship, but you must be a U.S. citizen."
    assert eligibility_gate(text).verdict in {"FLAG", "FAIL"}
    assert eligibility_gate(text).verdict != "PASS"


@pytest.mark.parametrize(
    "text",
    [
        "There is no requirement that you are a US citizen.",
        "There is no requirement that you be a U.S. citizen.",
        "It is not required that you are a US citizen to apply.",
        "No es requisito ser ciudadano estadounidense.",
        "No es necesario ser ciudadano estadounidense.",
    ],
)
def test_eligibility_negated_requirement_phrasing_is_flag(text: str) -> None:
    assert eligibility_gate(text).verdict == "FLAG"


@pytest.mark.parametrize(
    "text",
    [
        "There is no requirement for sponsorship, you are a US citizen.",
        "There is no requirement; applicants are US citizens only.",
        "No requirement that you are fluent, but candidates are US citizens.",
        "You are a US citizen.",
    ],
)
def test_eligibility_us_citizen_wording_is_never_pass(text: str) -> None:
    assert eligibility_gate(text).verdict in {"FLAG", "FAIL"}


@pytest.mark.parametrize(
    "text",
    [
        "It is not required that you relocate since candidates are US citizens only.",
        "It is not required that you relocate since candidates are U.S. citizens only.",
    ],
)
def test_eligibility_negation_followed_by_real_requirement_is_flag(text: str) -> None:
    assert eligibility_gate(text).verdict == "FLAG"


@pytest.mark.parametrize(
    "text",
    ["No se requiere alemán.", "German is not required.", "Alemán no necesario."],
)
def test_language_negated_requirement_is_flag_not_pass(text: str) -> None:
    assert language_gate(text).verdict == "FLAG"
