"""Base CV PDFs uploaded in ``/setup`` (onboarding-v2 T5, S5).

Step 9 asks for one PDF per *slot*: a ``<ROLE>_<LANG>`` key of
:mod:`cva.cvgen.base_pdfs` for every family the answers keep and every CV
language that family ends up with (computed by running the answers through
the same document builder and strict loader Confirm uses, so the slots are
exactly the variants the bot can then deliver).

An upload is validated (PDF name or MIME type, size checked before the
download, ``%PDF-`` magic, a page count pypdf can read) and kept in a pending
area inside the profile directory (:data:`PENDING_DIR`). Only Confirm copies it
to ``cv_base_pdfs/`` and records it in ``cv_base_pdfs/manifest.json`` (merged:
variants the user did not replace stay as they are, extra per-entry keys such
as ``delivery_name`` and top-level keys such as ``environment`` survive).
The pending area stays until a Confirm fully succeeds (a failed one is
undone, see :mod:`cva.onboarding.snapshot`, and can be retried without
uploading again); Cancel, expiry or a new ``/setup`` remove it.

A slot whose variant the profile's manifest already lists, with a PDF that
still matches its sha256, is :func:`existing`: it needs no upload.

Stored file name, following the demo profile's convention
(``CV_AI_Engineer_Alex_Rivera_EN.pdf``)::

    CV_<family label slug>_<identity file_slug>[_<LANG>].pdf   # no suffix for ES

It never uses the name the user's file had (no path from Telegram reaches
the disk).

A PDF lazyhunt created (:mod:`cva.onboarding.starter`) has its structured
source pending next to it (:func:`pending_source`); Confirm stores it as
``cv_base_pdfs/<SLOT>.source.json`` (:mod:`cva.cvgen.cv_source`) and an
uploaded PDF removes the slot's stored source.
"""

import hashlib
import json
import os
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from pypdf import PdfReader

from cva.cvgen.base_pdfs import (
    MANIFEST_NAME,
    VARIANT_KEY_RE,
    variant_key,
    verified_base_pdf,
)
from cva.cvgen.cv_source import (
    CvSourceError,
    load_source,
    save_source,
    source_path,
)
from cva.cvgen.master import CvgenError
from cva.fsutil import atomic_write
from cva.onboarding import answers as A
from cva.profile_identity import ProfileError
from cva.profile_paths import resolve_profile_paths
from cva.profile_prefs import preferences_from_data
from cva.profile_writer import read_document, toml_document

PENDING_DIR: Final[str] = ".onboarding_uploads"
MAX_BYTES: Final[int] = 10 * 1024 * 1024
MAX_PAGES: Final[int] = 10
PDF_MAGIC: Final[bytes] = b"%PDF-"
PDF_MIME: Final[str] = "application/pdf"


class UploadError(Exception):
    """A refused upload; ``key`` is the i18n key of the reason."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True)
class Slot:
    """One base CV the profile can use: a family in one CV language."""

    key: str  # ``<ROLE>_<LANG>``
    label: str  # the family's label
    lang: str  # lower-case CV language


def slots(
    profile_dir: Path, answers: Mapping[str, Any], section: str | None = None
) -> list[Slot]:
    """The slots of the profile the answers would write, in declared order
    (none when the answers do not make a valid ``preferences.toml`` yet);
    ``section`` as in :func:`cva.onboarding.answers.preferences_document`."""
    try:
        base = read_document(profile_dir / "preferences.toml")
        doc = A.preferences_document(base, answers, section)
        prefs = preferences_from_data(tomllib.loads(toml_document(doc)))
    except (ProfileError, KeyError, TypeError, ValueError):
        return []
    return [
        Slot(variant_key(family.role, lang), family.label, lang)
        for family in prefs.families.families
        for lang in family.languages
    ]


def base_dir(profile_dir: Path) -> Path:
    """Where Confirm stores the base PDFs and their manifest."""
    return resolve_profile_paths(profile_dir).base_pdfs_dir


def existing(profile_dir: Path, all_slots: list[Slot]) -> set[str]:
    """Keys of ``all_slots`` the profile already has a verified base PDF for."""
    try:
        folder = base_dir(profile_dir)
    except ProfileError:
        return set()
    found: set[str] = set()
    for slot in all_slots:
        try:
            verified_base_pdf(slot.key, folder)
        except (CvgenError, OSError):
            continue
        found.add(slot.key)
    return found


def pending_dir(profile_dir: Path) -> Path:
    return profile_dir / PENDING_DIR


def discard_pending(profile_dir: Path) -> None:
    """Remove every upload not confirmed yet."""
    shutil.rmtree(pending_dir(profile_dir), ignore_errors=True)


def check_document(
    file_name: str | None, mime_type: str | None, size: int | None
) -> None:
    """Refuse what is not a PDF, or too big, before downloading anything."""
    name_ok = file_name is None or file_name.lower().endswith(".pdf")
    mime_ok = mime_type is None or mime_type.lower() == PDF_MIME
    if not (name_ok and mime_ok) or (file_name is None and mime_type is None):
        raise UploadError("onboarding.err.cv_not_pdf")
    if size is not None and size > MAX_BYTES:
        raise UploadError("onboarding.err.cv_too_big")


def _page_count(path: Path) -> int:
    try:
        return len(PdfReader(path).pages)
    except Exception:  # noqa: BLE001 — untrusted file: any parser failure = not a PDF
        return 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_of(path: Path) -> str:
    """The sha256 the manifest records for ``path``."""
    return _sha256(path)


def accept_download(tmp: Path, profile_dir: Path, key: str) -> tuple[str, int]:
    """Validate a downloaded file and keep it as the pending upload of ``key``.

    Returns ``(sha256, pages)``; :class:`UploadError` (and ``tmp`` removed)
    when it is not an acceptable PDF.
    """
    try:
        if VARIANT_KEY_RE.fullmatch(key) is None:
            raise UploadError("onboarding.err.cv_pick_slot")
        if tmp.stat().st_size > MAX_BYTES:
            raise UploadError("onboarding.err.cv_too_big")
        with tmp.open("rb") as handle:
            if handle.read(len(PDF_MAGIC)) != PDF_MAGIC:
                raise UploadError("onboarding.err.cv_not_pdf")
        pages = _page_count(tmp)
        if pages < 1:
            raise UploadError("onboarding.err.cv_not_pdf")
        if pages > MAX_PAGES:
            raise UploadError("onboarding.err.cv_pages")
        target = pending_dir(profile_dir) / f"{key}.pdf"
        # A new PDF for the slot: whatever source a created one had is gone
        # (:func:`cva.onboarding.starter.create_pending_cv` writes it again).
        pending_source(profile_dir, key).unlink(missing_ok=True)
        os.replace(tmp, target)
        return _sha256(target), pages
    finally:
        tmp.unlink(missing_ok=True)


def pending_source(profile_dir: Path, key: str) -> Path:
    """The structured source of the pending PDF of ``key`` when lazyhunt
    created it (:mod:`cva.cvgen.cv_source`); absent for an upload."""
    return source_path(pending_dir(profile_dir), key)


def _store_source(profile_dir: Path, target_dir: Path, key: str, sha: str) -> None:
    """Keep the source of the stored PDF of ``key`` next to it when the
    pending PDF was created by lazyhunt (and is the one stored: same sha256);
    otherwise remove the slot's stored source, which described an older PDF."""
    stored = source_path(target_dir, key)
    try:
        source = load_source(pending_source(profile_dir, key))
    except (OSError, CvSourceError):
        stored.unlink(missing_ok=True)
        return
    if source.slot != key or source.pdf_sha256 != sha:
        stored.unlink(missing_ok=True)
        return
    save_source(stored, source)


