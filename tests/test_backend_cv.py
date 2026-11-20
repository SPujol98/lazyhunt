"""backend-profile T3: Backend CV masters, variant selection, role toggle and
the migration that widens the cv_base CHECK (odd/tasks/backend-profile.md)."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cva.claimsdb import load_claims, whitelist_violations
from cva.cvgen import CvVariant, load_master, master_leaf_ids
from cva.cvgen.engine import render
from cva.cvgen.plan import neutral_plan
from cva.cvgen.validators import extract_text
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.repo import _MIGRATION_V2, _SCHEMA_SQL, SCHEMA_VERSION
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

_ROOT = Path(__file__).resolve().parent.parent
MASTERS = DEMO_MASTERS
URL = "https://www.linkedin.com/jobs/view/4459804598/"


@pytest.fixture(scope="module")
def db() -> Any:
    return load_claims(DEMO_CLAIMS)


# --- masters ---------------------------------------------------------------


@pytest.mark.parametrize("variant", [CvVariant.BACKEND_ES, CvVariant.BACKEND_EN])
def test_backend_masters_load_and_reference_known_claims(
    variant: CvVariant, db: Any
) -> None:
    master = load_master(variant, MASTERS, db)
    assert master.variant == variant
    assert master_leaf_ids(master) <= set(db.by_id)
    assert master.header.tagline is not None
    assert master.header.tagline.text.startswith("Python Backend Developer")
    assert list(master.sections) == [
        "summary",
        "experience",
        "projects",
        "education",
        "certifications",
        "skills",
    ]


@pytest.mark.parametrize("variant", [CvVariant.BACKEND_ES, CvVariant.BACKEND_EN])
def test_backend_masters_render_one_page_without_violations(
    variant: CvVariant, db: Any, tmp_path: Path
) -> None:
    master = load_master(variant, MASTERS, db)
    out = tmp_path / f"{variant.value}.pdf"
    result = render(master, neutral_plan(variant), out=out, db=db)
    assert result.pages == 1
    assert result.violations == []
    text = extract_text(out)
    assert whitelist_violations(text, db, master=master) == []
    assert "FastAPI" in text and "Django" in text


def test_backend_masters_share_the_owner_approved_ai_sections(db: Any) -> None:
    for lang in ("ES", "EN"):
        backend = load_master(CvVariant[f"BACKEND_{lang}"], MASTERS, db)
        ai = load_master(CvVariant[f"AI_{lang}"], MASTERS, db)
        for section in ("experience", "education", "certifications"):
            assert backend.sections[section] == ai.sections[section]
        assert backend.header.contact == ai.header.contact


def test_backend_summary_is_backend_not_ai(db: Any) -> None:
    es = load_master(CvVariant.BACKEND_ES, MASTERS, db)
    assert es.sections["summary"].paragraphs[0].text.startswith("Desarrollador backend")
    en = load_master(CvVariant.BACKEND_EN, MASTERS, db)
    assert (
        en.sections["summary"]
        .paragraphs[0]
        .text.startswith("Junior Python backend developer")
    )


def test_backend_has_no_catalan_variant() -> None:
    assert not hasattr(CvVariant, "BACKEND_CA")


# --- migration v7 ----------------------------------------------------------


def test_migration_v7_widens_cv_base_check_and_keeps_every_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(_SCHEMA_SQL)
    legacy.executescript(_MIGRATION_V2)
    legacy.execute(
        "INSERT INTO offers (source_data, cv_base, estado) VALUES ('t', 'DS_EN', 'preparada')"
    )
    legacy.execute("PRAGMA user_version = 2")
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert SCHEMA_VERSION == 24
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    row = repo.get_offer(1)
    assert row.cv_base == "DS_EN" and row.perfil is None
    repo.record_cv(1, cv_base="BACKEND_EN", cv_path="x.pdf", cambios="c")
    assert repo.get_offer(1).cv_base == "BACKEND_EN"
    # Since v23 the CHECK is a shape rule (<ROLE>_<LANG>); per-family languages are enforced in code.
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute(
            "INSERT INTO offers (source_data, cv_base) VALUES ('t', 'backend_ca')"
        )
    columns = [r[1] for r in repo._conn.execute("PRAGMA table_info(offers)")]
    for name in ("analyze_attempts", "next_analyze_at", "perfil"):
        assert columns.count(name) == 1
    # Reopening a v7 DB is a no-op.
    assert OfferRepository(sqlite3.connect(path)).get_offer(1).cv_base == "BACKEND_EN"


def _v6_db_with_dependents(path: Path) -> int:
    """A populated DB rolled back to v6: an offer plus a FK-linked match row."""
    repo = OfferRepository(sqlite3.connect(path))
    oid = repo.upsert_offer(OfferInput(title="t", url=URL, text="x", source="link"))
    repo._conn.execute(
        "INSERT INTO match_results (offer_id, score, threshold, breakdown_json) "
        "VALUES (?, 1, 1, '{}')",
        (oid,),
    )
    repo._conn.execute("PRAGMA user_version = 6")
    repo._conn.commit()
    repo._conn.close()
    return oid


def test_migration_v7_keeps_dependent_rows(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    oid = _v6_db_with_dependents(path)
    repo = OfferRepository(sqlite3.connect(path))  # runs the v7 rebuild
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    assert repo.get_offer(oid).url_oferta == URL
    assert repo.latest_match(oid) is not None
    assert repo._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_v7_failure_rolls_back_and_keeps_offers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rebuild interrupted after DROP TABLE must not lose the offers table."""
    import cva.tracker.repo as repo_mod

    path = tmp_path / "v6.db"
    oid = _v6_db_with_dependents(path)
    broken = repo_mod._MIGRATION_V7.replace(
        "ALTER TABLE offers_v7 RENAME TO offers",
        "SELECT * FROM no_such_table;\nALTER TABLE offers_v7 RENAME TO offers",
    )
    assert broken != repo_mod._MIGRATION_V7
    monkeypatch.setattr(repo_mod, "_MIGRATION_V7", broken)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(sqlite3.connect(path))
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert conn.execute("SELECT id FROM offers").fetchall() == [(oid,)]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "offers_v7" not in tables


def test_history_import_accepts_backend_cv_base() -> None:
    from cva.tracker.history import HistoryRecord

    rec = HistoryRecord.model_validate(
        {
            "log_id": 1,
            "empresa": "A",
            "posicion": "B",
            "estado": "enviada",
            "cv_base": "BACKEND_ES",
        }
    )
    assert rec.cv_base == "BACKEND_ES"
