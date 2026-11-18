"""Master CV model + loader (cv-generation spec; design D8/D9).

A master is authored per variant (AI/DS × ES/EN) from the base PDFs in
``cv_base_pdfs/``. Every content leaf carries a ``claim_id`` resolvable in
the claims DB; structural boilerplate (dates, URLs, section titles, skill
row labels) stays plain. ``load_master`` fails fast: missing file, schema
violation, variant mismatch, or any leaf whose ``claim_id`` is unknown to
the claims DB raises :class:`CvgenError` naming the offender.
"""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from cva.claimsdb.loader import ClaimsDB


class CvVariant(StrEnum):
    """The role × language matrix: AI/DS × ES/EN plus the Catalan AI variant
    and the Backend Python variants (ES/EN only, backend-profile).

    AI_CA (v3) is a standard variant authored from the owner's hand-tailored
    Catalan dict; tailored per-offer variants are runtime
    SelectionPlans, never masters.
    """

    AI_ES = "AI_ES"
    AI_EN = "AI_EN"
    DS_ES = "DS_ES"
    DS_EN = "DS_EN"
    AI_CA = "AI_CA"
    BACKEND_ES = "BACKEND_ES"
    BACKEND_EN = "BACKEND_EN"


class CvgenError(ValueError):
    """Raised when a master file or selection plan violates the contract."""


class Leaf(BaseModel):
    """One content leaf: text plus the claim that backs it."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str


class Header(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Leaf
    tagline: Leaf | None = None
    contact: list[Leaf]


class Entry(BaseModel):
    """A dated entry (experience, education)."""

    model_config = ConfigDict(extra="forbid")

    title: Leaf
    date: str
    subtitle: Leaf | None = None
    description: Leaf | None = None
    bullets: list[Leaf] = []


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Leaf
    url: str | None = None
    bullets: list[Leaf] = []
    stack: Leaf | None = None


class SkillsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    items: Leaf


class Block(BaseModel):
    """One rendered section; the populated field list selects its layout."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    paragraphs: list[Leaf] = []
    entries: list[Entry] = []
    projects: list[Project] = []
    skills: list[SkillsRow] = []
    certs: list[Leaf] = []


class MasterCv(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: CvVariant
    header: Header
    sections: dict[str, Block]  # insertion order = render order


def load_master(variant: CvVariant, masters_dir: Path, db: ClaimsDB) -> MasterCv:
    """Load ``masters_dir/{variant}.json`` fail-fast.

    Design lists a two-argument signature, but leaf claim ids must be
    resolvable at load time (task 7.1); the claims DB is injected instead
    of hidden behind a global path, matching the project's DI style.
    """
    path = masters_dir / f"{variant.value}.json"
    if not path.is_file():
        raise CvgenError(f"master file not found: {path}")
    try:
        master = MasterCv.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise CvgenError(f"invalid master {path.name}: {exc}") from exc
    if master.variant != variant:
        raise CvgenError(
            f"master file {path.name} declares variant {master.variant}, "
            f"expected {variant}"
        )
    for leaf, location in _walk_leaves(master):
        if leaf.claim_id not in db.by_id:
            raise CvgenError(
                f"master {path.name}: unknown claim_id '{leaf.claim_id}' "
                f"on leaf at {location}"
            )
    return master


def master_leaf_ids(master: MasterCv) -> set[str]:
    """Every claim id referenced by any leaf of the master."""
    return {leaf.claim_id for leaf, _location in _walk_leaves(master)}


def iter_leaves(master: MasterCv) -> list[Leaf]:
    """All content leaves in render order."""
    return [leaf for leaf, _location in _walk_leaves(master)]


def _walk_leaves(master: MasterCv) -> list[tuple[Leaf, str]]:
    walked: list[tuple[Leaf, str]] = []
    header = master.header
    walked.append((header.name, "header.name"))
    if header.tagline is not None:
        walked.append((header.tagline, "header.tagline"))
    for index, leaf in enumerate(header.contact):
        walked.append((leaf, f"header.contact[{index}]"))
    for section, block in master.sections.items():
        for index, leaf in enumerate(block.paragraphs):
            walked.append((leaf, f"{section}.paragraphs[{index}]"))
        for index, entry in enumerate(block.entries):
            walked.append((entry.title, f"{section}.entries[{index}].title"))
            if entry.subtitle is not None:
                walked.append((entry.subtitle, f"{section}.entries[{index}].subtitle"))
            if entry.description is not None:
                walked.append(
                    (entry.description, f"{section}.entries[{index}].description")
                )
            for b, leaf in enumerate(entry.bullets):
                walked.append((leaf, f"{section}.entries[{index}].bullets[{b}]"))
        for index, project in enumerate(block.projects):
            walked.append((project.title, f"{section}.projects[{index}].title"))
            for b, leaf in enumerate(project.bullets):
                walked.append((leaf, f"{section}.projects[{index}].bullets[{b}]"))
            if project.stack is not None:
                walked.append((project.stack, f"{section}.projects[{index}].stack"))
        for index, row in enumerate(block.skills):
            walked.append((row.items, f"{section}.skills[{index}].items"))
        for index, leaf in enumerate(block.certs):
            walked.append((leaf, f"{section}.certs[{index}]"))
    return walked


__all__ = [
    "Block",
    "CvVariant",
    "CvgenError",
    "Entry",
    "Header",
    "Leaf",
    "MasterCv",
    "Project",
    "SkillsRow",
    "iter_leaves",
    "load_master",
    "master_leaf_ids",
]
