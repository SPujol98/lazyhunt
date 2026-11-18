"""The fictional demo persona every test runs as (``profiles/demo``)."""

from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEMO_DIR: Final[Path] = REPO_ROOT / "profiles" / "demo"
DEMO_CLAIMS: Final[Path] = DEMO_DIR / "claims.json"
DEMO_SYNONYMS: Final[Path] = DEMO_DIR / "synonyms.json"
DEMO_MASTERS: Final[Path] = DEMO_DIR / "masters"
DEMO_BASE_PDFS: Final[Path] = DEMO_DIR / "cv_base_pdfs"
