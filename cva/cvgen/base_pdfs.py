"""Fixed base CVs (fixed-base-cvs): the owner's hand-reviewed PDFs, delivered as-is.

``cv_base_pdfs/manifest.json`` maps each bot variant to a file name and its
sha256 (written by ``scripts/sync_base_cvs.py``). Delivery verifies the hash,
then copies the bytes to the tailored output path: nothing is rendered.

A variant key is ``<ROLE>_<LANG>`` (role-families T3): ``ROLE`` is a role
family's ``id.upper()`` and ``LANG`` one of :data:`cva.families.CV_LANGUAGES`
upper-cased, e.g. ``AI_ES`` or ``FRONTEND_EN``. Any family the profile declares
gets its base CVs from here with no code change. An entry may carry an
optional ``delivery_name`` (the role part of the delivered file name)::

    "FRONTEND_EN": {"file": "CV_Frontend_EN.pdf", "sha256": "...",
                    "delivery_name": "Frontend_Developer"}

The functions take the key as a plain string; a :class:`CvVariant` member is
one too.
"""

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Final, NamedTuple

from cva.cvgen.master import CvgenError
from cva.families import CV_LANGUAGES, FamilyRegistry
from cva.profile_paths import resolve_profile_paths

MANIFEST_NAME: Final[str] = "manifest.json"

# ``<ROLE>_<LANG>``: ROLE is a family id upper-cased (FAMILY_ID_RE), LANG a CV language.
VARIANT_KEY_RE: Final[re.Pattern[str]] = re.compile(
    "([A-Z][A-Z0-9_]*)_(" + "|".join(code.upper() for code in CV_LANGUAGES) + ")"
)


class BaseCvEntry(NamedTuple):
    """One manifest entry: file name, sha256 and the optional delivered name."""

    file: str
    sha256: str
    delivery_name: str | None


def variant_key(role: str, lang: str) -> str:
    """The manifest key for a family role and a CV language (``AI``, ``es`` -> ``AI_ES``)."""
    return f"{role}_{lang.upper()}"


def family_allows(registry: FamilyRegistry, variant: str) -> bool:
    """True when ``variant`` names a declared family (by role) in one of its languages."""
    match = VARIANT_KEY_RE.fullmatch(variant)
    if match is None:
        return False
    role, lang = match.groups()
    return any(
        family.role == role and lang.lower() in family.languages
        for family in registry.families
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_entry(variant: str, base_dir: Path | None = None) -> BaseCvEntry:
    """The manifest entry for ``variant``; :class:`CvgenError` when missing."""
    manifest_path = _base_dir(base_dir) / MANIFEST_NAME
    key = str(variant)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["variants"][key]
        delivery_name = entry.get("delivery_name")
        if delivery_name is not None and not isinstance(delivery_name, str):
            raise TypeError("delivery_name must be a string")
        return BaseCvEntry(str(entry["file"]), str(entry["sha256"]), delivery_name)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CvgenError(
            f"no hay CV base para {key}: manifiesto ilegible o sin "
            f"entrada ({manifest_path}); ejecuta scripts/sync_base_cvs.py"
        ) from exc


def _base_dir(base_dir: Path | None) -> Path:
    """``base_dir``, or the profile's base PDFs dir (today's ``cv_base_pdfs/``)."""
    return base_dir if base_dir is not None else resolve_profile_paths().base_pdfs_dir


def deliverable_variants(
    base_dir: Path | None = None, registry: FamilyRegistry | None = None
) -> frozenset[str]:
    """Variant keys the bot can deliver: the ones listed in the manifest.

    Reads ``<base_dir>/manifest.json`` (the profile's base PDFs dir; today's
    ``cv_base_pdfs/`` when unset). ``scripts/sync_base_cvs.py`` reads the same
    file for the names it syncs. A key that is not ``<ROLE>_<LANG>`` is an
    error; a well-formed key of a family the profile does not declare is
    listed, and dropped when ``registry`` is given (with the keys in a
    language its family does not allow).
    """
    manifest_path = _base_dir(base_dir) / MANIFEST_NAME
    try:
        names = [
            str(name)
            for name in json.loads(manifest_path.read_text(encoding="utf-8"))[
                "variants"
            ]
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CvgenError(
            f"manifiesto ilegible o con variantes desconocidas ({manifest_path}): {exc}"
        ) from exc
    bad = [name for name in names if VARIANT_KEY_RE.fullmatch(name) is None]
    if bad:
        raise CvgenError(
            f"manifiesto ilegible o con variantes desconocidas ({manifest_path}): "
            f"{', '.join(bad)} (se espera <ROL>_<IDIOMA>, idioma en "
            f"{'/'.join(c.upper() for c in CV_LANGUAGES)})"
        )
    if registry is not None:
        names = [name for name in names if family_allows(registry, name)]
    return frozenset(names)


def verified_base_pdf(variant: str, base_dir: Path | None = None) -> Path:
    """Path of the base PDF for ``variant`` once its sha256 matches the manifest."""
    base_dir = _base_dir(base_dir)
    entry = manifest_entry(variant, base_dir)
    path = base_dir / entry.file
    if not path.is_file():
        raise CvgenError(f"CV base {variant} no encontrado: {path}")
    if _sha256(path) != entry.sha256:
        raise CvgenError(
            f"CV base {variant} no coincide con el manifiesto (sha256): "
            f"{path}; ejecuta scripts/sync_base_cvs.py --check"
        )
    return path


def deliver_base_pdf(variant: str, out: Path, base_dir: Path | None = None) -> Path:
    """Copy the verified base PDF byte-for-byte to ``out`` and return ``out``."""
    source = verified_base_pdf(variant, _base_dir(base_dir))
    out.parent.mkdir(parents=True, exist_ok=True)
    # Temp file + replace: an interrupted copy never leaves a truncated PDF
    # at the tailored path that the mirror or mark_sent could pick up.
    tmp = out.with_name(f".{out.name}.tmp")
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def base_cambios(variant: str, base_dir: Path | None = None) -> str:
    """``cambios`` text naming the delivered base: variant + manifest sha prefix.

    Derived from the manifest so a later sync never mislabels new rows.
    """
    sha = manifest_entry(variant, base_dir).sha256[:8]
    return f"CV base fijo {variant} ({sha}), sin cambios por oferta"
