"""SelectionPlan model + validation (cv-generation spec; design D8).

The LLM may only emit claim ids it wants promoted, demoted, emphasized,
or featured in the tagline. Validation is deterministic and claims-DB
driven: any id absent from the DB is rejected before rendering, and ids
that must map to rendered blocks (promote/demote) are additionally checked
against the target master so ordering can only reference existing leaves.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from cva.claimsdb.loader import ClaimsDB
from cva.cvgen.master import Block, CvgenError, CvVariant, MasterCv, master_leaf_ids


class SelectionPlan(BaseModel):
    """LLM-emitted adaptation plan referencing claim ids only (D8)."""

    model_config = ConfigDict(extra="forbid")

    variant: CvVariant
    promote: list[str]
    demote: list[str]
    emphasis: dict[str, str]  # claim_id -> emphasis note
    tagline_keywords: list[str]  # claim ids featured in the tagline


def validate_plan(
    plan: SelectionPlan, db: ClaimsDB, master: MasterCv | None = None
) -> None:
    """Reject any plan referencing an unknown claim id; raise naming the id.

    With ``master`` provided, promote/demote ids must also exist as leaves
    of that master — reordering can only reference rendered content.
    """
    unknown_db = _unknown_ids(plan, set(db.by_id))
    if unknown_db:
        raise CvgenError(
            f"selection plan references unknown claim_id(s): {', '.join(unknown_db)}"
        )
    if master is not None:
        leaf_ids = master_leaf_ids(master)
        unknown_master = [i for i in [*plan.promote, *plan.demote] if i not in leaf_ids]
        if unknown_master:
            raise CvgenError(
                f"selection plan promote/demote references claim id(s) absent from "
                f"master {plan.variant.value}: {', '.join(unknown_master)}"
            )


def neutral_plan(variant: CvVariant) -> SelectionPlan:
    """The identity plan: renders the master verbatim, no adaptation."""
    return SelectionPlan(
        variant=variant, promote=[], demote=[], emphasis={}, tagline_keywords=[]
    )


def claim_family(claim_id: str) -> str:
    """Language-twin family key: ``"x_en"`` / ``"x_ca"`` / ``"x"`` all map to
    ``"x"`` (design D3) — EN/CA masters reference language-suffixed leaf ids
    while strong/weak aliases target base ids, so matching must compare
    families rather than exact ids."""
    return claim_id.removesuffix("_en").removesuffix("_ca")


def offer_emphasis(
    matched: Sequence[tuple[str, Sequence[str]]], master: MasterCv
) -> dict[str, str]:
    """Emphasis map for the offer's ✓/~ requirements on this master (D3).

    ``matched`` = (requirement name, claim_ids) for ✓/~ entries only. Keys
    are the master's OWN leaf claim ids (so ``validate_plan`` and the
    engine see ids that exist), restricted to non-summary sections; any id
    that also appears in the summary is excluded so the profile block
    never bolds. The header is never a candidate (only ``master.sections``
    is walked). Value = the matching requirement names joined with
    ``"; "`` in sorted order. Deterministic: keys sorted.
    """
    summary_ids = _section_leaf_ids(master.sections.get("summary"))
    candidate_ids: set[str] = set()
    for section, block in master.sections.items():
        if section == "summary":
            continue
        candidate_ids |= _section_leaf_ids(block)
    candidate_ids -= summary_ids

    families: dict[str, set[str]] = {
        name: {claim_family(claim_id) for claim_id in claim_ids}
        for name, claim_ids in matched
    }

    emphasis: dict[str, str] = {}
    for leaf_id in sorted(candidate_ids):
        family = claim_family(leaf_id)
        matching_names = sorted(
            name for name, family_set in families.items() if family in family_set
        )
        if matching_names:
            emphasis[leaf_id] = "; ".join(matching_names)
    return emphasis


def _section_leaf_ids(block: Block | None) -> set[str]:
    """Every claim id referenced by any leaf directly inside ``block``."""
    if block is None:
        return set()
    ids = {leaf.claim_id for leaf in block.paragraphs}
    ids.update(leaf.claim_id for leaf in block.certs)
    ids.update(row.items.claim_id for row in block.skills)
    for entry in block.entries:
        ids.add(entry.title.claim_id)
        if entry.subtitle is not None:
            ids.add(entry.subtitle.claim_id)
        if entry.description is not None:
            ids.add(entry.description.claim_id)
        ids.update(bullet.claim_id for bullet in entry.bullets)
    for project in block.projects:
        ids.add(project.title.claim_id)
        ids.update(bullet.claim_id for bullet in project.bullets)
        if project.stack is not None:
            ids.add(project.stack.claim_id)
    return ids


def _unknown_ids(plan: SelectionPlan, known: set[str]) -> list[str]:
    """Every referenced id missing from ``known``, in field order, deduped."""
    seen: list[str] = []
    for claim_id in [
        *plan.promote,
        *plan.demote,
        *plan.emphasis,
        *plan.tagline_keywords,
    ]:
        if claim_id not in known and claim_id not in seen:
            seen.append(claim_id)
    return seen


__all__ = [
    "SelectionPlan",
    "claim_family",
    "neutral_plan",
    "offer_emphasis",
    "validate_plan",
]
