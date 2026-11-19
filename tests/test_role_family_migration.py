"""v23 (role-families T2): offers.perfil / offers.cv_base accept any role family.

The v22 CHECKs only accepted the built-in vocabularies (``perfil IN
('ai','backend')`` and a closed 7-value ``cv_base`` list). v23 rebuilds
``offers`` with shape checks instead: ``perfil`` is a family id
(``[a-z][a-z0-9_]*``) and ``cv_base`` is ``<ROLE>_<LANG>`` in upper case.
"""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from cva.tracker import OfferInput, OfferRepository

# The v22 CHECKs, verbatim from the v7/v13 DDL; used to rebuild a real v22 table.
_V22_PERFIL_CHECK = "CHECK(perfil IN ('ai','backend'))"
_V22_CV_BASE_CHECK = (
    "CHECK(cv_base IN "
    "('AI_ES','AI_EN','DS_ES','DS_EN','AI_CA','BACKEND_ES','BACKEND_EN'))"
)
_LEGACY_CV_BASES = (
    "AI_ES",
    "AI_EN",
    "DS_ES",
    "DS_EN",
    "AI_CA",
    "BACKEND_ES",
    "BACKEND_EN",
)


def _offer(n: int, **overrides: object) -> OfferInput:
    fields: dict[str, object] = {
        "job_id": str(n),
        "title": f"Engineer {n}",
        "company": f"Acme {n}",
        "url": f"https://www.linkedin.com/jobs/view/{n}",
        "text": f"offer text {n}",
        "source": "link",
    }
    fields.update(overrides)
    return OfferInput(**fields)


def _offers_ddl(conn: sqlite3.Connection) -> str:
    (ddl,) = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='offers'"
    ).fetchone()
    return str(ddl)


def _indexes_and_triggers(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE tbl_name='offers' AND type IN ('index','trigger')"
        )
    )


