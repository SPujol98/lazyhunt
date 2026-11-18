"""Where the owner's persona files live: one typed resolver (profile-extraction).

With ``profile_dir`` unset every item resolves to its legacy repository
location, so nothing moves for the current owner. With ``profile_dir`` set,
each item comes from ``<profile_dir>/<name>`` (layout in
``odd/tasks/profile-extraction.md``): there is no per-item fallback, so a
missing file in a configured profile fails loudly instead of silently mixing
two personas.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

_PACKAGE_DIR: Final[Path] = Path(__file__).parent
_REPO_ROOT: Final[Path] = _PACKAGE_DIR.parent
# Anchored to the repo root so the bot works from any cwd (as before).
_LEGACY_BASE_PDFS_DIR: Final[Path] = (
    Path(__file__).resolve().parents[1] / "cv_base_pdfs"
)


@dataclass(frozen=True)
class ProfilePaths:
    """Resolved location of every owner-specific file or directory."""

    claims: Path
    synonyms: Path
    voice: Path
    salary_table: Path
    honest_gaps: Path
    masters_dir: Path
    base_pdfs_dir: Path
    # ``None`` for the legacy layout: no profile.toml exists there, so the
    # identity falls back to its defaults (cva/profile_identity.py).
    profile_toml: Path | None
    # ``None`` for the legacy layout (no preferences.toml: every preference
    # keeps its engine default, cva/profile_prefs.py).
    preferences: Path | None
    # ``None`` for the legacy layout (the owner's judge text, persona_defaults.py);
    # otherwise the directory with facts.md, anchors.md, rubric_persona.md.
    judge_dir: Path | None
    # The labeled offer bank ``judge-eval`` compares the judge against:
    # ``eval/labels.json`` in the legacy layout, ``<profile_dir>/eval/labels.json``.
    eval_labels: Path
    # The base cover letter (salutations, paragraph-1 text, closing, sign-off per
    # language): ``data/cover_letter_base.toml`` in the legacy layout.
    letter_base: Path


def resolve_profile_paths(profile_dir: Path | None = None) -> ProfilePaths:
    """Paths under ``profile_dir``, or today's locations when it is ``None``."""
    if profile_dir is None:
        return ProfilePaths(
            claims=_PACKAGE_DIR / "claimsdb" / "claims.json",
            synonyms=_PACKAGE_DIR / "match" / "synonyms.json",
            voice=_REPO_ROOT / "data" / "cover_letter_voice.md",
            salary_table=_REPO_ROOT / "data" / "salary_reference.toml",
            honest_gaps=_REPO_ROOT / "docs" / "honest_gaps.md",
            masters_dir=Path("masters"),
            base_pdfs_dir=_LEGACY_BASE_PDFS_DIR,
            profile_toml=None,
            preferences=None,
            judge_dir=None,
            eval_labels=_REPO_ROOT / "eval" / "labels.json",
            letter_base=_REPO_ROOT / "data" / "cover_letter_base.toml",
        )
    return ProfilePaths(
        claims=profile_dir / "claims.json",
        synonyms=profile_dir / "synonyms.json",
        voice=profile_dir / "voice.md",
        salary_table=profile_dir / "salary_reference.toml",
        honest_gaps=profile_dir / "honest_gaps.md",
        masters_dir=profile_dir / "masters",
        base_pdfs_dir=profile_dir / "cv_base_pdfs",
        profile_toml=profile_dir / "profile.toml",
        preferences=profile_dir / "preferences.toml",
        judge_dir=profile_dir / "judge",
        eval_labels=profile_dir / "eval" / "labels.json",
        letter_base=profile_dir / "cover_letter_base.toml",
    )
