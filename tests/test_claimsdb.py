"""Claims DB loader + whitelist validator behavior (claims-db spec)."""

import builtins
import json
import pathlib
from pathlib import Path

import httpx
import pytest

from cva.claimsdb import ClaimsError, load_claims, whitelist_violations
from cva.claimsdb.validator import _tokens, _vocabulary
from cva.cvgen import CvVariant, load_master, master_leaf_ids
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

FIXTURE_CLAIMS: list[dict[str, object]] = [
    {"id": "skill-python", "text": "Python", "category": "skill", "tags": ["python"]},
    {
        "id": "proj-rag-recall",
        "text": "Demo RAG project: 83% recall@5 docs",
        "category": "project",
        "tags": ["rag", "recall"],
    },
    {
        "id": "lang-english",
        "text": "Inglés C1",
        "category": "language",
        "tags": ["english", "c1"],
    },
]


def _write_claims(tmp_path: Path, claims: list[dict[str, object]]) -> Path:
    path = tmp_path / "claims.json"
    path.write_text(json.dumps(claims), encoding="utf-8")
    return path


@pytest.fixture()
def claims_db(tmp_path: Path) -> object:
    return load_claims(_write_claims(tmp_path, FIXTURE_CLAIMS))


def test_load_claims_happy_builds_typed_db_with_by_id(tmp_path: Path) -> None:
    db = load_claims(_write_claims(tmp_path, FIXTURE_CLAIMS))
    assert [claim.id for claim in db.claims] == [
        "skill-python",
        "proj-rag-recall",
        "lang-english",
    ]
    assert db.by_id["proj-rag-recall"].category == "project"
    assert db.by_id["skill-python"].tags == ["python"]
    assert len(db.by_id) == len(db.claims)  # ids unique


def test_load_claims_duplicate_id_names_offender(tmp_path: Path) -> None:
    duplicate = dict(FIXTURE_CLAIMS[0])
    duplicate["text"] = "Python avanzado"
    with pytest.raises(ClaimsError, match="skill-python"):
        load_claims(_write_claims(tmp_path, [*FIXTURE_CLAIMS, duplicate]))


def test_load_claims_missing_field_names_offender(tmp_path: Path) -> None:
    broken = [{"id": "edu-dam", "category": "education", "tags": []}]
    with pytest.raises(ClaimsError, match="edu-dam"):
        load_claims(_write_claims(tmp_path, broken))


@pytest.mark.parametrize(
    ("claim", "offender"),
    [
        pytest.param(
            {"id": "cat-books", "text": "Clean Code", "category": "books", "tags": []},
            "cat-books",
            id="unknown-category",
        ),
        pytest.param(
            {"id": "skill-c", "text": "  ", "category": "skill", "tags": []},
            "skill-c",
            id="empty-text",
        ),
    ],
)
def test_load_claims_invalid_claim_names_offender(
    tmp_path: Path, claim: dict[str, object], offender: str
) -> None:
    with pytest.raises(ClaimsError, match=offender):
        load_claims(_write_claims(tmp_path, [claim]))


def test_whitelist_exact_claim_text_returns_empty(claims_db: object) -> None:
    exact = "Demo RAG project: 83% recall@5 docs"
    assert whitelist_violations(exact, claims_db) == []  # type: ignore[arg-type]


def test_whitelist_invented_claim_flags_offending_tokens(
    claims_db: object,
) -> None:
    violations = whitelist_violations(
        "Integración con Kubernetes y cloud natives", claims_db
    )
    assert {"kubernetes", "cloud"} <= set(violations)
    assert "docker" not in violations  # v3 part B: Docker is a real claim now
    assert violations == sorted(set(violations))  # deterministic order, unique


def test_whitelist_is_deterministic(claims_db: object) -> None:
    text = "Python con Docker y FastAPI"
    assert whitelist_violations(text, claims_db) == whitelist_violations(
        text, claims_db
    )