def _dump(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [tuple(r) for r in conn.execute("SELECT * FROM offers ORDER BY id")]


def _downgrade_to_v22(conn: sqlite3.Connection) -> None:
    """Rebuild ``offers`` with the v22 closed-list CHECKs; rewind user_version."""
    from cva.tracker.repo import _CV_BASE_CHECK_V23, _PERFIL_CHECK_V23

    ddl = _offers_ddl(conn)
    assert _PERFIL_CHECK_V23 in ddl
    assert _CV_BASE_CHECK_V23 in ddl
    old = (
        ddl.replace(_PERFIL_CHECK_V23, _V22_PERFIL_CHECK)
        .replace(_CV_BASE_CHECK_V23, _V22_CV_BASE_CHECK)
        .replace('CREATE TABLE "offers"', "CREATE TABLE offers_old")
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(old)
    conn.executescript(
        "INSERT INTO offers_old SELECT * FROM offers;"
        "DROP TABLE offers; ALTER TABLE offers_old RENAME TO offers;"
        "CREATE INDEX idx_offers_dedup_key ON offers (dedup_key);"
        "PRAGMA user_version = 22;"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _v22_database(path: Path) -> list[int]:
    """A v22 database holding every legacy perfil and cv_base value."""
    conn = sqlite3.connect(path)
    repo = OfferRepository(conn)
    ids: list[int] = []
    perfiles: list[str | None] = ["ai", "backend", None]
    for n, cv_base in enumerate((*_LEGACY_CV_BASES, None), start=1):
        offer_id = repo.upsert_offer(
            _offer(n, profile=perfiles[n % 3], location=f"City {n}")
        )
        if cv_base is not None:
            repo.record_cv(
                offer_id, cv_base=cv_base, cv_path=f"/cv/{n}.pdf", cambios="c"
            )
        ids.append(offer_id)
    conn.execute(
        "INSERT INTO match_results (offer_id, score, threshold, breakdown_json) "
        "VALUES (?, 80, 65, '{}')",
        (ids[0],),
    )
    conn.commit()
    _downgrade_to_v22(conn)
    conn.close()
    return ids


def test_v22_database_migrates_to_v23_preserving_every_row(tmp_path: Path) -> None:
    path = tmp_path / "v22.db"
    ids = _v22_database(path)
    legacy = sqlite3.connect(path)
    before = _dump(legacy)
    columns = [r[1] for r in legacy.execute("PRAGMA table_info(offers)")]
    pre_schema = _indexes_and_triggers(legacy)
    assert {r[-1] for r in pre_schema if r[0] == "index"}
    # The fixture really is v22: a non-built-in family is rejected.
    with pytest.raises(sqlite3.IntegrityError):
        legacy.execute("UPDATE offers SET perfil = 'frontend' WHERE id = ?", (ids[0],))
    legacy.rollback()
    assert {r[0] for r in legacy.execute("SELECT DISTINCT perfil FROM offers")} == {
        "ai",
        "backend",
        None,
    }
    assert {r[0] for r in legacy.execute("SELECT DISTINCT cv_base FROM offers")} == {
        *_LEGACY_CV_BASES,
        None,
    }
    legacy.close()

    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 24
    assert [r[1] for r in migrated.execute("PRAGMA table_info(offers)")] == columns
    assert _dump(migrated) == before
    assert _indexes_and_triggers(migrated) == pre_schema
    assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [r[0] for r in migrated.execute("SELECT offer_id FROM match_results")] == [
        ids[0]
    ]


def test_v23_stores_a_non_builtin_family_through_the_repository(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v22.db"
    _v22_database(path)
    conn = sqlite3.connect(path)
    repo = OfferRepository(conn)
    offer_id = repo.upsert_offer(_offer(100, profile="frontend"))
    repo.record_cv(offer_id, cv_base="FRONTEND_ES", cv_path="/cv/f.pdf", cambios="c")
    row = repo.get_offer(offer_id)
    assert row is not None
    assert (row.perfil, row.cv_base) == ("frontend", "FRONTEND_ES")
    # A family id with digits/underscores maps to an upper-case role.
    other = repo.upsert_offer(_offer(101, profile="data_science2"))
    repo.record_cv(other, cv_base="DATA_SCIENCE2_CA", cv_path="/cv/d.pdf", cambios="")
    stored = repo.get_offer(other)
    assert stored is not None
    assert (stored.perfil, stored.cv_base) == ("data_science2", "DATA_SCIENCE2_CA")


def test_fresh_database_has_the_same_offers_schema_as_a_migrated_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v22.db"
    _v22_database(path)
    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    fresh = sqlite3.connect(tmp_path / "fresh.db")
    OfferRepository(fresh)
    assert _offers_ddl(fresh) == _offers_ddl(migrated)
    assert _indexes_and_triggers(fresh) == _indexes_and_triggers(migrated)
    assert [tuple(r) for r in fresh.execute("PRAGMA table_info(offers)")] == [
        tuple(r) for r in migrated.execute("PRAGMA table_info(offers)")
    ]
    assert fresh.execute("PRAGMA user_version").fetchone()[0] == 24


def test_v23_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "v22.db"
    _v22_database(path)
    first = sqlite3.connect(path)
    OfferRepository(first)
    once = (_offers_ddl(first), _dump(first), _indexes_and_triggers(first))
    first.close()
    again = sqlite3.connect(path)
    OfferRepository(again)
    OfferRepository(again)
    assert (_offers_ddl(again), _dump(again), _indexes_and_triggers(again)) == once
    assert again.execute("PRAGMA user_version").fetchone()[0] == 24
    assert (
        again.execute(
            "SELECT count(*) FROM sqlite_master WHERE name LIKE 'offers_v%'"
        ).fetchone()[0]
        == 0
    )


def test_migration_v23_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "v22.db"
    _v22_database(path)
    legacy = sqlite3.connect(path)
    before = (_offers_ddl(legacy), _dump(legacy))
    legacy.close()
    broken = repo_mod._MIGRATION_V23.replace(
        "PRAGMA user_version = 23;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 23;",
    )
    assert broken != repo_mod._MIGRATION_V23
    monkeypatch.setattr(repo_mod, "_MIGRATION_V23", broken)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 22
    assert (_offers_ddl(conn), _dump(conn)) == before
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("perfil", "Frontend"),
        ("perfil", "1ai"),
        ("perfil", "front-end"),
        ("perfil", ""),
        ("cv_base", "frontend_es"),
        ("cv_base", "FRONTEND"),
        ("cv_base", "FRONTEND_ESP"),
        ("cv_base", "_ES"),
        ("cv_base", "FRONT END_ES"),
    ],
)
def test_v23_rejects_values_outside_the_family_shape(
    tmp_path: Path, column: str, value: str
) -> None:
    conn = sqlite3.connect(tmp_path / "fresh.db")
    repo = OfferRepository(conn)
    offer_id = repo.upsert_offer(_offer(1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE offers SET {column} = ? WHERE id = ?", (value, offer_id))
    conn.rollback()


@pytest.mark.parametrize("profile", ["Frontend", "front-end", "1ai", ""])
def test_offer_input_rejects_an_invalid_family_id(profile: str) -> None:
    with pytest.raises(ValidationError):
        _offer(1, profile=profile)


def test_builtin_profiles_still_upsert_unchanged(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "fresh.db")
    repo = OfferRepository(conn)
    ai = repo.upsert_offer(_offer(1, profile="ai"))
    backend = repo.upsert_offer(_offer(2, profile="backend"))
    unset = repo.upsert_offer(_offer(3))
    repo.record_cv(backend, cv_base="BACKEND_EN", cv_path="/cv/b.pdf", cambios="")
    # A refresh fills perfil in but never overwrites it.
    repo.upsert_offer(_offer(1, profile="backend"))
    repo.upsert_offer(_offer(3, profile="ai"))
    rows = {i: repo.get_offer(i) for i in (ai, backend, unset)}
    assert {i: (r.perfil if r else None) for i, r in rows.items()} == {
        ai: "ai",
        backend: "backend",
        unset: "ai",
    }
    stored = rows[backend]
    assert stored is not None
    assert (stored.cv_base, stored.estado) == ("BACKEND_EN", "preparada")
