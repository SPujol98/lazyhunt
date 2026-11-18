"""Master CV model + loader (cv-generation spec: claim ids on every leaf)."""

import json
from pathlib import Path

import pytest

from cva.claimsdb import load_claims
from cva.cvgen import CvgenError, CvVariant, load_master, master_leaf_ids
from cva.cvgen.master import iter_leaves
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

CLAIMS_PATH = DEMO_CLAIMS
FIXTURES = DEMO_MASTERS
REAL_MASTERS = DEMO_MASTERS


@pytest.fixture(scope="module")
def db() -> object:
    return load_claims(CLAIMS_PATH)


ALL_VARIANTS = [
    CvVariant.AI_ES,
    CvVariant.AI_EN,
    CvVariant.DS_ES,
    CvVariant.DS_EN,
]

# v3 amendment: AI_CA (Catalan) is a standard base variant.
REAL_VARIANTS = [*ALL_VARIANTS, CvVariant.AI_CA]


@pytest.mark.parametrize("variant", ALL_VARIANTS)
def test_all_four_variants_load_with_resolvable_leaves(
    variant: CvVariant, db: object
) -> None:
    master = load_master(variant, FIXTURES, db)
    assert master.variant is variant
    assert master.header.name is not None
    # Every leaf claim_id is resolvable in the claims DB (loader raises otherwise).
    assert master.sections["summary"].paragraphs
    assert master.sections["summary"].paragraphs[0].claim_id in db.by_id


def test_every_leaf_carries_claim_id_present_in_db(db: object) -> None:
    master = load_master(CvVariant.AI_ES, FIXTURES, db)
    leaf_ids: list[str] = []
    leaf_ids.append(master.header.name.claim_id)
    if master.header.tagline is not None:
        leaf_ids.append(master.header.tagline.claim_id)
    leaf_ids.extend(leaf.claim_id for leaf in master.header.contact)
    for block in master.sections.values():
        leaf_ids.extend(leaf.claim_id for leaf in block.paragraphs)
        leaf_ids.extend(leaf.claim_id for leaf in block.certs)
        for entry in block.entries:
            leaf_ids.append(entry.title.claim_id)
            if entry.subtitle is not None:
                leaf_ids.append(entry.subtitle.claim_id)
            if entry.description is not None:
                leaf_ids.append(entry.description.claim_id)
            leaf_ids.extend(leaf.claim_id for leaf in entry.bullets)
        for project in block.projects:
            leaf_ids.append(project.title.claim_id)
            if project.stack is not None:
                leaf_ids.append(project.stack.claim_id)
            leaf_ids.extend(leaf.claim_id for leaf in project.bullets)
        for row in block.skills:
            leaf_ids.append(row.items.claim_id)
    assert leaf_ids  # non-empty: the walk really collected leaves
    assert all(claim_id in db.by_id for claim_id in leaf_ids)


def test_unknown_claim_id_on_leaf_is_load_error_naming_id(
    db: object, tmp_path: Path
) -> None:
    raw = json.loads((FIXTURES / "AI_ES.json").read_text(encoding="utf-8"))
    raw["sections"]["summary"]["paragraphs"][0]["claim_id"] = "skill-kubernetes"
    bad = tmp_path / "AI_ES.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="skill-kubernetes"):
        load_master(CvVariant.AI_ES, tmp_path, db)


def test_missing_master_file_fails_fast(db: object, tmp_path: Path) -> None:
    with pytest.raises(Exception, match="AI_EN.json"):
        load_master(CvVariant.AI_EN, tmp_path, db)


def test_schema_violation_is_load_error(db: object, tmp_path: Path) -> None:
    raw = json.loads((FIXTURES / "AI_ES.json").read_text(encoding="utf-8"))
    del raw["sections"]["summary"]["paragraphs"][0]["text"]  # leaf without text
    bad = tmp_path / "AI_ES.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(CvgenError):
        load_master(CvVariant.AI_ES, tmp_path, db)


