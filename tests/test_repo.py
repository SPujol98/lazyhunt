"""Tracker repository behavior against tmp SQLite files (tracker spec, design D4/D5)."""

import sqlite3
from pathlib import Path

import pytest

from cva.tracker import OfferInput, OfferRepository, RepositoryError

S1_COLUMNS: list[str] = [
    "id",
    "fecha_envio",
    "empresa",
    "posicion",
    "origen",
    "url_oferta",
    "cv_base",
    "cv_adaptado",
    "cambios",
    "match",
    "sueldo_rango",
    "sueldo_ofrecido",
    "estado",
    "fecha_update",
    "contacto",
    "notas",
    "source_data",
]

ESTADO_VALUES: list[str] = [
    "pendiente_analisis",
    "analizada",
    "preparada",
    "descartada",
    "enviada",
    "screening",
    "entrevista_tecnica",
    "entrevista_final",
    "oferta",
    "aceptada",
    "rechazada",
    "retirada",
    "sin_respuesta",
]

EXPECTED_TYPES: list[tuple[str, str]] = [
    ("id", "INTEGER"),
    ("fecha_envio", "TEXT"),
    ("empresa", "TEXT"),
    ("posicion", "TEXT"),
    ("origen", "TEXT"),
    ("url_oferta", "TEXT"),
    ("cv_base", "TEXT"),
    ("cv_adaptado", "TEXT"),
    ("cambios", "TEXT"),
    ("match", "INTEGER"),
    ("sueldo_rango", "TEXT"),
    ("sueldo_ofrecido", "TEXT"),
    ("estado", "TEXT"),
    ("fecha_update", "TEXT"),
    ("contacto", "TEXT"),
    ("notas", "TEXT"),
    ("source_data", "TEXT"),
]

REQUIRED_TABLES: set[str] = {
    "offers",
    "beacons",
    "seen_job_ids",
    "keyword_registry",
    "offer_keywords",
    "extraction_cache",
    "match_results",
    "judge_results",
}


class _Clock:
    """Injectable deterministic clock returning a fixed YYYY-MM-DD string."""

    def __init__(self, today: str) -> None:
        self.today = today

    def __call__(self) -> str:
        return self.today


@pytest.fixture()
def clock() -> _Clock:
    return _Clock("2026-08-30")


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "app.db")


@pytest.fixture()
def repo(conn: sqlite3.Connection, clock: _Clock) -> OfferRepository:
    return OfferRepository(conn, clock=clock)


def _offer(**overrides: object) -> OfferInput:
    fields: dict[str, object] = {
        "job_id": "4123",
        "title": "AI Engineer",
        "company": "Acme",
        "url": "https://www.linkedin.com/jobs/view/4123",
        "text": "full offer text",
        "source": "link",
    }
    fields.update(overrides)
    return OfferInput(**fields)


def _offers_ddl(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'offers'"
    ).fetchone()
    assert row is not None
    return " ".join(str(row[0]).split())


def test_init_applies_schema_with_wal_and_user_version(
    conn: sqlite3.Connection, clock: _Clock
) -> None:
    OfferRepository(conn, clock=clock)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    # user_version tracks the ordered migrations (v6: offers.perfil).
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 22
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert REQUIRED_TABLES <= tables


def test_init_is_idempotent_on_second_open(
    conn: sqlite3.Connection, clock: _Clock
) -> None:
    OfferRepository(conn, clock=clock)
    OfferRepository(conn, clock=clock)  # migrations already applied: no re-run
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 22


@pytest.mark.parametrize(("column", "sql_type"), EXPECTED_TYPES)
def test_offers_s1_field_types_via_pragma_table_info(
    repo: OfferRepository, conn: sqlite3.Connection, column: str, sql_type: str
) -> None:
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(offers)")}
    assert columns[column] == sql_type


