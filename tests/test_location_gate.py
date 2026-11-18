"""Location gate: on-site/hybrid outside Catalonia → FAIL; unclear → FLAG."""

import pytest

from cva.match.gates import location_gate, run_gates

SANT_ANDREU = (
    "Ingeniero/a de IA en Acme. Modalidad híbrida (3 días en oficina) en "
    "Sant Andreu de la Barca. Python, LLMs."
)


def test_hybrid_in_sant_andreu_de_la_barca_passes() -> None:
    assert location_gate(SANT_ANDREU, None).verdict == "PASS"
    assert (
        location_gate("Ingeniero de IA. Híbrido.", "Sant Andreu de la Barca").verdict
        == "PASS"
    )


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("Puesto presencial en Madrid. Python.", None),
        ("Python engineer. Presencial.", "Madrid"),
        ("Modalidad híbrida, oficina en Valencia.", None),
        ("Ingeniero de datos. Híbrido (2 días en oficina).", "Valencia, Valencia"),
        ("On-site role in London.", None),
        ("Hybrid, 3 days in the office.", "Berlin, Germany"),
        ("Trabajo presencial. Ubicación: Bilbao.", None),
        ("Backend developer.", "Sevilla"),  # no remote wording at all + outside
    ],
)
def test_onsite_or_hybrid_outside_catalonia_fails(
    text: str, location: str | None
) -> None:
    result = location_gate(text, location)
    assert result.gate == "location"
    assert result.verdict == "FAIL"
    assert result.reason.startswith(("Presencial", "Híbrido", "Sin teletrabajo"))
    assert "fuera de Cataluña" in result.reason
    assert result.evidence


def test_fail_reason_names_the_place() -> None:
    result = location_gate("Puesto presencial.", "Madrid")
    assert result.reason == "Presencial fuera de Cataluña (Madrid)"
    assert (
        location_gate("Híbrido.", "Valencia").reason
        == "Híbrido fuera de Cataluña (Valencia)"
    )


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("100% remoto. Empresa con sede en Madrid.", None),
        ("Puesto 100% remoto desde España. Equipo en Madrid y Valencia.", "Madrid"),
        ("Fully remote (Spain or EU).", "Madrid, Spain"),
        ("Teletrabajo total. Python.", "Sevilla"),
        ("Work from home. Offices in London.", "London"),
        ("Empresa con sede en Madrid. Puesto remoto.", None),
        ("Treball en remot 100%.", "Madrid"),
    ],
)
def test_remote_passes_even_with_outside_places(
    text: str, location: str | None
) -> None:
    assert location_gate(text, location).verdict == "PASS"


def test_remote_in_location_field_counts() -> None:
    assert location_gate("Python engineer.", "Remoto").verdict == "PASS"
    assert location_gate("Python engineer.", "Teletrabajo, Madrid").verdict == "PASS"


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("Presencial en Barcelona.", None),
        ("Híbrido, oficina en Sabadell.", None),
        ("Presencial.", "Terrassa"),
        ("Presencial.", "Lleida"),
        ("Híbrido.", "Girona"),
        ("On-site.", "Reus, Tarragona"),
        ("Presencial en Vilanova i la Geltrú, Barcelona.", None),
        ("Híbrido en El Prat de Llobregat.", None),
        ("Presencial.", "Cataluña"),
        ("Presencial en Baix Llobregat.", None),
    ],
)
def test_catalan_locations_pass(text: str, location: str | None) -> None:
    assert location_gate(text, location).verdict == "PASS"


def test_madrid_or_barcelona_passes() -> None:
    assert location_gate("Puesto híbrido.", "Madrid o Barcelona").verdict == "PASS"
    assert location_gate("Híbrido en Madrid o Barcelona.", None).verdict == "PASS"
    assert location_gate("Presencial.", "Madrid / Barcelona").verdict == "PASS"
    assert location_gate("Presencial.", "Barcelona, Madrid").verdict == "PASS"


def test_spain_only_never_fails() -> None:
    assert location_gate("Puesto presencial.", "España").verdict == "FLAG"
    assert location_gate("Híbrido en España.", None).verdict == "FLAG"
    assert (
        location_gate("Python engineer.", "Spain").verdict == "PASS"
    )  # no mode, no place


def test_no_location_flags_only_when_mode_is_office() -> None:
    assert location_gate("Puesto presencial.", None).verdict == "FLAG"
    assert location_gate("Python engineer, LLMs.", None).verdict == "PASS"


def test_remote_with_monthly_visits_to_madrid_flags() -> None:
    text = "Puesto 100% remoto con visitas mensuales a la oficina de Madrid."
    result = location_gate(text, None)
    assert result.verdict == "FLAG"
    assert "Madrid" in result.reason
    assert "Madrid" in result.evidence
    english = "Fully remote. Monthly travel to our Madrid office is required."
    assert location_gate(english, None).verdict == "FLAG"
    assert (
        location_gate("Remoto. Una vez al mes en la oficina de Valencia.", None).verdict
        == "FLAG"
    )


def test_remote_visits_to_a_catalan_office_pass() -> None:
    text = "100% remoto con visitas mensuales a la oficina de Barcelona."
    assert location_gate(text, None).verdict == "PASS"


