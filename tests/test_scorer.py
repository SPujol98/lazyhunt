"""Scorer tests: synonym loader fail-fast, ✓/~/✗ rules, D7 formula, threshold.

Pure deterministic scoring (job-matching spec, design D6/D7): exact claim
tag or strong alias → ✓, weak alias only → ~, no mapping → ✗; weights
must_have=3 / nice=1; ``score = round(100·Σ(w_req·w_status)/Σw_req)``.
"""

import json
from pathlib import Path

import pytest

from cva.claimsdb import Claim, ClaimsDB, load_claims
from cva.match.extraction import OfferExtraction, Requirement
from cva.match.scorer import (
    SynonymEntry,
    SynonymError,
    SynonymMap,
    load_synonyms,
    score,
)
from tests.demo import DEMO_CLAIMS, DEMO_SYNONYMS


def _claim(claim_id: str, *tags: str) -> Claim:
    return Claim(
        id=claim_id, text=f"text of {claim_id}", category="skill", tags=list(tags)
    )


MINI_DB = ClaimsDB(
    claims=[
        _claim("c-python", "python"),
        _claim("c-hf", "hugging face"),
        _claim("c-jvm", "java"),
    ],
    by_id={},
)
MINI_DB = ClaimsDB(claims=MINI_DB.claims, by_id={c.id: c for c in MINI_DB.claims})

MINI_SYNONYMS: dict[str, dict[str, str]] = {
    "py": {"claim_id": "c-python", "strength": "strong"},
    "huggingface": {"claim_id": "c-hf", "strength": "strong"},
    "jvm": {"claim_id": "c-jvm", "strength": "weak"},
}


def _mini_syn() -> SynonymMap:
    return SynonymMap(
        entries={
            alias: SynonymEntry.model_validate(entry)
            for alias, entry in MINI_SYNONYMS.items()
        }
    )


def _req(name: str, *, must: bool = True) -> Requirement:
    return Requirement(
        name=name, category="skill", must_have=must, evidence="literal quote"
    )


def _write_synonyms(tmp_path: Path, payload: str) -> Path:
    path = tmp_path / "synonyms.json"
    path.write_text(payload, encoding="utf-8")
    return path


# --- loader: fail-fast contract ---


def test_load_synonyms_rejects_unknown_claim_id(tmp_path: Path) -> None:
    path = _write_synonyms(
        tmp_path, json.dumps({"foo": {"claim_id": "ghost", "strength": "strong"}})
    )
    with pytest.raises(SynonymError, match="foo.*ghost"):
        load_synonyms(path, MINI_DB)


def test_load_synonyms_rejects_invalid_strength(tmp_path: Path) -> None:
    path = _write_synonyms(
        tmp_path,
        json.dumps({"foo": {"claim_id": "c-python", "strength": "medium"}}),
    )
    with pytest.raises(SynonymError, match=r"(?s)foo.*medium"):
        load_synonyms(path, MINI_DB)


def test_load_synonyms_rejects_duplicate_alias(tmp_path: Path) -> None:
    # Raw JSON text: a Python dict literal cannot carry duplicate keys.
    path = _write_synonyms(
        tmp_path,
        '{"foo": {"claim_id": "c-python", "strength": "strong"},'
        ' "foo": {"claim_id": "c-hf", "strength": "weak"}}',
    )
    with pytest.raises(SynonymError, match="foo"):
        load_synonyms(path, MINI_DB)


def test_load_synonyms_parses_mini_file(tmp_path: Path) -> None:
    path = _write_synonyms(tmp_path, json.dumps(MINI_SYNONYMS))
    syn = load_synonyms(path, MINI_DB)
    assert syn.entries["jvm"].strength == "weak"
    assert syn.entries["py"].claim_id == "c-python"


# --- status rules: ✓ / ~ / ✗ ---


