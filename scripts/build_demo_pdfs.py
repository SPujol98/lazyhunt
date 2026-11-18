"""Regenerate the demo profile's base CV PDFs and manifest from its masters.

The five deliverable PDFs of ``profiles/demo/cv_base_pdfs`` are rendered by the
engine (``cva.cvgen.engine``) from ``profiles/demo/masters`` with the neutral
plan, so they are reproducible: the PDF creation date is pinned, which makes
the bytes (and the sha256 in ``manifest.json``) stable for a given engine,
fpdf2 version and font set. The demo PDFs are public assets, so they always
use the redistributable Carlito font (``--font carlito``, the default) and do
not follow the owner's ``cv_font`` setting; ``--font calibri`` exists only for
local comparisons and must never be committed. Run::

    uv run python scripts/build_demo_pdfs.py             # rewrite PDFs + manifest
    uv run python scripts/build_demo_pdfs.py --check     # verify, change nothing
    uv run python scripts/build_demo_pdfs.py --profile-dir path/to/profile

``--check`` exits 1 when the committed PDFs do not hash to ``manifest.json``,
when the manifest and the files on disk list different PDFs, when the manifest
is missing or unreadable, or (only when the recorded fpdf2 / font environment
matches the running one) when a regenerated PDF differs from the manifest.
"""

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf import __version__ as fpdf_version

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cva.claimsdb import load_claims
from cva.cvgen import (
    CvVariant,
    engine,
    load_master,
)
from cva.cvgen.fonts import DEFAULT_CV_FONT, FONT_DIR, CvFont, font_files
from cva.cvgen.plan import neutral_plan
from cva.profile_identity import load_identity
from cva.profile_paths import resolve_profile_paths

DEFAULT_PROFILE = _REPO_ROOT / "profiles" / "demo"
# Variant -> delivered file name (without the ``.pdf`` the owner slug precedes).
_NAMES: dict[CvVariant, str] = {
    CvVariant.AI_ES: "CV_AI_Engineer_{slug}.pdf",
    CvVariant.AI_EN: "CV_AI_Engineer_{slug}_EN.pdf",
    CvVariant.AI_CA: "CV_AI_Engineer_{slug}_CA.pdf",
    CvVariant.BACKEND_ES: "CV_Backend_Developer_Python_{slug}.pdf",
    CvVariant.BACKEND_EN: "CV_Backend_Developer_Python_{slug}_EN.pdf",
}
_FIXED_DATE = datetime(2026, 1, 1, tzinfo=UTC)


class _DeterministicFPDF(FPDF):
    """FPDF with a pinned creation date (the only per-run varying field)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.creation_date = _FIXED_DATE


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def environment(font: CvFont = DEFAULT_CV_FONT) -> dict[str, Any]:
    """What the bytes depend on besides the masters: fpdf2 and the font files."""
    return {
        "fpdf2": fpdf_version,
        "fonts": {
            path.relative_to(FONT_DIR).as_posix(): _sha256(path)
            for path in sorted(font_files(font).values())
        },
    }


def read_manifest(base_dir: Path) -> dict[str, Any]:
    """The parsed ``manifest.json`` of ``base_dir``; ``SystemExit`` when unusable."""
    path = base_dir / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data["variants"], dict):
            raise TypeError("'variants' must be an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"{path}: manifest missing or unreadable ({exc})") from exc
    manifest: dict[str, Any] = data
    return manifest


def check_disk(base_dir: Path, manifest: dict[str, Any]) -> list[str]:
    """Problems between ``manifest.json`` and the PDFs committed beside it."""
    problems: list[str] = []
    listed = {str(e["file"]): str(e["sha256"]) for e in manifest["variants"].values()}
    for name, sha in sorted(listed.items()):
        pdf = base_dir / name
        if not pdf.is_file():
            problems.append(f"{name}: listed in the manifest but missing on disk")
        elif _sha256(pdf) != sha:
            problems.append(f"{name}: sha256 differs from the manifest")
    for pdf in sorted(base_dir.glob("*.pdf")):
        if pdf.name not in listed:
            problems.append(f"{pdf.name}: on disk but not listed in the manifest")
    return problems


def build_all(
    profile_dir: Path, out_dir: Path, font: CvFont = DEFAULT_CV_FONT
) -> dict[str, dict[str, str]]:
    """Render the five deliverables into ``out_dir``; return the manifest entries."""
    paths = resolve_profile_paths(profile_dir)
    if paths.profile_toml is None or not paths.profile_toml.is_file():
        raise SystemExit(f"{profile_dir}: profile.toml is required (file_slug)")
    identity = load_identity(paths.profile_toml)
    db = load_claims(paths.claims)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, str]] = {}
    original = engine.FPDF
    engine.FPDF = _DeterministicFPDF  # type: ignore[misc]
    try:
        for variant, pattern in _NAMES.items():
            name = pattern.format(slug=identity.file_slug)
            master = load_master(variant, paths.masters_dir, db)
            result = engine.render(
                master,
                neutral_plan(variant),
                out=out_dir / name,
                db=db,
                identity=identity,
                font=font,
            )
            if result.violations:
                raise SystemExit(f"{variant.value}: {'; '.join(result.violations)}")
            entries[variant.value] = {"file": name, "sha256": _sha256(result.pdf_path)}
    finally:
        engine.FPDF = original  # type: ignore[misc]
    return entries


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--check", action="store_true", help="verify only")
    parser.add_argument(
        "--font",
        choices=["carlito", "calibri"],
        default=DEFAULT_CV_FONT,
        help="CV font family (default carlito: the committed demo PDFs are public)",
    )
    args = parser.parse_args(argv)
    target = args.profile_dir / "cv_base_pdfs"
    with tempfile.TemporaryDirectory() as tmp:
        entries = build_all(args.profile_dir, Path(tmp), args.font)
        manifest_path = target / "manifest.json"
        if args.check:
            manifest = read_manifest(target)
            problems = check_disk(target, manifest)
            recorded = manifest.get("environment")
            if recorded != environment(args.font):
                print(
                    "regeneration skipped: fpdf2/font environment differs from "
                    "the one recorded in manifest.json",
                    file=sys.stderr,
                )
            elif manifest["variants"] != entries:
                problems.append("regenerated PDFs differ from manifest.json")
            if problems:
                print("\n".join(problems), file=sys.stderr)
                return 1
            print("demo base PDFs match manifest.json")
            return 0
        target.mkdir(parents=True, exist_ok=True)
        for entry in entries.values():
            (target / entry["file"]).write_bytes(
                (Path(tmp) / entry["file"]).read_bytes()
            )
    manifest_path.write_text(
        json.dumps(
            {"environment": environment(args.font), "variants": entries}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    for variant, entry in entries.items():
        print(f"{variant}: {entry['file']} {entry['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
