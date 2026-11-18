"""Language inference heuristic tests (job-matching spec, design D10).

Pure stopword-ratio heuristic: ES/EN/CA dominant snippets,
punctuation/case robustness, and the documented deterministic tie-break.
ES/CA disambiguation (v3 part B): CA-specific markers (i, amb, per,
també, això...) are not valid Spanish words, so two or more of them —
and more markers than EN stopwords — elect Catalan; everything else
falls through to the legacy ES/EN ratio with the ES tie-break.
"""

import pytest

from cva.match.language import infer_language

ES_OFFER = (
    "Buscamos un ingeniero de IA con experiencia en Python y modelos de "
    "lenguaje. Debes tener buen nivel de inglés y capacidad de trabajo en "
    "equipo."
)

EN_OFFER = (
    "We are looking for a senior AI engineer with strong experience in "
    "Python and LLMs to own our retrieval stack."
)

EN_DOMINANT_MIXED = (
    "Requisitos: you will work with the team that builds our platform, "
    "and you will own the roadmap for retrieval quality across products."
)

CA_OFFER = (
    "Busquem perfil per a desenvolupament de plataformes amb Python. "
    "També es valora experiència amb SQL i APIs REST, i treball en equip."
)

CA_MASTER_SNIPPET = (
    "AI Engineer amb una base sòlida en programació (Python i C) i un "
    "enfocament pràctic en la construcció de sistemes sobre models de "
    "llenguatge (LLMs)."
)

ES_OFFER_WITH_RD = (
    "Startup que desarrolla una plataforma de datos para equipos de I+D. "
    "Trabajarás con los equipos de I+D de tus clientes y dedicarás un 10 % "
    "del tiempo a I+D. Buscamos experiencia con Python y PostgreSQL, "
    "conocimientos de LLMs y nivel fluido de español e inglés."
)

ES_WITH_STRAY_CA_MARKERS = (
    "Buscamos una persona para el equipo de datos que trabaje con Python, "
    "SQL y modelos de lenguaje en un entorno de producción. Se valora "
    "experiencia con APIs y bases de datos. Proyecto per a cliente final, "
    "con equipo i+d y oficina en Barcelona, cerca de la plaza."
)

EN_FIRST_PERSON = (
    "I have built RAG pipelines end to end and I own the retrieval "
    "roadmap for our product."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(ES_OFFER, "es", id="spanish-dominant"),
        pytest.param(EN_OFFER, "en", id="english-dominant"),
        pytest.param(EN_DOMINANT_MIXED, "en", id="mixed-text-english-side-wins"),
        pytest.param(CA_OFFER, "ca", id="catalan-dominant"),
        pytest.param(CA_MASTER_SNIPPET, "ca", id="ca-master-summary-text"),
        pytest.param(EN_FIRST_PERSON, "en", id="english-i-token-is-not-catalan"),
        pytest.param(ES_OFFER_WITH_RD, "es", id="spanish-i-mas-d-is-not-catalan"),
        pytest.param(
            ES_WITH_STRAY_CA_MARKERS, "es", id="spanish-with-stray-ca-markers"
        ),
        pytest.param("En España, con Python.", "es", id="punctuation-ignored"),
        pytest.param("THE ROLE IS FOR AN ENGINEER", "en", id="case-insensitive"),
        pytest.param("que the", "es", id="tie-breaks-to-es"),
        pytest.param("", "es", id="empty-text-tie-breaks-to-es"),
    ],
)
def test_infer_language_table(text: str, expected: str) -> None:
    assert infer_language(text) == expected


def test_infer_language_deterministic() -> None:
    assert infer_language(ES_OFFER) == infer_language(ES_OFFER)
    assert infer_language(CA_OFFER) == infer_language(CA_OFFER)