def test_offers_columns_in_s1_order(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(offers)")]
    # v4 appends the two retry columns AFTER the §1 fields (additive ALTERs).
    assert columns[: len(S1_COLUMNS)] == S1_COLUMNS
    assert columns[len(S1_COLUMNS) :] == [
        "analyze_attempts",
        "next_analyze_at",
        "perfil",
        "salario_investigado",
        "carta_presentacion",
        "fecha_alta",
        "fecha_publicacion",
        "fecha_limite",
        "dedup_key",
        "ingest_source",
        "ubicacion",
    ]


def test_estado_check_declares_13_values_in_order(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    ddl = _offers_ddl(conn)
    marker = "CHECK(estado IN ("
    start = ddl.find(marker)
    assert start >= 0, "estado CHECK missing from DDL"
    region = ddl[start + len(marker) : ddl.find("))", start)]
    position = 0
    for value in ESTADO_VALUES:
        found = region.find(f"'{value}'", position)
        assert found >= position, f"estado value out of order or missing: {value}"
        position = found + 1


def test_estado_defaults_to_pendiente_analisis(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    conn.execute("INSERT INTO offers (source_data) VALUES ('t')")
    conn.commit()
    estado = conn.execute("SELECT estado FROM offers").fetchone()[0]
    assert estado == "pendiente_analisis"


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("origen", "Wall Apop"),
        ("origen", ""),
        ("origen", "x" * 41),
        ("cv_base", "XX_ES"),
        ("match", 101),
        ("match", -1),
        ("estado", "cancelada"),
    ],
)
def test_out_of_domain_value_rejected_by_check(
    repo: OfferRepository,
    conn: sqlite3.Connection,
    column: str,
    value: object,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO offers (source_data, {column}) VALUES ('t', ?)",
            (value,),
        )
        conn.commit()


def test_ai_ca_is_a_valid_cv_base_value(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    """v3 part B: the Catalan AI variant joins the cv_base CHECK domain."""
    conn.execute("INSERT INTO offers (source_data, cv_base) VALUES ('t', 'AI_CA')")
    conn.commit()
    row = conn.execute("SELECT cv_base FROM offers").fetchone()
    assert row is not None and row[0] == "AI_CA"


def test_v2_database_migrates_to_v3_cv_base_check_preserving_rows(
    tmp_path: Path,
) -> None:
    """A pre-v3 database (offers CHECK without AI_CA) rebuilds with data."""
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    from cva.tracker.repo import _MIGRATION_V2, _SCHEMA_SQL

    legacy.executescript(_SCHEMA_SQL)
    legacy.executescript(_MIGRATION_V2)
    legacy.execute(
        "INSERT INTO offers (source_data, cv_base, estado) "
        "VALUES ('legacy text', 'AI_ES', 'analizada')"
    )
    legacy.execute("PRAGMA user_version = 2")
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 22
    row = repo.get_offer(1)
    assert row.cv_base == "AI_ES" and row.estado == "analizada"
    # The rebuilt CHECK accepts the new variant and still rejects junk.
    repo.record_cv(1, cv_base="AI_CA", cv_path="adaptadas/x.pdf", cambios="c")
    assert repo.get_offer(1).cv_base == "AI_CA"
    with pytest.raises(sqlite3.IntegrityError):
        repo._conn.execute(
            "INSERT INTO offers (source_data, cv_base) VALUES ('t', 'XX_ES')"
        )
        repo._conn.commit()


def test_upsert_offer_creates_pendiente_analisis_row_with_source_data(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    row = repo.get_offer(offer_id)
    assert row.estado == "pendiente_analisis"
    assert row.source_data == "full offer text"
    assert row.empresa == "Acme"
    assert row.posicion == "AI Engineer"
    assert row.url_oferta == "https://www.linkedin.com/jobs/view/4123"
    assert row.origen == "linkedin"
    assert row.fecha_update == "2026-08-30"


def test_upsert_offer_same_url_returns_same_row(
    repo: OfferRepository,
) -> None:
    first = repo.upsert_offer(_offer())
    second = repo.upsert_offer(_offer(text="updated offer text"))
    assert second == first
    row = repo.get_offer(first)
    assert row.source_data == "updated offer text"
    assert row.estado == "pendiente_analisis"


def test_upsert_offer_paste_source_maps_to_otro_origen(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer(job_id=None, url=None, source="paste"))
    assert repo.get_offer(offer_id).origen == "otro"


def test_upsert_offer_bridge_inline_maps_to_infojobs_origen(
    repo: OfferRepository,
) -> None:
    """D3: the exact inline marker "bridge-inline" maps to origen='infojobs'
    (the schema CHECK already allows it — no migration)."""
    infojobs_url = (
        "https://www.infojobs.net/barcelona/ingeniero-ia/of-"
        "0f8d7e6c5b4a39281706f5e4d3c2b1a0/"
    )
    offer_id = repo.upsert_offer(
        _offer(
            job_id="0f8d7e6c5b4a39281706f5e4d3c2b1a0",
            url=infojobs_url,
            text="Oferta completa inline.",
            source="bridge-inline",
        )
    )
    row = repo.get_offer(offer_id)
    assert row.origen == "infojobs"
    assert row.source_data == "Oferta completa inline."  # real text stored (D4)


def test_upsert_offer_origen_comes_from_the_url_host(
    repo: OfferRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fake_portal import FAKE_ID, FAKE_URL, register_fake_portal

    register_fake_portal(monkeypatch)
    inline = repo.upsert_offer(
        _offer(job_id=FAKE_ID, url=FAKE_URL, text="Oferta", source="bridge-inline")
    )
    assert repo.get_offer(inline).origen == "otroportal"
    linked = repo.upsert_offer(
        _offer(job_id=FAKE_ID, url=FAKE_URL + "x/", text="Oferta", source="link")
    )
    assert repo.get_offer(linked).origen == "otroportal"
    # No registered host: a link keeps the legacy linkedin default, inline is otro.
    unknown = repo.upsert_offer(
        _offer(job_id="1", url="https://example.com/o/1", text="t", source="link")
    )
    assert repo.get_offer(unknown).origen == "linkedin"
    other = repo.upsert_offer(
        _offer(
            job_id="2", url="https://example.com/o/2", text="t", source="bridge-inline"
        )
    )
    assert repo.get_offer(other).origen == "otro"


def test_origen_check_is_loose_but_shaped(conn: sqlite3.Connection) -> None:
    OfferRepository(conn)
    for good in ("tecnoempleo", "web_empresa", "job-fluent", "otro"):
        conn.execute(
            "INSERT INTO offers (source_data, origen) VALUES ('t', ?)", (good,)
        )
    conn.execute("INSERT INTO offers (source_data) VALUES ('t')")
    conn.commit()


def _downgrade_to_v12(conn: sqlite3.Connection) -> None:
    """Rebuild ``offers`` with the v12 origen CHECK and rewind user_version."""
    from cva.tracker.repo import _ORIGEN_CHECK_V13

    (ddl,) = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='offers'"
    ).fetchone()
    assert _ORIGEN_CHECK_V13 in ddl
    old = ddl.replace(
        _ORIGEN_CHECK_V13,
        "CHECK(origen IN ('linkedin','infojobs','referido','web_empresa','otro'))",
    ).replace('CREATE TABLE "offers"', "CREATE TABLE offers_old")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(old)
    conn.executescript(
        "INSERT INTO offers_old SELECT * FROM offers;"
        "DROP TABLE offers; ALTER TABLE offers_old RENAME TO offers;"
        "CREATE INDEX idx_offers_dedup_key ON offers (dedup_key);"
        "PRAGMA user_version = 12;"
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()


def _offers_indexes_and_triggers(
    conn: sqlite3.Connection,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """Every index (name, unique, origin, partial, DDL) and trigger on offers."""
    indexes = sorted(
        (r[1], r[2], r[3], r[4])
        + tuple(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (r[1],)
            ).fetchone()
        )
        for r in conn.execute("PRAGMA index_list(offers)")
        if not str(r[1]).startswith("sqlite_autoindex")
    )
    triggers = sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='offers'"
        )
    )
    return indexes, triggers


def test_v12_database_migrates_to_v13_loose_origen_check(tmp_path: Path) -> None:
    path = tmp_path / "v12.db"
    first = sqlite3.connect(path)
    repo = OfferRepository(first)
    ids = [
        repo.upsert_offer(
            _offer(
                job_id=str(n), url=f"https://linkedin.com/jobs/view/{n}", text=f"t{n}"
            )
        )
        for n in (1, 2, 3)
    ]
    first.execute(
        "INSERT INTO match_results (offer_id, score, threshold, breakdown_json) "
        "VALUES (?, 80, 65, '{}')",
        (ids[1],),
    )
    first.execute("INSERT INTO offer_keywords VALUES (?, 'ai', 'python')", (ids[2],))
    first.commit()
    _downgrade_to_v12(first)
    # A real v12 database has no v14 column: it is re-added (NULL) after v13.
    first.execute("ALTER TABLE offers DROP COLUMN ingest_source")
    first.execute("ALTER TABLE offers DROP COLUMN ubicacion")
    first.commit()
    before = [tuple(r) for r in first.execute("SELECT * FROM offers ORDER BY id")]
    columns = [r[1] for r in first.execute("PRAGMA table_info(offers)")]
    pre_schema = _offers_indexes_and_triggers(first)
    assert pre_schema[0], "fixture must define at least the dedup_key index"
    with pytest.raises(sqlite3.IntegrityError):
        first.execute(
            "INSERT INTO offers (source_data, origen) VALUES ('t','tecnoempleo')"
        )
    first.rollback()
    first.close()

    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 22
    assert [r[1] for r in migrated.execute("PRAGMA table_info(offers)")] == [
        *columns,
        "ingest_source",
        "ubicacion",
    ]
    assert [
        tuple(r)[:-2] for r in migrated.execute("SELECT * FROM offers ORDER BY id")
    ] == before
    assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [r[0] for r in migrated.execute("SELECT offer_id FROM match_results")] == [
        ids[1]
    ]
    assert [r[0] for r in migrated.execute("SELECT keyword FROM offer_keywords")] == [
        "python"
    ]
    index_names = {r[1] for r in migrated.execute("PRAGMA index_list(offers)")}
    assert "idx_offers_dedup_key" in index_names
    assert _offers_indexes_and_triggers(migrated) == pre_schema
    migrated.execute(
        "INSERT INTO offers (source_data, origen) VALUES ('t','tecnoempleo')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute(
            "INSERT INTO offers (source_data, origen) VALUES ('t','Bad Value')"
        )
    migrated.rollback()


def test_migration_v13_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "v12.db"
    first = sqlite3.connect(path)
    repo = OfferRepository(first)
    repo.upsert_offer(_offer(job_id="1", url="https://linkedin.com/jobs/view/1"))
    _downgrade_to_v12(first)
    first.close()
    broken = repo_mod._MIGRATION_V13.replace(
        "PRAGMA user_version = 13;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 13;",
    )
    assert broken != repo_mod._MIGRATION_V13
    monkeypatch.setattr(repo_mod, "_MIGRATION_V13", broken)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert conn.execute("SELECT count(*) FROM offers").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'offers_v13'"
        ).fetchone()[0]
        == 0
    )


