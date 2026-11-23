"""The structured source of a base CV lazyhunt built itself (tailored-cv T1, S3/S4(a)).

A starter CV (:mod:`cva.cvgen.starter`) is drawn from a :class:`CvSource`:
the slot it is for, its template and language, the header (name, target
position, contact line) and its ordered sections, each an ordered list of
items ``{claim_id, text}``. Every item points at the claim it says
(``claims.json``): the ``setup-*`` claims :mod:`cva.onboarding.profile_gen`
builds from the ``/setup`` answers, or the identity claim for the header and,
without an experience answer, the profile line.

Storage: one JSON file per slot next to the PDF it was rendered to,
``cv_base_pdfs/<SLOT>.source.json`` (e.g. ``AI_ES.source.json``), written
atomically by ``/setup`` Confirm and by ``/cvs`` (change template, rebuild).
It is named after the slot, not the PDF, because a slot's stored file name
can change between Confirms while its key never does; and it lives in the
base PDFs directory, so Confirm's snapshot (every file directly inside that
directory) undoes it together with the PDF and the manifest. The manifest
itself is unchanged: older loaders and ``scripts/sync_base_cvs.py`` never
see the source.

A source records the sha256 of the PDF it was rendered to
(``pdf_sha256``). It describes the slot's base CV only while that hash
matches the manifest entry (an uploaded PDF later replacing a created one
makes the source obsolete) and every claim id it uses still exists:
:func:`adaptable_source` answers ``None`` otherwise, never an exception at
delivery. Uploaded PDFs, the owner's hand-reviewed PDFs and profiles without
a profile directory have no source at all.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal

from cva.cvgen.base_pdfs import VARIANT_KEY_RE, manifest_entry, verified_base_pdf
from cva.cvgen.master import CvgenError
from cva.fsutil import atomic_write
from cva.profile_paths import resolve_profile_paths

SOURCE_VERSION: Final[int] = 1
SOURCE_SUFFIX: Final[str] = ".source.json"
# Section kinds, in the order a starter CV draws them; the ``inline`` ones
# are drawn as one line of items joined with " · ".
SECTION_KINDS: Final[tuple[str, ...]] = (
    "profile",
    "experience",
    "education",
    "skills",
    "languages",
)
INLINE_KINDS: Final[frozenset[str]] = frozenset({"skills", "languages"})
SourceState = Literal["none", "current", "stale_claims", "replaced"]


class CvSourceError(ValueError):
    """A source file that is not a valid :class:`CvSource`."""


@dataclass(frozen=True)
class SourceItem:
    """One line (or one inline entry) of a section and the claim it says.
    ``bold`` is the leading part drawn in bold (an experience entry's role)."""

    claim_id: str
    text: str
    bold: str | None = None


@dataclass(frozen=True)
class SourceSection:
    kind: str  # one of SECTION_KINDS
    items: tuple[SourceItem, ...]

    @property
    def inline(self) -> bool:
        return self.kind in INLINE_KINDS


@dataclass(frozen=True)
class CvSource:
    """What a base CV lazyhunt built says, item by item (module docstring)."""

    slot: str  # ``<ROLE>_<LANG>``
    lang: str  # the CV language (headings): ``es``/``en``/``ca``
    template: str  # a :data:`cva.cvgen.starter.TEMPLATES` name
    name: str
    headline: str  # the target position
    identity_claim_id: str  # the claim behind the name and contact line
    contact: tuple[str, ...] = ()
    sections: tuple[SourceSection, ...] = ()
    pdf_sha256: str | None = None  # the PDF rendered from it, once stored

    def claim_ids(self) -> list[str]:
        """Every claim id the source uses, header first, without repeats."""
        ids = [self.identity_claim_id]
        ids += [item.claim_id for s in self.sections for item in s.items]
        return list(dict.fromkeys(ids))

    def section(self, kind: str) -> SourceSection | None:
        return next((s for s in self.sections if s.kind == kind), None)

    def with_template(self, template: str) -> "CvSource":
        return replace(self, template=template)

    def to_json(self) -> str:
        data: dict[str, Any] = {
            "version": SOURCE_VERSION,
            "slot": self.slot,
            "lang": self.lang,
            "template": self.template,
            "name": self.name,
            "headline": self.headline,
            "identity_claim_id": self.identity_claim_id,
            "contact": list(self.contact),
            "sections": [
                {
                    "kind": s.kind,
                    "items": [
                        {
                            "claim_id": i.claim_id,
                            "text": i.text,
                            **({"bold": i.bold} if i.bold is not None else {}),
                        }
                        for i in s.items
                    ],
                }
                for s in self.sections
            ],
            "pdf_sha256": self.pdf_sha256,
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _str(data: dict[str, Any], key: str, *, empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise CvSourceError(f"source: {key!r} must be a non-empty string")
    return value


def _item(raw: Any) -> SourceItem:
    if not isinstance(raw, dict):
        raise CvSourceError("source: every item must be an object")
    bold = raw.get("bold")
    if bold is not None and not isinstance(bold, str):
        raise CvSourceError("source: an item's 'bold' must be a string")
    return SourceItem(_str(raw, "claim_id"), _str(raw, "text"), bold)


def _section(raw: Any) -> SourceSection:
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise CvSourceError("source: every section needs a list of items")
    kind = _str(raw, "kind")
    if kind not in SECTION_KINDS:
        raise CvSourceError(f"source: unknown section kind {kind!r}")
    return SourceSection(kind, tuple(_item(i) for i in raw["items"]))


def parse_source(text: str) -> CvSource:
    """A source from its JSON text; :class:`CvSourceError` when it is not one."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CvSourceError(f"source: not JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CvSourceError("source: must be a JSON object")
    if data.get("version") != SOURCE_VERSION:
        raise CvSourceError(f"source: unsupported version {data.get('version')!r}")
    slot = _str(data, "slot")
    if VARIANT_KEY_RE.fullmatch(slot) is None:
        raise CvSourceError(f"source: {slot!r} is not a <ROLE>_<LANG> slot")
    contact, sections, sha = (
        data.get("contact"),
        data.get("sections"),
        data.get("pdf_sha256"),
    )
    if not isinstance(contact, list) or not all(isinstance(c, str) for c in contact):
        raise CvSourceError("source: 'contact' must be a list of strings")
    if not isinstance(sections, list):
        raise CvSourceError("source: 'sections' must be a list")
    if sha is not None and not isinstance(sha, str):
        raise CvSourceError("source: 'pdf_sha256' must be a string")
    parsed = tuple(_section(s) for s in sections)
    kinds = [s.kind for s in parsed]
    if len(set(kinds)) != len(kinds):
        raise CvSourceError("source: a section kind appears twice")
    return CvSource(
        slot=slot,
        lang=_str(data, "lang"),
        template=_str(data, "template"),
        name=_str(data, "name"),
        headline=_str(data, "headline"),
        identity_claim_id=_str(data, "identity_claim_id"),
        contact=tuple(contact),
        sections=parsed,
        pdf_sha256=sha,
    )


def source_path(base_dir: Path, slot: str) -> Path:
    """``<base_dir>/<SLOT>.source.json``."""
    return base_dir / f"{slot}{SOURCE_SUFFIX}"


def save_source(path: Path, source: CvSource) -> None:
    """Write ``source`` to ``path`` atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, source.to_json())


def load_source(path: Path) -> CvSource:
    """The source at ``path``; :class:`CvSourceError` when it is unreadable
    or invalid (a missing file is an :class:`OSError`)."""
    return parse_source(path.read_text(encoding="utf-8"))


def claim_ids_in(claims_path: Path) -> frozenset[str] | None:
    """The ids of ``claims.json`` (``None`` when it cannot be read)."""
    try:
        raw = json.loads(claims_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    return frozenset(
        str(item["id"])
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def source_state(
    base_dir: Path, slot: str, claims: frozenset[str] | None
) -> tuple[SourceState, CvSource | None]:
    """How the slot's source relates to its stored base CV:

    ``none``          no (valid) source: an uploaded or hand-made PDF
    ``replaced``      a source whose PDF is no longer the slot's verified PDF
    ``stale_claims``  the PDF matches but a claim id it uses is gone (or
                      ``claims`` is ``None``)
    ``current``       the PDF matches and every claim id exists
    """
    try:
        source = load_source(source_path(base_dir, slot))
    except (OSError, CvSourceError):
        return "none", None
    if source.slot != slot:
        return "none", None
    try:
        verified_base_pdf(slot, base_dir)
        sha = manifest_entry(slot, base_dir).sha256
    except (CvgenError, OSError):
        return "replaced", source
    if source.pdf_sha256 != sha:
        return "replaced", source
    if claims is None or any(cid not in claims for cid in source.claim_ids()):
        return "stale_claims", source
    return "current", source


def adaptable_source(profile_dir: Path | None, slot: str) -> CvSource | None:
    """The source of ``slot``'s base CV when it can be adapted to an offer:
    lazyhunt built it, its PDF is still the verified one in the manifest and
    every claim id it uses exists in ``claims.json``. ``None`` for an
    uploaded or hand-made PDF, a legacy install (``profile_dir`` ``None``),
    an obsolete or stale source; never raises for a broken file."""
    if profile_dir is None:
        return None
    paths = resolve_profile_paths(profile_dir)
    state, source = source_state(paths.base_pdfs_dir, slot, claim_ids_in(paths.claims))
    return source if state == "current" else None
