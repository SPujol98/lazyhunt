"""The base CV catalog: ``/cvs`` in Telegram and Options › Profile (tailored-cv T1, S3/S4(a)).

:func:`base_cvs` lists every base CV slot of the manifest (position ×
language, in manifest order) with where it comes from:

============  ===============================================================
origin        meaning
============  ===============================================================
``lazyhunt``  ``/setup`` created it; its structured source
              (:mod:`cva.cvgen.cv_source`) still describes the stored PDF, so
              the template and sections are known and the two actions below
              apply. ``stale`` when a claim it uses is gone (a rebuild fixes it)
``uploaded``  a PDF the user sent, in a profile that also has created ones:
              not adaptable (``/setup cvs`` can create one instead)
``own``       a profile where no base CV has a source at all (the owner's
              hand-reviewed PDFs, a legacy install): listed plainly as the
              user's own PDF, with no adaptation wording (owner decision, S5)
============  ===============================================================

:func:`change_template` re-renders a created CV from its source with another
template; :func:`rebuild` renders it again from the current ``/setup``
answers (same template). Both replace the PDF, its source and the manifest
sha256 under :data:`cva.onboarding.commit.WRITE_LOCK`, all or nothing (the
base PDFs directory is snapshotted first, :mod:`cva.onboarding.snapshot`).
Nothing else in the manifest changes.
"""

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

from cva.cvgen.base_pdfs import (
    MANIFEST_NAME,
    VARIANT_KEY_RE,
    deliverable_variants,
    manifest_entry,
    verified_base_pdf,
)
from cva.cvgen.cv_source import (
    CvSource,
    SourceState,
    claim_ids_in,
    save_source,
    source_path,
    source_state,
)
from cva.cvgen.master import CvgenError
from cva.cvgen.starter import TEMPLATES, render_source
from cva.families import FamilyRegistry
from cva.fsutil import atomic_write
from cva.i18n import Lang, t
from cva.onboarding import cv_upload as U
from cva.onboarding.commit import WRITE_LOCK
from cva.onboarding.prefill import current_answers
from cva.onboarding.snapshot import Snapshot
from cva.onboarding.starter import identity_claim_id, starter_source
from cva.profile_identity import ProfileError
from cva.profile_paths import resolve_profile_paths

Origin = Literal["lazyhunt", "uploaded", "own"]
# Source states of a CV lazyhunt created (stale claims included).
_CREATED: Final[frozenset[SourceState]] = frozenset({"current", "stale_claims"})
# Callback payload prefix of the catalog's buttons (``cvs:<action>:<slot>[:<arg>]``).
PREFIX: Final[str] = "cvs"