def test_get_offer_unknown_id_fails_fast(repo: OfferRepository) -> None:
    with pytest.raises(RepositoryError, match="offer 999"):
        repo.get_offer(999)


def test_find_offer_by_url_hit_returns_row(
    repo: OfferRepository,
) -> None:
    """Bridge dedupe seam (D3/D4): exact url_oferta match, full row back."""
    repo.upsert_offer(_offer())
    hit = repo.find_offer_by_url("https://www.linkedin.com/jobs/view/4123")
    assert hit is not None
    assert hit.url_oferta == "https://www.linkedin.com/jobs/view/4123"
    assert hit.empresa == "Acme"


def test_find_offer_by_url_miss_returns_none(
    repo: OfferRepository,
) -> None:
    repo.upsert_offer(_offer())
    assert repo.find_offer_by_url("https://www.linkedin.com/jobs/view/999") is None
    assert repo.find_offer_by_url("https://www.linkedin.com/jobs/view/") is None


def test_set_estado_transitions_to_valid_value(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    repo.set_estado(offer_id, "analizada")
    assert repo.get_offer(offer_id).estado == "analizada"


def test_set_estado_invalid_value_rejected_by_check(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    with pytest.raises(sqlite3.IntegrityError):
        repo.set_estado(offer_id, "cancelada")


def test_mark_sent_sets_enviada_with_cv_fields(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    repo.set_estado(offer_id, "preparada")
    repo.mark_sent(
        offer_id,
        cv_path="adaptadas/Alex_Rivera_AI_Engineer_Acme.pdf",
        cambios="highlight RAG project",
        match=78,
    )
    row = repo.get_offer(offer_id)
    assert row.estado == "enviada"
    assert row.fecha_envio == "2026-08-30"
    assert row.cv_adaptado == "adaptadas/Alex_Rivera_AI_Engineer_Acme.pdf"
    assert row.cambios == "highlight RAG project"
    assert row.match == 78


def test_bump_keyword_inserts_then_increments_with_last_seen(
    repo: OfferRepository, conn: sqlite3.Connection, clock: _Clock
) -> None:
    repo.bump_keyword("AI Engineer", "RAG")
    repo.bump_keyword("AI Engineer", "RAG")
    clock.today = "2026-08-31"
    repo.bump_keyword("AI Engineer", "RAG")
    row = conn.execute(
        "SELECT frequency, last_seen FROM keyword_registry "
        "WHERE profile = ? AND keyword = ?",
        ("AI Engineer", "RAG"),
    ).fetchone()
    assert row is not None
    assert row[0] == 3
    assert row[1] == "2026-08-31"


def test_bump_keyword_keys_are_independent_per_profile(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    repo.bump_keyword("AI Engineer", "RAG")
    repo.bump_keyword("Data Scientist", "RAG")
    count = conn.execute(
        "SELECT COUNT(*) FROM keyword_registry WHERE keyword = 'RAG'"
    ).fetchone()[0]
    assert count == 2


def _seed_beacon(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        "INSERT INTO beacons (name, url) VALUES ('ai-engineer', 'https://example.com/jobs')"
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_diff_seen_returns_only_new_ids_and_marks_them(
    repo: OfferRepository, conn: sqlite3.Connection, clock: _Clock
) -> None:
    beacon_id = _seed_beacon(conn)
    assert repo.diff_seen(beacon_id, ["a", "b", "c"]) == ["a", "b", "c"]
    clock.today = "2026-08-31"
    assert repo.diff_seen(beacon_id, ["b", "d"]) == ["d"]
    assert repo.diff_seen(beacon_id, ["b", "d"]) == []
    rows = conn.execute(
        "SELECT job_id, first_seen_at FROM seen_job_ids "
        "WHERE beacon_id = ? ORDER BY job_id",
        (beacon_id,),
    ).fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        ("a", "2026-08-30"),
        ("b", "2026-08-30"),
        ("c", "2026-08-30"),
        ("d", "2026-08-31"),
    ]


def test_diff_seen_ignores_duplicate_ids_in_one_batch(
    repo: OfferRepository, conn: sqlite3.Connection
) -> None:
    beacon_id = _seed_beacon(conn)
    assert repo.diff_seen(beacon_id, ["a", "a", "b"]) == ["a", "b"]
    stored = conn.execute(
        "SELECT COUNT(*) FROM seen_job_ids WHERE beacon_id = ?", (beacon_id,)
    ).fetchone()[0]
    assert stored == 2


# --- v4 migration + analyze-retry columns (notification-pacing design D3) ----


def test_v4_migration_adds_retry_columns_to_v3_database(tmp_path: Path) -> None:
    """A v3 database gains the two additive retry columns; rows preserved."""
    path = tmp_path / "v3.db"
    legacy = sqlite3.connect(path)
    from cva.tracker.repo import _MIGRATION_V2, _MIGRATION_V3, _SCHEMA_SQL

    legacy.executescript(_SCHEMA_SQL)
    legacy.executescript(_MIGRATION_V2)
    legacy.execute("PRAGMA foreign_keys=OFF")
    legacy.executescript(_MIGRATION_V3)
    legacy.execute("PRAGMA foreign_keys=ON")
    legacy.execute("PRAGMA user_version = 3")
    legacy.execute(
        "INSERT INTO offers (source_data, estado) VALUES ('legacy text', 'analizada')"
    )
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 22
    columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(offers)")}
    assert {"analyze_attempts", "next_analyze_at"} <= columns
    row = repo.get_offer(1)
    assert row.estado == "analizada"
    assert row.analyze_attempts == 0  # NOT NULL DEFAULT 0 backfills
    assert row.next_analyze_at is None


def test_schema_sql_untouched_by_v4_migration(tmp_path: Path) -> None:
    """Migrations own post-v1 columns (v2 convention): schema.sql stays v1."""
    from cva.tracker.repo import _SCHEMA_SQL

    assert "analyze_attempts" not in _SCHEMA_SQL
    assert "next_analyze_at" not in _SCHEMA_SQL


def test_fresh_database_runs_full_chain_to_v4(
    conn: sqlite3.Connection, clock: _Clock
) -> None:
    OfferRepository(conn, clock=clock)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 22
    columns = {row[1] for row in conn.execute("PRAGMA table_info(offers)")}
    assert {"analyze_attempts", "next_analyze_at"} <= columns


# --- v5 migration + judge_results (llm-offer-judge design) ------------------


def test_v5_migration_adds_judge_results_table_to_v4_database(
    tmp_path: Path,
) -> None:
    """A v4 database gains the judge_results table; existing rows preserved."""
    path = tmp_path / "v4.db"
    legacy = sqlite3.connect(path)
    from cva.tracker.repo import (
        _MIGRATION_V2,
        _MIGRATION_V3,
        _MIGRATION_V4,
        _SCHEMA_SQL,
    )

    legacy.executescript(_SCHEMA_SQL)
    legacy.executescript(_MIGRATION_V2)
    legacy.execute("PRAGMA foreign_keys=OFF")
    legacy.executescript(_MIGRATION_V3)
    legacy.execute("PRAGMA foreign_keys=ON")
    legacy.executescript(_MIGRATION_V4)
    legacy.execute("PRAGMA user_version = 4")
    legacy.execute(
        "INSERT INTO offers (source_data, estado) VALUES ('legacy text', 'analizada')"
    )
    legacy.commit()
    legacy.close()

    repo = OfferRepository(sqlite3.connect(path))
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 22
    tables = {
        row[0]
        for row in repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert "judge_results" in tables
    assert repo.get_offer(1).estado == "analizada"  # existing row preserved


def test_save_and_read_judge_result(repo: OfferRepository) -> None:
    offer_id = repo.upsert_offer(_offer())
    assert repo.latest_judge_result(offer_id) is None

    repo.save_judge_result(
        offer_id, score=76, verdict="apply", judgment_json='{"score": 76}'
    )

    row = repo.latest_judge_result(offer_id)
    assert row is not None
    assert row.offer_id == offer_id
    assert row.score == 76
    assert row.verdict == "apply"
    assert row.judgment_json == '{"score": 76}'
    assert row.created_at == "2026-08-30"


def test_save_judge_result_replaces_previous_row(repo: OfferRepository) -> None:
    offer_id = repo.upsert_offer(_offer())
    repo.save_judge_result(
        offer_id, score=30, verdict="discard", judgment_json='{"score": 30}'
    )
    repo.save_judge_result(
        offer_id, score=80, verdict="apply", judgment_json='{"score": 80}'
    )

    row = repo.latest_judge_result(offer_id)
    assert row is not None
    assert row.score == 80  # one row per offer — latest wins
    assert row.verdict == "apply"


def test_offer_row_defaults_zero_attempts_null_next(repo: OfferRepository) -> None:
    offer_id = repo.upsert_offer(_offer())
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 0
    assert row.next_analyze_at is None


def test_record_analyze_failure_increments_attempts_and_sets_next(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    attempts = repo.record_analyze_failure(
        offer_id, next_analyze_at="2026-09-19T12:10:00+00:00"
    )
    assert attempts == 1
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 1
    assert row.next_analyze_at == "2026-09-19T12:10:00+00:00"

    attempts = repo.record_analyze_failure(
        offer_id, next_analyze_at="2026-09-19T12:20:00+00:00"
    )
    assert attempts == 2
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 2
    assert row.next_analyze_at == "2026-09-19T12:20:00+00:00"


def test_clear_analyze_retry_resets_attempts_and_next(
    repo: OfferRepository,
) -> None:
    offer_id = repo.upsert_offer(_offer())
    repo.record_analyze_failure(offer_id, next_analyze_at="2026-09-19T12:10:00+00:00")
    repo.clear_analyze_retry(offer_id)
    row = repo.get_offer(offer_id)
    assert row.analyze_attempts == 0
    assert row.next_analyze_at is None


def test_list_retry_pending_returns_due_rows_within_attempts(
    repo: OfferRepository,
) -> None:
    due_id = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/4123"))
    future_id = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/4124"))
    exhausted_id = repo.upsert_offer(
        _offer(url="https://www.linkedin.com/jobs/view/4125")
    )
    sent_id = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/4126"))
    fresh_id = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/4127"))

    repo.record_analyze_failure(due_id, next_analyze_at="2026-09-19T11:50:00+00:00")
    repo.record_analyze_failure(future_id, next_analyze_at="2026-09-19T12:30:00+00:00")
    for _ in range(3):
        repo.record_analyze_failure(
            exhausted_id, next_analyze_at="2026-09-19T11:50:00+00:00"
        )
    repo.record_analyze_failure(sent_id, next_analyze_at="2026-09-19T11:50:00+00:00")
    repo.set_estado(sent_id, "preparada")
    repo.mark_sent(
        sent_id, cv_path="adaptadas/x.pdf", cambios="c", match=70
    )  # sent-ish: never swept again

    pending = repo.list_retry_pending("2026-09-19T12:00:00+00:00", 2)

    assert [row.id for row in pending] == [due_id]
    assert pending[0].analyze_attempts == 1
    assert pending[0].next_analyze_at == "2026-09-19T11:50:00+00:00"
    # fresh row (0 attempts, NULL next) is never listed
    assert fresh_id not in [row.id for row in pending]


def test_list_retry_pending_boundary_now_equals_next_is_due(
    repo: OfferRepository,
) -> None:
    """Due boundary: next_analyze_at == now IS due (>= comparison semantics)."""
    offer_id = repo.upsert_offer(_offer())
    repo.record_analyze_failure(offer_id, next_analyze_at="2026-09-19T12:00:00+00:00")
    pending = repo.list_retry_pending("2026-09-19T12:00:00+00:00", 5)
    assert [row.id for row in pending] == [offer_id]
    assert repo.list_retry_pending("2026-09-19T11:59:00+00:00", 5) == []


def test_defer_analyze_retry_sets_next_without_touching_attempts(
    repo: OfferRepository,
) -> None:
    """Shutdown deferral: next=now marks the row sweepable; attempts unchanged."""
    offer_id = repo.upsert_offer(_offer())
    repo.record_analyze_failure(offer_id, next_analyze_at="2026-09-19T12:10:00+00:00")
    repo.defer_analyze_retry(offer_id, next_analyze_at="2026-09-19T13:00:00+00:00")
    row = repo.get_offer(offer_id)
    assert row.next_analyze_at == "2026-09-19T13:00:00+00:00"
    assert row.analyze_attempts == 1  # untouched: shutdown is not a failure


def test_list_retry_pending_empty_when_nothing_pending(
    repo: OfferRepository,
) -> None:
    repo.upsert_offer(_offer())  # pending nothing: 0 attempts, NULL next
    assert repo.list_retry_pending("2026-09-19T12:00:00+00:00", 5) == []


def test_all_offers_returns_every_row_oldest_first(repo: OfferRepository) -> None:
    first = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/1"))
    second = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/2"))
    third = repo.upsert_offer(_offer(url="https://www.linkedin.com/jobs/view/3"))
    repo.set_estado(second, "descartada")

    rows = repo.all_offers()

    assert [row.id for row in rows] == [first, second, third]
    assert rows[1].estado == "descartada"


def test_all_offers_empty_database_returns_empty_list(repo: OfferRepository) -> None:
    assert repo.all_offers() == []


def test_ingest_source_is_stored_on_insert(conn: sqlite3.Connection) -> None:
    repo = OfferRepository(conn)
    link = repo.upsert_offer(
        _offer(job_id="1", url="https://linkedin.com/jobs/view/1", text="t")
    )
    inline = repo.upsert_offer(
        OfferInput(
            job_id="2", url="https://example.com/o/2", text="t", source="bridge-inline"
        )
    )
    assert repo.get_offer(link).ingest_source == "link"
    assert repo.get_offer(inline).ingest_source == "bridge-inline"


def test_v13_database_migrates_to_v14_with_null_ingest_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v13.db"
    first = sqlite3.connect(path)
    repo = OfferRepository(first)
    offer_id = repo.upsert_offer(
        _offer(job_id="1", url="https://linkedin.com/jobs/view/1", text="t")
    )
    first.execute("ALTER TABLE offers DROP COLUMN ingest_source")
    first.execute("PRAGMA user_version = 13")
    first.commit()
    first.close()

    migrated = sqlite3.connect(path)
    migrated_repo = OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 22
    assert migrated_repo.get_offer(offer_id).ingest_source is None


def test_v13_database_with_column_already_present_migrates_to_v14(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v13-col.db"
    first = sqlite3.connect(path)
    repo = OfferRepository(first)
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="2", url="https://example.com/o/2", text="t", source="bridge-inline"
        )
    )
    # Interrupted earlier run: the column exists but the version is still 13.
    first.execute("PRAGMA user_version = 13")
    first.commit()
    first.close()

    migrated = sqlite3.connect(path)
    migrated_repo = OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 22
    assert migrated_repo.get_offer(offer_id).ingest_source == "bridge-inline"


def test_link_reingest_keeps_the_inline_ingest_source(conn: sqlite3.Connection) -> None:
    repo = OfferRepository(conn)
    url = "https://www.infojobs.net/oferta-x/of-i123"
    offer_id = repo.upsert_offer(
        OfferInput(job_id="i123", url=url, text="inline text", source="bridge-inline")
    )
    repo.upsert_offer(OfferInput(job_id="i123", url=url, text="t2", source="link"))
    assert repo.get_offer(offer_id).ingest_source == "bridge-inline"


def _prepared_offer(repo: OfferRepository) -> int:
    offer_id = repo.upsert_offer(_offer(text="the posting as analysed"))
    repo.set_cover_letter(offer_id, "Carta elegida")
    repo.record_cv(
        offer_id,
        cv_base="AI_ES",
        cv_path="adaptadas/Alex_Rivera_AI_Acme.pdf",
        cambios="c",
    )
    return offer_id


def test_mark_sent_stores_a_sent_snapshot(repo: OfferRepository) -> None:
    offer_id = _prepared_offer(repo)
    first = repo.mark_sent(
        offer_id, cv_path="adaptadas/Alex_Rivera_AI_Acme.pdf", cambios="c", match=70
    )
    snap = repo.get_sent_snapshot(offer_id)
    assert first is True
    assert snap is not None
    assert snap.sent_at == "2026-08-30"
    assert snap.posting_text == "the posting as analysed"
    assert snap.cover_letter == "Carta elegida"
    assert snap.cv_file == "Alex_Rivera_AI_Acme.pdf"
    assert snap.cv_variant == "AI_ES"
    assert snap.url == "https://www.linkedin.com/jobs/view/4123"


def test_resend_keeps_the_first_snapshot(repo: OfferRepository, clock: _Clock) -> None:
    offer_id = _prepared_offer(repo)
    repo.mark_sent(offer_id, cv_path="a/one.pdf", cambios="c", match=70)
    repo.set_cover_letter(offer_id, "Otra carta")
    clock.today = "2026-09-05"
    again = repo.mark_sent(offer_id, cv_path="a/two.pdf", cambios="c", match=70)
    snap = repo.get_sent_snapshot(offer_id)
    assert again is False
    assert snap is not None
    assert (snap.cv_file, snap.cover_letter, snap.sent_at) == (
        "one.pdf",
        "Carta elegida",
        "2026-08-30",
    )


def test_offer_without_send_has_no_snapshot(repo: OfferRepository) -> None:
    offer_id = _prepared_offer(repo)
    assert repo.get_sent_snapshot(offer_id) is None


def test_v14_database_migrates_to_v15_with_sent_snapshots(tmp_path: Path) -> None:
    path = tmp_path / "v14.db"
    first = sqlite3.connect(path)
    OfferRepository(first)
    first.execute("DROP TABLE sent_snapshots")
    first.execute("PRAGMA user_version = 14")
    first.commit()
    first.close()

    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] >= 15
    cols = [r[1] for r in migrated.execute("PRAGMA table_info(sent_snapshots)")]
    assert cols == [
        "offer_id",
        "sent_at",
        "posting_text",
        "cover_letter",
        "cv_file",
        "cv_variant",
        "url",
    ]


def test_v15_migration_is_idempotent_when_table_already_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v14-table.db"
    first = sqlite3.connect(path)
    OfferRepository(first)
    first.execute("PRAGMA user_version = 14")  # interrupted: table exists
    first.commit()
    first.close()

    migrated = sqlite3.connect(path)
    OfferRepository(migrated)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] >= 15
