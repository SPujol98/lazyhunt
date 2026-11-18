"""Fixed base CVs (fixed-base-cvs): the owner's hand-reviewed PDFs, delivered as-is.

``cv_base_pdfs/manifest.json`` maps each bot variant to a file name and its
sha256 (written by ``scripts/sync_base_cvs.py``). Delivery verifies the hash,
then copies the bytes to the tailored output path: nothing is rendered.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Final

from cva.cvgen.master import CvgenError, CvVariant
from cva.profile_paths import resolve_profile_paths

MANIFEST_NAME: Final[str] = "manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_entry(variant: CvVariant, base_dir: Path) -> dict[str, str]:
    manifest_path = base_dir / MANIFEST_NAME
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["variants"][variant.value]
        return {"file": str(entry["file"]), "sha256": str(entry["sha256"])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CvgenError(
            f"no hay CV base para {variant.value}: manifiesto ilegible o sin "
            f"entrada ({manifest_path}); ejecuta scripts/sync_base_cvs.py"
        ) from exc


def _base_dir(base_dir: Path | None) -> Path:
    """``base_dir``, or the profile's base PDFs dir (today's ``cv_base_pdfs/``)."""
    return base_dir if base_dir is not None else resolve_profile_paths().base_pdfs_dir


def deliverable_variants(base_dir: Path | None = None) -> frozenset[CvVariant]:
    """Variants the bot can deliver: the ones listed in the manifest.

    Reads ``<base_dir>/manifest.json`` (the profile's base PDFs dir; today's
    ``cv_base_pdfs/`` when unset). ``scripts/sync_base_cvs.py`` reads the same
    file for the names it syncs.
    """
    manifest_path = _base_dir(base_dir) / MANIFEST_NAME
    try:
        names = list(json.loads(manifest_path.read_text(encoding="utf-8"))["variants"])
        return frozenset(CvVariant(name) for name in names)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CvgenError(
            f"manifiesto ilegible o con variantes desconocidas ({manifest_path}): {exc}"
        ) from exc


def verified_base_pdf(variant: CvVariant, base_dir: Path | None = None) -> Path:
    """Path of the base PDF for ``variant`` once its sha256 matches the manifest."""
    base_dir = _base_dir(base_dir)
    entry = _manifest_entry(variant, base_dir)
    path = base_dir / entry["file"]
    if not path.is_file():
        raise CvgenError(f"CV base {variant.value} no encontrado: {path}")
    if _sha256(path) != entry["sha256"]:
        raise CvgenError(
            f"CV base {variant.value} no coincide con el manifiesto (sha256): "
            f"{path}; ejecuta scripts/sync_base_cvs.py --check"
        )
    return path


def deliver_base_pdf(
    variant: CvVariant, out: Path, base_dir: Path | None = None
) -> Path:
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


def base_cambios(variant: CvVariant, base_dir: Path | None = None) -> str:
    """``cambios`` text naming the delivered base: variant + manifest sha prefix.

    Derived from the manifest so a later sync never mislabels new rows.
    """
    sha = _manifest_entry(variant, _base_dir(base_dir))["sha256"][:8]
    return f"CV base fijo {variant.value} ({sha}), sin cambios por oferta"
