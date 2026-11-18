"""Apply-instruction detector (pure, regex-based; ES/EN/CA phrasing)."""

import pytest

from cva.match.apply_instructions import (
    MAX_FINDINGS,
    ApplyInstruction,
    detect_apply_instructions,
)


def _kinds(text: str) -> list[str]:
    return [f.kind for f in detect_apply_instructions(text)]


def _one(text: str, kind: str) -> ApplyInstruction:
    found = [f for f in detect_apply_instructions(text) if f.kind == kind]
    assert len(found) == 1, found
    return found[0]


def test_apply_email_with_value_and_snippet() -> None:
    text = "Buscamos un perfil data. Si te encaja, remite tu CV a talent@acme-data.example y te contamos."
    finding = _one(text, "email")
    assert finding.value == "talent@acme-data.example"
    assert "remite tu CV a talent@acme-data.example" in finding.snippet
    assert finding.label


def test_cosmo_start_date_and_direct_online() -> None:
    text = (
        "Apply directly online with your informative application documents "
        "and your earliest possible start date."
    )
    kinds = _kinds(text)
    assert "start_date" in kinds
    assert "external_apply" in kinds


def test_linkedin_managed_outside() -> None:
    assert _kinds("Respuestas gestionadas fuera de LinkedIn") == ["external_apply"]
    assert _kinds("Solicitar en el sitio web de la empresa") == ["external_apply"]
    assert _kinds("Apply on company website") == ["external_apply"]


def test_cover_letter_and_reference_code() -> None:
    text = "Envíanos tu CV y carta de presentación indicando la referencia REF-2026-14."
    kinds = _kinds(text)
    assert "cover_letter" in kinds
    assert _one(text, "reference_code").value == "REF-2026-14"


def test_salary_expectations() -> None:
    assert _kinds("Please include your salary expectations") == ["salary_expectations"]
    assert _kinds("Indica tus pretensiones salariales") == ["salary_expectations"]


def test_catalan_email_and_cover_letter() -> None:
    text = "Envia el teu CV i carta de presentació a feina@empresa.cat"
    assert sorted(_kinds(text)) == ["cover_letter", "email"]
    assert _one(text, "email").value == "feina@empresa.cat"


def test_phone_needs_contact_context() -> None:
    assert _one("Llámanos al 612 345 678 para más info", "phone").value == "612345678"
    assert (
        _one("Contact us on WhatsApp +34 612-345-678", "phone").value == "+34612345678"
    )
    assert _kinds("Presupuesto de 612 345 678 euros") == []


def test_portfolio_asked() -> None:
    assert _kinds("Adjunta tu portfolio o GitHub") == ["portfolio"]
    assert _kinds("Please share a link to your GitHub profile") == ["portfolio"]


def test_start_date_variants() -> None:
    assert _kinds("Indica tu fecha de incorporación") == ["start_date"]
    assert _kinds("Please state your notice period") == ["start_date"]
    assert _kinds("Indica la teva disponibilitat d'incorporació") == ["start_date"]


def test_dedupe_and_cap() -> None:
    text = "Escribe a a@x.es, a@x.es, b@x.es, c@x.es, d@x.es, e@x.es, f@x.es, g@x.es"
    emails = [f.value for f in detect_apply_instructions(text)]
    assert len(emails) == len(set(emails))
    assert len(emails) <= MAX_FINDINGS


def test_snippet_is_collapsed_and_truncated() -> None:
    text = "x " * 200 + "remite\n\n   tu   CV a   a@b.es " + "y " * 200
    snippet = _one(text, "email").snippet
    assert "\n" not in snippet and "  " not in snippet
    assert len(snippet) <= 125
    assert snippet.startswith("…") and snippet.endswith("…")


@pytest.mark.parametrize(
    "text",
    [
        "Requisitos: Python, email marketing, SEO.",
        "Experiencia con Git/GitHub y CI/CD.",
        "Responsibilities include CI/CD with GitHub Actions.",
        "We have high expectations of the role and the team.",
        "Disponibilidad para viajar 20%.",
        "Protección de datos: escríbenos a privacy@company.com (RGPD).",
        "Contacto DPO: dpo@company.com / noreply@company.com",
        "No cover letter needed.",
        "",
    ],
)
def test_negatives_do_not_warn(text: str) -> None:
    assert detect_apply_instructions(text) == []


def test_deterministic() -> None:
    text = "Remite tu CV a a@b.es con carta de presentación"
    assert detect_apply_instructions(text) == detect_apply_instructions(text)


def test_linkedin_premium_ui_cover_letter_button_is_not_a_request() -> None:
    """A pasted LinkedIn page carries Premium UI chrome ("Tailor my resume
    Help me stand out Create cover letter BETA"); it is not the offer asking
    for a cover letter."""
    text = (
        "Am I a good fit for this job? Tailor my resume Help me stand out "
        "Create cover letter BETA • Is this information helpful? "
        "About the job: we build data platforms with Python and SQL."
    )
    assert [f.kind for f in detect_apply_instructions(text)] == []
    es = "Ayúdame a destacar Crear carta de presentación BETA. Buscamos Python."
    assert [f.kind for f in detect_apply_instructions(es)] == []


def test_professional_references_are_not_a_reference_code() -> None:
    text = "Please include two professional references with your application."
    assert "reference_code" not in [f.kind for f in detect_apply_instructions(text)]


def test_reference_code_negated_is_ignored() -> None:
    text = "No hace falta indicar la referencia REF-2026-14 en el asunto."
    assert "reference_code" not in [f.kind for f in detect_apply_instructions(text)]


def test_plain_contact_email_is_not_labelled_as_send_cv() -> None:
    findings = detect_apply_instructions("Más información. Email: info@empresa.com")
    assert [f.kind for f in findings] == ["email"]
    assert "Enviar el CV" not in findings[0].label
    sent = detect_apply_instructions("Envía tu CV a jobs@empresa.com")
    assert "Enviar el CV" in sent[0].label


def test_many_emails_do_not_crowd_out_other_kinds() -> None:
    emails = " ".join(f"Escríbenos a team{i}@empresa.com." for i in range(8))
    text = emails + " Indica tu fecha de incorporación y adjunta carta de presentación."
    kinds = [f.kind for f in detect_apply_instructions(text)]
    assert "start_date" in kinds and "cover_letter" in kinds
