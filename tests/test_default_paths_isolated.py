"""Default output locations never point at the real repository during tests.

Production resolves ``adaptadas/``, ``data/export.xlsx`` and ``data/`` (rejudge
reports) at call time from module constants; ``tests/conftest.py`` redirects
those constants under ``tmp_path`` so no test can leak files into the repo.
"""

import asyncio
import sqlite3
from pathlib import Path

from cva.claimsdb import load_claims
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.profile_paths import ProfilePaths, resolve_profile_paths
from cva.service import JobService
from cva.tracker import OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent


def _default_service(
    tmp_path: Path, profile_paths: ProfilePaths | None = None
) -> JobService:
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    db = load_claims(DEMO_CLAIMS)
    return JobService(
        repo=OfferRepository(conn),
        llm=None,  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        profile_paths=profile_paths,
    )


def test_default_output_dir_is_isolated_from_repo(tmp_path: Path) -> None:
    service = _default_service(tmp_path)

    assert service._output_dir.is_absolute()
    assert service._output_dir != _ROOT / "adaptadas"


def test_default_export_path_is_isolated_from_repo(tmp_path: Path) -> None:
    service = _default_service(tmp_path)

    exported = service.export()

    assert exported.is_absolute()
    assert exported.exists()
    assert _ROOT not in exported.parents


def test_default_rejudge_report_dir_is_isolated_from_repo(tmp_path: Path) -> None:
    service = _default_service(tmp_path)

    result = asyncio.run(service.rejudge_backlog(report_only=True))

    assert result.report_path is not None
    assert result.report_path.is_absolute()
    assert _ROOT not in result.report_path.parents


def test_default_owner_data_dirs_are_the_legacy_repo_locations(tmp_path: Path) -> None:
    """profile_dir unset: masters/base PDFs resolve exactly as before (no moves)."""
    service = _default_service(tmp_path, resolve_profile_paths(None))

    assert service._masters_dir == Path("masters")
    assert service._base_pdfs_dir == _ROOT / "cv_base_pdfs"