def test_whitelist_cv_boilerplate_section_labels_not_flagged(claims_db: object) -> None:
    """Rendered CV section titles / skill-row labels are boilerplate (slice 7).

    The masters render these labels verbatim in ES and EN; they carry no
    claim content, so they must stay unflagged exactly like the other
    section-header stopwords.
    """
    labels = (
        "Perfil Profesional Experiencia Profesional "
        "Lenguajes de Programación Programming Languages Data "
        "Skills Idiomas Herramientas Certificaciones Formación Proyectos"
    )
    assert whitelist_violations(labels, claims_db) == []  # type: ignore[arg-type]


@pytest.fixture()
def forbid_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("whitelist_violations must not perform I/O")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(pathlib.Path, "read_text", _forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", _forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", _forbidden)


def test_whitelist_pure_no_disk_no_llm_access(
    claims_db: object, forbid_io: None
) -> None:
    text = "Demo RAG project: 83% recall@5 docs. Inglés C1. Python"
    assert whitelist_violations(text, claims_db) == []  # type: ignore[arg-type]


def test_whitelist_pure_flags_foreign_even_without_io(
    claims_db: object, forbid_io: None
) -> None:
    violations = whitelist_violations("Despliegue con Kubernetes", claims_db)
    assert "kubernetes" in violations
    assert "docker" not in violations  # v3 part B: in-DB skill never flagged
    assert "python" not in violations  # in-DB skill never flagged


# --- demo profile: generic guards on the shipped claims ------------------------

DEMO_CATEGORIES = {
    "identity",
    "education",
    "experience",
    "project",
    "skill",
    "language",
    "soft",
}


@pytest.fixture()
def demo_db() -> object:
    return load_claims(DEMO_CLAIMS)


def test_demo_claims_json_loads_all_categories(demo_db: object) -> None:
    assert {c.category for c in demo_db.claims} == DEMO_CATEGORIES  # type: ignore[attr-defined]


def test_demo_claims_tags_are_lowercase(demo_db: object) -> None:
    for claim in demo_db.claims:  # type: ignore[attr-defined]
        assert claim.tags == [tag.lower() for tag in claim.tags], claim.id


def test_demo_claims_pair_every_en_twin_with_base_claim(demo_db: object) -> None:
    ids = {claim.id for claim in demo_db.claims}  # type: ignore[attr-defined]
    en_ids = {cid for cid in ids if cid.endswith("_en")}
    assert en_ids
    for claim_id in sorted(en_ids):
        assert claim_id.removesuffix("_en") in ids, f"'{claim_id}' has no base twin"


def test_no_demo_master_leaf_references_a_soft_claim(demo_db: object) -> None:
    for variant in CvVariant:
        master = load_master(variant, DEMO_MASTERS, demo_db)  # type: ignore[arg-type]
        for claim_id in master_leaf_ids(master):
            category = demo_db.by_id[claim_id].category  # type: ignore[attr-defined]
            assert category != "soft", f"{variant.value} references soft '{claim_id}'"


def test_demo_whitelist_vocabulary_excludes_soft_claims(demo_db: object) -> None:
    soft = [c for c in demo_db.claims if c.category == "soft"]  # type: ignore[attr-defined]
    assert soft
    vocabulary = _vocabulary(demo_db)  # type: ignore[arg-type]
    # Same tokenizer (casefold + accent stripping) as the vocabulary itself, so
    # "resolución" is compared as "resolucion", never as the fragments "resoluci".
    soft_only = {
        token for claim in soft for token in _tokens(claim.text) if len(token) >= 6
    }
    others = {
        token
        for c in demo_db.claims  # type: ignore[attr-defined]
        if c.category != "soft"
        for token in _tokens(c.text)
    }
    exclusive = soft_only - others
    # Pinned words: accented ("resolucion", "autonomo") and plain ("teamwork").
    assert {"resolucion", "autonomo", "teamwork"} <= exclusive
    assert not exclusive & vocabulary