class CatalogError(Exception):
    """A refused catalog action; ``key`` is the i18n key of the reason."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class BaseCv:
    """One base CV slot as the catalog shows it."""

    slot: str  # ``<ROLE>_<LANG>``
    label: str  # the position (family label, else the role)
    lang: str  # lower-case CV language
    file: str
    origin: Origin
    template: str | None = None  # ``lazyhunt`` only
    sections: tuple[tuple[str, int], ...] = ()  # (kind, items), ``lazyhunt`` only
    stale: bool = False  # a claim the source uses is gone
    adaptable: bool = False

    @property
    def name(self) -> str:
        """``AI · ES``."""
        return f"{self.label} · {self.lang.upper()}"


def _labels(families: FamilyRegistry) -> dict[str, str]:
    return {family.role: family.label for family in families.families}


def base_cvs(profile_dir: Path | None, families: FamilyRegistry) -> list[BaseCv]:
    """Every base CV slot of the profile's manifest (module docstring); an
    empty list when there is none or the manifest cannot be read."""
    paths = resolve_profile_paths(profile_dir)
    base = paths.base_pdfs_dir
    try:
        manifest = json.loads((base / MANIFEST_NAME).read_text(encoding="utf-8"))
        slots = [s for s in manifest["variants"] if s in deliverable_variants(base)]
        files = {s: manifest_entry(s, base).file for s in slots}
    except (OSError, ValueError, KeyError, TypeError, CvgenError):
        return []
    claims = claim_ids_in(paths.claims) if profile_dir is not None else None
    labels = _labels(families)
    states: list[tuple[str, SourceState, CvSource | None]] = [
        (slot, *source_state(base, slot, claims))
        if profile_dir is not None
        else (slot, "none", None)
        for slot in slots
    ]
    any_created = any(state in _CREATED for _, state, _ in states)
    out: list[BaseCv] = []
    for slot, state, source in states:
        match = VARIANT_KEY_RE.fullmatch(slot)
        assert match is not None  # deliverable_variants checked it
        role, lang = match.groups()
        entry = BaseCv(
            slot=slot,
            label=labels.get(role, role.capitalize()),
            lang=lang.lower(),
            file=files[slot],
            origin="uploaded" if any_created else "own",
        )
        if source is not None and state in _CREATED:
            entry = replace(
                entry,
                origin="lazyhunt",
                template=source.template,
                sections=tuple((s.kind, len(s.items)) for s in source.sections),
                stale=state == "stale_claims",
                adaptable=state == "current",
            )
        out.append(entry)
    return out


def pdf_path(profile_dir: Path | None, slot: str) -> Path:
    """The verified stored PDF of ``slot`` (:class:`CatalogError` otherwise)."""
    try:
        return verified_base_pdf(slot, resolve_profile_paths(profile_dir).base_pdfs_dir)
    except (CvgenError, OSError) as exc:
        raise CatalogError("bot.cvs.err.missing") from exc


def created_source(profile_dir: Path | None, slot: str) -> CvSource:
    """The source of a slot lazyhunt created (stale claims allowed)."""
    if profile_dir is None or VARIANT_KEY_RE.fullmatch(slot) is None:
        raise CatalogError("bot.cvs.err.not_created")
    paths = resolve_profile_paths(profile_dir)
    state, source = source_state(paths.base_pdfs_dir, slot, claim_ids_in(paths.claims))
    if source is None or state not in _CREATED:
        raise CatalogError("bot.cvs.err.not_created")
    return source


def _store(profile_dir: Path, source: CvSource) -> CvSource:
    """Render ``source`` over the slot's stored PDF, then write its source
    and the manifest sha256; all or nothing. Holds the caller's lock."""
    base = U.base_dir(profile_dir)
    entry = manifest_entry(source.slot, base)
    target = base / entry.file
    snap = Snapshot.take([], [base])
    part = base / f".{entry.file}.part"
    try:
        render_source(source, part)
        sha = U.sha256_of(part)
        os.replace(part, target)
        stored = replace(source, pdf_sha256=sha)
        save_source(source_path(base, source.slot), stored)
        manifest_path = base / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["variants"][source.slot]["sha256"] = sha
        atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
    except BaseException:
        part.unlink(missing_ok=True)
        snap.restore()
        raise
    return stored


def change_template(profile_dir: Path | None, slot: str, template: str) -> CvSource:
    """Re-render a created CV from its source with ``template``."""
    if template not in TEMPLATES:
        raise CatalogError("bot.cvs.err.not_created")
    with WRITE_LOCK:
        source = created_source(profile_dir, slot)
        assert profile_dir is not None
        return _guarded(profile_dir, source.with_template(template))


def rebuild(profile_dir: Path | None, slot: str) -> CvSource:
    """Render a created CV again from the current ``/setup`` answers, with
    the template it has."""
    with WRITE_LOCK:
        old = created_source(profile_dir, slot)
        assert profile_dir is not None
        answers = current_answers(profile_dir)
        target = next(
            (s for s in U.slots(profile_dir, answers, "cvs") if s.key == slot), None
        )
        if target is None:  # the family or the language is no longer declared
            raise CatalogError("bot.cvs.err.no_slot")
        try:
            source = starter_source(
                answers, target, old.template, identity_claim_id(profile_dir)
            )
        except ProfileError as exc:
            raise CatalogError("bot.cvs.err.failed") from exc
        return _guarded(profile_dir, source)