def test_exact_claim_tag_is_check() -> None:
    result = score(_ex(_req("Python")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✓"
    assert result.breakdown[0].claim_ids == ["c-python"]


def test_strong_alias_is_check() -> None:
    result = score(_ex(_req("huggingface")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✓"
    assert result.breakdown[0].claim_ids == ["c-hf"]


def test_weak_alias_is_tilde_with_half_credit() -> None:
    result = score(_ex(_req("jvm")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "~"
    assert result.breakdown[0].claim_ids == ["c-jvm"]
    assert result.score == 50  # single must-have at 0.5 credit


def test_unmapped_requirement_is_cross_and_contributes_zero() -> None:
    result = score(_ex(_req("docker"), _req("Python")), MINI_DB, _mini_syn())
    statuses = [m.status for m in result.breakdown]
    assert statuses == ["✗", "✓"]
    assert result.breakdown[0].claim_ids == []
    assert result.score == 50  # 3 / (3+3)


def test_case_and_spacing_normalized_on_tags() -> None:
    result = score(_ex(_req("Hugging Face")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✓"


def test_accents_normalized_on_tags() -> None:
    db = load_claims(DEMO_CLAIMS)
    result = score(_ex(_req("Evaluación")), db, load_synonyms(DEMO_SYNONYMS, db))
    assert result.breakdown[0].status == "✓"
    assert "proj-rag-recall-metrics" in result.breakdown[0].claim_ids


# --- weights and formula ---


def _ex(*reqs: Requirement) -> OfferExtraction:
    return OfferExtraction(title="AI Engineer", company="Acme", requirements=list(reqs))


def test_must_have_dominates_nice_three_to_one() -> None:
    failed_must = score(
        _ex(_req("docker", must=True), _req("Python", must=False)),
        MINI_DB,
        _mini_syn(),
    )
    passed_must = score(
        _ex(_req("Python", must=True), _req("docker", must=False)),
        MINI_DB,
        _mini_syn(),
    )
    assert failed_must.score == 25  # nice ✓ (1) over must+nice (4)
    assert passed_must.score == 75  # must ✓ (3) over 4


@pytest.mark.parametrize(
    ("must_count", "nice_counts", "expected", "passes_at_65"),
    [
        # Σw=20: 5 must (4 ✓, 1 ✗ = 12) + 5 nice (1 ✓, 4 ✗ = 1) → 13/20
        pytest.param({"✓": 4, "✗": 1}, {"✓": 1, "✗": 4}, 65, True, id="65"),
        # Σw=25: 5 must (4 ✓, 1 ✗ = 12) + 10 nice (4 ✓, 6 ✗ = 4) → 16/25
        pytest.param({"✓": 4, "✗": 1}, {"✓": 4, "✗": 6}, 64, False, id="64"),
        # Σw=25: 5 must (12) + 10 nice (4 ✓, 1 ~, 5 ✗ = 4.5) → 16.5/25
        pytest.param({"✓": 4, "✗": 1}, {"✓": 4, "~": 1, "✗": 5}, 66, True, id="66"),
    ],
)
def test_threshold_boundary_at_65(
    must_count: dict[str, int],
    nice_counts: dict[str, int],
    expected: int,
    passes_at_65: bool,
) -> None:
    name_by_status = {"✓": "Python", "~": "jvm", "✗": "gap"}
    reqs = [
        _req(name_by_status[status], must=True)
        for status, count in must_count.items()
        for _ in range(count)
    ] + [
        _req(name_by_status[status], must=False)
        for status, count in nice_counts.items()
        for _ in range(count)
    ]
    result = score(_ex(*reqs), MINI_DB, _mini_syn())
    assert result.score == expected
    assert result.threshold == 65
    assert (result.score >= result.threshold) is passes_at_65


def test_empty_requirements_score_zero() -> None:
    result = score(_ex(), MINI_DB, _mini_syn())
    assert result.score == 0
    assert result.breakdown == []
    assert result.threshold == 65


def test_custom_threshold_echoed_in_result() -> None:
    result = score(_ex(_req("Python")), MINI_DB, _mini_syn(), threshold=25)
    assert result.threshold == 25
    assert result.score == 100


# --- demo synonyms.json integration ---


def test_load_synonyms_demo_file_references_only_real_claims() -> None:
    db = load_claims(DEMO_CLAIMS)
    syn = load_synonyms(DEMO_SYNONYMS, db)
    assert len(syn.entries) >= 20
    assert {e.claim_id for e in syn.entries.values()} <= set(db.by_id)


def test_demo_synonyms_seed_statuses() -> None:
    db = load_claims(DEMO_CLAIMS)
    syn = load_synonyms(DEMO_SYNONYMS, db)
    result = score(
        _ex(_req("Vector DB"), _req("Containers"), _req("Kubernetes"), _req("Docker")),
        db,
        syn,
    )
    by_name = {m.requirement.name: m for m in result.breakdown}
    assert by_name["Vector DB"].status == "~"
    assert by_name["Vector DB"].claim_ids == ["proj-rag-hybrid-retrieval"]
    assert by_name["Containers"].status == "~"
    assert by_name["Containers"].claim_ids == ["skill-docker"]
    assert by_name["Docker"].status == "✓"
    assert "skill-docker" in by_name["Docker"].claim_ids
    assert by_name["Kubernetes"].status == "✗"
    assert by_name["Kubernetes"].claim_ids == []


def test_output_claim_ids_exist_in_db_invariant() -> None:
    db = load_claims(DEMO_CLAIMS)
    syn = load_synonyms(DEMO_SYNONYMS, db)
    result = score(
        _ex(
            _req("Python"),
            _req("Vector DB"),
            _req("PostgreSQL"),
            _req("LangChain"),
            _req("Recall@k"),
        ),
        db,
        syn,
    )
    for match in result.breakdown:
        assert set(match.claim_ids) <= set(db.by_id)
    assert len(result.breakdown) == 5


# --- soft-framing fallback: "interest in X" / "good X practices" etc. ---


@pytest.mark.parametrize(
    "name",
    [
        "Interest in Python",
        "Knowledge of Python",
        "Knowledge in Python",
        "Experience with Python",
        "Experience in Python",
        "Familiarity with Python",
        "Good Python",
        "Python interest",
        "Python knowledge",
        "Python experience",
    ],
)
def test_soft_framing_wrapper_falls_back_to_the_core_skill(name: str) -> None:
    """A soft-framing wrapper never blocks a requirement whose core skill is
    already backed by a real, exact-tagged claim (design: match-calibration
    T2)."""
    result = score(_ex(_req(name)), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✓", name
    assert result.breakdown[0].claim_ids == ["c-python"], name


def test_soft_framing_wrapper_still_yields_tilde_on_a_weak_alias() -> None:
    result = score(_ex(_req("Interest in jvm")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "~"
    assert result.breakdown[0].claim_ids == ["c-jvm"]


def test_soft_framing_never_promotes_a_genuinely_unmapped_requirement() -> None:
    """Stripping is a fallback only: it cannot invent a match out of nothing."""
    result = score(_ex(_req("Interest in Docker")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✗"
    assert result.breakdown[0].claim_ids == []


def test_soft_framing_does_not_run_when_the_literal_name_already_matches() -> None:
    """A requirement that resolves directly is never re-resolved via
    stripping — the direct result is definitive."""
    result = score(_ex(_req("docker")), MINI_DB, _mini_syn())
    assert result.breakdown[0].status == "✗"  # no framing word to strip either