def test_variant_mismatch_between_file_and_request_rejected(
    db: object, tmp_path: Path
) -> None:
    raw = json.loads((FIXTURES / "DS_ES.json").read_text(encoding="utf-8"))
    (tmp_path / "AI_ES.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="variant"):
        load_master(CvVariant.AI_ES, tmp_path, db)


def test_unknown_section_field_rejected_not_silently_dropped(
    db: object, tmp_path: Path
) -> None:
    raw = json.loads((FIXTURES / "AI_ES.json").read_text(encoding="utf-8"))
    raw["sections"]["summary"]["paragrphs"] = raw["sections"]["summary"].pop(
        "paragraphs"
    )
    bad = tmp_path / "AI_ES.json"
    bad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(Exception, match="paragrphs"):
        load_master(CvVariant.AI_ES, tmp_path, db)


@pytest.mark.parametrize("variant", REAL_VARIANTS)
def test_real_masters_all_five_variants_load(variant: CvVariant, db: object) -> None:
    """The shipped masters/ files (authored from the base PDFs) must always load."""
    master = load_master(variant, REAL_MASTERS, db)
    assert master.header.contact


def test_real_en_masters_use_en_twin_claims_es_masters_base_claims(
    db: object,
) -> None:
    """Owner amendment: EN renders must be 100% English → `_en` claim twins."""
    for variant in REAL_VARIANTS:
        master = load_master(variant, REAL_MASTERS, db)
        ids = master_leaf_ids(master)  # type: ignore[arg-type]
        en_used = {cid for cid in ids if cid.endswith("_en")}
        if variant in (CvVariant.AI_EN, CvVariant.DS_EN):
            assert en_used == ids, (
                f"{variant}: non-EN leaf claims {sorted(ids - en_used)}"
            )
        else:
            assert not en_used, (
                f"{variant}: EN twins in {variant} master {sorted(en_used)}"
            )


def test_real_masters_have_no_fly_in_project(db: object) -> None:
    """v2 content: Fly-in removed from every master (claims stay in the DB)."""
    for variant in REAL_VARIANTS:
        master = load_master(variant, REAL_MASTERS, db)
        ids = master_leaf_ids(master)  # type: ignore[arg-type]
        assert "proj-fly-in" not in ids, variant
        blob = _accentless_blob(master)
        assert "fly-in" not in blob, variant
        assert "pygame" not in blob, variant


def test_masters_are_base_variants_only(db: object) -> None:
    """Per-offer tailored variants are runtime plans: never a base master."""
    variants = {variant.name for variant in CvVariant}
    assert {path.stem for path in REAL_MASTERS.glob("*.json")} <= variants


def test_real_ds_masters_carry_ds_tagline_claim(db: object) -> None:
    """Owner amendment: the DS tagline is now a backed claim (was flagged)."""
    ds_en = load_master(CvVariant.DS_EN, REAL_MASTERS, db)
    ds_es = load_master(CvVariant.DS_ES, REAL_MASTERS, db)
    assert ds_en.header.tagline is not None
    assert ds_en.header.tagline.claim_id == "identity-ds-tagline_en"
    assert ds_es.header.tagline is not None
    assert ds_es.header.tagline.claim_id == "identity-ds-tagline"


def _accentless_blob(master: object) -> str:
    import unicodedata

    blob = " ".join(leaf.text for leaf in iter_leaves(master)).casefold()  # type: ignore[arg-type]
    return "".join(
        ch for ch in unicodedata.normalize("NFD", blob) if not unicodedata.combining(ch)
    )


def test_demo_masters_experience_is_northwind_then_acme(db: object) -> None:
    """The demo persona lists the freelance project first, the internship last,
    in the claim-id language of each master."""
    for variant in REAL_VARIANTS:
        master = load_master(variant, REAL_MASTERS, db)
        entries = master.sections["experience"].entries
        assert len(entries) == 2, variant
        en = "_en" if variant in (CvVariant.AI_EN, CvVariant.DS_EN) else ""
        assert entries[0].title.claim_id == f"exp-northwind-role{en}", variant
        assert entries[1].title.claim_id == f"exp-acme-role{en}", variant