def new_temp(profile_dir: Path) -> Path:
    """A fresh temporary file in the pending area (the download target)."""
    folder = pending_dir(profile_dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=folder, prefix=".dl-", suffix=".part")
    os.close(fd)
    return Path(name)


def stored_name(slot: Slot, file_slug: str) -> str:
    """``CV_<label>_<file_slug>[_<LANG>].pdf`` (no suffix for Spanish)."""
    role = A.file_slug(slot.label) or slot.key.rsplit("_", 1)[0]
    suffix = "" if slot.lang == "es" else f"_{slot.lang.upper()}"
    return f"CV_{role}_{file_slug}{suffix}.pdf"


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"variants": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"cv_base_pdfs/{MANIFEST_NAME} unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("variants", {}), dict):
        raise ProfileError(
            f"cv_base_pdfs/{MANIFEST_NAME}: 'variants' must be an object"
        )
    data.setdefault("variants", {})
    return data


def commit(
    profile_dir: Path, received: Mapping[str, str], all_slots: list[Slot], name: str
) -> list[str]:
    """Copy the pending uploads of ``received`` (keys of ``all_slots``) into
    the profile's base PDFs dir and merge them into its manifest.

    Returns the keys stored. The pending uploads stay (the caller drops them
    once the whole Confirm succeeded). Raises :class:`ProfileError` (an
    unreadable manifest is never overwritten) or :class:`OSError`.
    """
    by_key = {slot.key: slot for slot in all_slots}
    keys = [key for key in received if key in by_key]
    if not keys:
        return []
    target_dir = base_dir(profile_dir)
    manifest_path = target_dir / MANIFEST_NAME
    manifest = _read_manifest(manifest_path)
    variants: dict[str, Any] = manifest["variants"]
    file_slug = A.file_slug(name) or "CV"
    taken = {
        str(entry.get("file")): variant
        for variant, entry in variants.items()
        if isinstance(entry, dict) and variant not in keys
    }
    plan: list[tuple[str, Path, str]] = []
    for key in keys:
        source = pending_dir(profile_dir) / f"{key}.pdf"
        if not source.is_file():
            raise ProfileError(f"upload for {key} is gone; send it again")
        file_name = stored_name(by_key[key], file_slug)
        if file_name in taken:  # another variant's PDF: never overwrite it
            file_name = f"CV_{key}_{file_slug}.pdf"
        if file_name in taken:
            raise ProfileError(
                f"cv_base_pdfs/{file_name} belongs to {taken[file_name]}"
            )
        taken[file_name] = key
        plan.append((key, source, file_name))
    target_dir.mkdir(parents=True, exist_ok=True)
    for key, source, file_name in plan:
        target = target_dir / file_name
        part = target.with_name(f".{file_name}.part")
        shutil.copyfile(source, part)
        os.replace(part, target)
        old = variants.get(key)
        sha = _sha256(target)
        variants[key] = {
            **(old if isinstance(old, dict) else {}),
            "file": file_name,
            "sha256": sha,
        }
        _store_source(profile_dir, target_dir, key, sha)
    atomic_write(manifest_path, json.dumps(manifest, indent=2) + "\n")
    return keys