def _guarded(profile_dir: Path, source: CvSource) -> CvSource:
    try:
        return _store(profile_dir, source)
    except (CvgenError, OSError, ValueError, KeyError, TypeError, ProfileError) as exc:
        raise CatalogError("bot.cvs.err.failed") from exc


# --- the Telegram message --------------------------------------------------------


def template_name(template: str, lang: Lang) -> str:
    return t(f"onboarding.cv.tpl.{template}", lang)


def _section_line(entry: BaseCv, lang: Lang) -> str:
    return " · ".join(
        f"{t(f'bot.cvs.section.{kind}', lang)} {count}"
        for kind, count in entry.sections
    )


def catalog_text(entries: list[BaseCv], lang: Lang, setup: str | None = None) -> str:
    """The ``/cvs`` message: one block per slot. ``setup`` is the ``/setup``
    command name (``None`` on a legacy install, where ``/setup`` refuses)."""
    if not entries:
        key = "bot.cvs.empty" if setup is not None else "bot.cvs.empty_legacy"
        return t(key, lang, setup=setup or "")
    lines = [t("bot.cvs.header", lang)]
    for entry in entries:
        if entry.origin == "lazyhunt":
            origin = t(
                "bot.cvs.origin.lazyhunt",
                lang,
                template=template_name(entry.template or "", lang),
            )
        else:
            origin = t(f"bot.cvs.origin.{entry.origin}", lang)
        lines.extend(["", f"• {entry.name} — {origin}"])
        if entry.sections:
            lines.append(f"  {_section_line(entry, lang)}")
        if entry.stale:
            lines.append(f"  {t('bot.cvs.stale', lang)}")
    created = any(e.origin == "lazyhunt" for e in entries)
    if created and setup is not None and any(e.origin == "uploaded" for e in entries):
        lines.extend(["", t("bot.cvs.create_hint", lang, setup=setup)])
    lines.extend(["", t("bot.cvs.legend" if created else "bot.cvs.legend_own", lang)])
    return "\n".join(lines)


def catalog_buttons(entries: list[BaseCv]) -> list[list[tuple[str, str]]]:
    """``(label, callback data)`` rows: one per slot; "📄" sends the PDF,
    "🎨"/"🔄" (created CVs only) change the template or rebuild it."""
    rows: list[list[tuple[str, str]]] = []
    for entry in entries:
        row = [(f"📄 {entry.name}", f"{PREFIX}:pdf:{entry.slot}")]
        if entry.origin == "lazyhunt":
            row.append((f"🎨 {entry.name}", f"{PREFIX}:tpl:{entry.slot}"))
            row.append((f"🔄 {entry.name}", f"{PREFIX}:re:{entry.slot}"))
        rows.append(row)
    return rows


def template_buttons(
    slot: str, current: str, lang: Lang
) -> list[list[tuple[str, str]]]:
    """The other templates of a created CV, one button each."""
    return [
        [(f"🎨 {template_name(tpl, lang)}", f"{PREFIX}:set:{slot}:{tpl}")]
        for tpl in TEMPLATES
        if tpl != current
    ]


def profile_entries(
    profile_dir: Path, families: FamilyRegistry
) -> list[dict[str, object]]:
    """The read-only ``base_cvs`` list of ``GET /profile``."""
    return [
        {
            "slot": e.slot,
            "label": e.label,
            "lang": e.lang,
            "origin": e.origin,
            "template": e.template,
            "sections": len(e.sections),
            "adaptable": e.adaptable,
        }
        for e in base_cvs(profile_dir, families)
    ]