def test_several_locations_with_outside_flag_unless_alternatives() -> None:
    flag = location_gate("Híbrido en Barcelona. Oficina también en Madrid.", None)
    assert flag.verdict == "FLAG"
    assert "Madrid" in flag.reason


def test_headquarters_alone_is_weak_evidence_flag_not_fail() -> None:
    text = "No es posible el teletrabajo. Sede en Sevilla, puesto presencial."
    assert location_gate(text, None).verdict == "FLAG"


@pytest.mark.parametrize(
    "text",
    [
        "No remoto, presencial en Madrid.",
        "Sin teletrabajo. Oficina en Madrid.",
        "This role is not remote. Madrid office.",
        "Non-remote position in Valencia.",
    ],
)
def test_negated_remote_counts_as_not_remote(text: str) -> None:
    assert location_gate(text, None).verdict == "FAIL"


def test_remote_not_negated_by_unrelated_no() -> None:
    assert (
        location_gate(
            "No problem: 100% remoto desde cualquier lugar.", "Madrid"
        ).verdict
        == "PASS"
    )
    assert location_gate("Sin horarios fijos, full remote.", "Madrid").verdict == "PASS"


def test_negated_office_wording_is_remote() -> None:
    assert location_gate("100% remoto, sin presencialidad.", "Madrid").verdict == "PASS"
    assert location_gate("Remoto (no presencial).", "Madrid").verdict == "PASS"


# --- false-positive guards -----------------------------------------------------


def test_english_word_salt_is_not_a_place() -> None:
    text = "Presencial. We add salt to every dish; our sort algorithm is fast."
    assert location_gate(text, None).verdict == "FLAG"  # office mode, no place → FLAG
    assert (
        location_gate("Híbrido.", "Girona, Salt").verdict == "PASS"
    )  # structured Salt


def test_company_hq_mention_does_not_fail_remote_job() -> None:
    text = "Empresa con sede en Madrid. Puesto 100% remoto."
    assert location_gate(text, None).verdict == "PASS"


def test_company_hq_mention_is_ignored_for_office_jobs_too() -> None:
    text = "Fundada en Madrid, con sede en Valencia. Híbrido en Sabadell."
    assert location_gate(text, None).verdict == "PASS"


def test_valencia_in_client_list_with_barcelona_job_passes() -> None:
    text = (
        "Nuestros clientes: Valencia CF, Sevilla FC y Bilbao Athletic. "
        "Puesto híbrido en Barcelona."
    )
    assert location_gate(text, None).verdict == "PASS"
    assert location_gate(text, "Barcelona").verdict == "PASS"


def test_interview_wording_is_not_a_work_mode() -> None:
    text = "Entrevista presencial en nuestras oficinas. Puesto 100% remoto."
    assert location_gate(text, "Madrid").verdict == "PASS"
    assert (
        location_gate("Formación presencial inicial. Remoto.", "Sevilla").verdict
        == "PASS"
    )


def test_banco_santander_is_not_a_place() -> None:
    assert location_gate("Presencial en Banco Santander.", None).verdict == "FLAG"
    assert location_gate("Presencial.", "Santander, Cantabria").verdict == "FAIL"


def test_accents_case_and_spanish_spellings() -> None:
    assert location_gate("PRESENCIAL EN SAN CUGAT.", None).verdict == "PASS"
    assert location_gate("Presencial en Hospitalet.", None).verdict == "PASS"
    assert location_gate("presencial en MADRID", None).verdict == "FAIL"


def test_run_gates_includes_location_gate_last() -> None:
    results = run_gates("Presencial.", "Madrid")
    assert [r.gate for r in results] == ["eligibility", "language", "location"]
    assert results[2].verdict == "FAIL"
    assert [r.verdict for r in run_gates("Plain offer.")] == ["PASS", "PASS", "PASS"]


@pytest.mark.parametrize(
    "text",
    [
        "You will work remotely from anywhere in Spain.",
        "Puestos remotos para todo el equipo.",
        "Ofrecemos posiciones remotas.",
        "Trabajarás remotamente.",
        "Modalidad de teletrabajo flexible.",
    ],
)
def test_remote_inflections_are_remote(text: str) -> None:
    assert location_gate(text, "Madrid, Spain").verdict == "PASS"


def test_spanish_verb_usa_is_not_a_country() -> None:
    result = location_gate("Trabajo presencial donde se usa Python a diario.", None)
    assert result.verdict != "FAIL"


def test_usa_in_the_structured_location_still_counts() -> None:
    assert location_gate("Trabajo presencial.", "New York, USA").verdict == "FAIL"


@pytest.mark.parametrize(
    ("text", "location", "verdict"),
    [
        (
            "Fully remote. Experience with hybrid cloud and hybrid search.",
            "Madrid",
            "PASS",
        ),
        ("Puesto 100% remoto o híbrido.", "Madrid, Spain", "PASS"),
        ("Experiencia en hybrid search. Trabajo en remoto.", "Valencia", "PASS"),
        ("Experience with hybrid search (RAG). Trabajo presencial.", "Madrid", "FAIL"),
        ("Modalidad remota o híbrida.", "Madrid", "FLAG"),
    ],
)
def test_hybrid_wording_vs_remote(text: str, location: str, verdict: str) -> None:
    assert location_gate(text, location).verdict == verdict
