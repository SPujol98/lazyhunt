"""SelectionPlan validation against the claims DB (cv-generation spec; design D8).

Also covers offer-driven emphasis derivation (design D3): ``claim_family``
and ``offer_emphasis`` are pure functions exercised with a hand-built
in-memory :class:`MasterCv` fixture so every candidate/exclusion rule
(summary, header, `_en`/`_ca` family expansion, note joining) is under
direct control, independent of any committed master JSON.
"""

import pytest

from cva.claimsdb import load_claims
from cva.cvgen import CvVariant, load_master
from cva.cvgen.master import Block, Entry, Header, Leaf, MasterCv, Project, SkillsRow
from cva.cvgen.plan import (
    SelectionPlan,
    claim_family,
    neutral_plan,
    offer_emphasis,
    validate_plan,
)
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

CLAIMS_PATH = DEMO_CLAIMS
FIXTURES = DEMO_MASTERS


@pytest.fixture(scope="module")
def db() -> object:
    return load_claims(CLAIMS_PATH)


@pytest.fixture(scope="module")
def ai_es_master(db: object) -> object:
    return load_master(CvVariant.AI_ES, FIXTURES, db)


def test_plan_with_unknown_claim_id_in_promote_fails_naming_id(db: object) -> None:
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=["skill-kubernetes"],
        demote=[],
        emphasis={},
        tagline_keywords=[],
    )
    with pytest.raises(Exception, match="skill-kubernetes"):
        validate_plan(plan, db)


def test_plan_with_unknown_claim_id_in_demote_fails_naming_id(db: object) -> None:
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=["skill-kubernetes"],
        emphasis={},
        tagline_keywords=[],
    )
    with pytest.raises(Exception, match="skill-kubernetes"):
        validate_plan(plan, db)


def test_plan_with_unknown_claim_id_in_emphasis_fails_naming_id(db: object) -> None:
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=[],
        emphasis={"skill-langchain": "lead with this"},
        tagline_keywords=[],
    )
    with pytest.raises(Exception, match="skill-langchain"):
        validate_plan(plan, db)


def test_plan_with_unknown_claim_id_in_tagline_keywords_fails_naming_id(
    db: object,
) -> None:
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=[],
        emphasis={},
        tagline_keywords=["proj-tensorflow"],
    )
    with pytest.raises(Exception, match="proj-tensorflow"):
        validate_plan(plan, db)


def test_fully_known_plan_passes(db: object, ai_es_master: object) -> None:
    leaf_ids = {
        leaf.claim_id for leaf in ai_es_master.sections["projects"].projects[0].bullets
    }
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=sorted(leaf_ids),
        demote=[],
        emphasis={"proj-metrics-api-overview": "key differentiator"},
        tagline_keywords=["skill-python"],
    )
    assert validate_plan(plan, db, master=ai_es_master) is None


def test_known_in_db_but_absent_from_master_promote_rejected(
    db: object, ai_es_master: object
) -> None:
    # skill-java exists in the claims DB but the AI_ES fixture master never uses it.
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=["skill-java"],
        demote=[],
        emphasis={},
        tagline_keywords=[],
    )
    with pytest.raises(Exception, match="skill-java"):
        validate_plan(plan, db, master=ai_es_master)


def test_neutral_plan_is_identity_and_passes(db: object, ai_es_master: object) -> None:
    plan = neutral_plan(CvVariant.AI_ES)
    assert plan.promote == [] and plan.demote == []
    assert plan.emphasis == {} and plan.tagline_keywords == []
    assert validate_plan(plan, db, master=ai_es_master) is None


# --- claim_family (design D3) ---------------------------------------------------


def test_claim_family_strips_en_suffix() -> None:
    assert claim_family("exp-acme-maintenance_en") == "exp-acme-maintenance"


def test_claim_family_strips_ca_suffix() -> None:
    assert claim_family("sum-ds-goal_ca") == "sum-ds-goal"


def test_claim_family_without_suffix_is_identity() -> None:
    assert claim_family("skill-python") == "skill-python"


# --- offer_emphasis (design D3) -------------------------------------------------


def _leaf(claim_id: str, text: str = "text") -> Leaf:
    return Leaf(claim_id=claim_id, text=text)


@pytest.fixture
def emphasis_master() -> MasterCv:
    """Hand-built master covering every candidate/exclusion rule at once:
    header leaves (never candidates), a summary paragraph (always
    excluded), an `_en`-suffixed experience bullet and a `_ca`-suffixed
    project bullet (family expansion), plus a skills row and a
    certification (candidates, even though the engine defines no bold
    site for skills rows — D3's `candidate_ids` walks every non-summary
    leaf; only the render engine restricts which of them actually bold)."""
    return MasterCv(
        variant=CvVariant.AI_ES,
        header=Header(
            name=_leaf("identity-contact", "Alex Rivera"),
            tagline=_leaf("identity-linkedin-headline", "AI Engineer"),
            contact=[_leaf("identity-contact", "Barcelona")],
        ),
        sections={
            "summary": Block(
                paragraphs=[_leaf("proj-rag-overview", "Summary paragraph")]
            ),
            "experience": Block(
                entries=[
                    Entry(
                        title=_leaf("exp-acme-role", "Title"),
                        date="2020",
                        bullets=[
                            _leaf("exp-acme-maintenance_en", "EN twin bullet"),
                            _leaf("skill-mypy", "mypy bullet"),
                        ],
                    )
                ]
            ),
            "projects": Block(
                projects=[
                    Project(
                        title=_leaf("proj-metrics-api-overview", "Project title"),
                        bullets=[_leaf("proj-x_ca", "CA twin bullet")],
                    )
                ]
            ),
            "skills": Block(
                skills=[SkillsRow(label="Tech", items=_leaf("skill-python", "Python"))]
            ),
            "certifications": Block(certs=[_leaf("cert-aws", "AWS cert")]),
        },
    )


def test_offer_emphasis_excludes_summary_leaves_even_when_matched(
    emphasis_master: MasterCv,
) -> None:
    matched = [("Summary req", ["proj-rag-overview"])]
    result = offer_emphasis(matched, emphasis_master)
    assert "proj-rag-overview" not in result


def test_offer_emphasis_expands_en_and_ca_family_to_base_requirement_ids(
    emphasis_master: MasterCv,
) -> None:
    matched = [
        ("EN twin req", ["exp-acme-maintenance"]),
        ("CA twin req", ["proj-x"]),
    ]
    result = offer_emphasis(matched, emphasis_master)
    assert result["exp-acme-maintenance_en"] == "EN twin req"
    assert result["proj-x_ca"] == "CA twin req"


def test_offer_emphasis_header_leaves_are_never_candidates(
    emphasis_master: MasterCv,
) -> None:
    matched = [("Header req", ["identity-contact", "identity-linkedin-headline"])]
    result = offer_emphasis(matched, emphasis_master)
    assert "identity-contact" not in result
    assert "identity-linkedin-headline" not in result


def test_offer_emphasis_joins_multiple_requirement_names_sorted(
    emphasis_master: MasterCv,
) -> None:
    matched = [("Zebra skill", ["skill-mypy"]), ("Alpha skill", ["skill-mypy"])]
    result = offer_emphasis(matched, emphasis_master)
    assert result["skill-mypy"] == "Alpha skill; Zebra skill"


def test_offer_emphasis_empty_matched_returns_empty_dict(
    emphasis_master: MasterCv,
) -> None:
    assert offer_emphasis([], emphasis_master) == {}


def test_offer_emphasis_unmatched_family_is_absent(emphasis_master: MasterCv) -> None:
    matched = [("Other req", ["skill-mypy"])]
    result = offer_emphasis(matched, emphasis_master)
    assert "skill-python" not in result
    assert "cert-aws" not in result
