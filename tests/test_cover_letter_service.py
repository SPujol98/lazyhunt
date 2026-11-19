"""Cover letters: service generation/choice, migration v9, synced xlsx (T2). No network."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook
from pydantic import BaseModel

from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.llm import LlmResponseError
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService, ServiceError
from cva.tracker import OfferRepository
from cva.tracker.repo import SCHEMA_VERSION, OfferInput
from cva.tracker.xlsx import SYNCED_COLUMNS
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS
from tests.test_cover_letter_v2 import ES_BASE, ES_PARTS, _letter

CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
ES_LETTER = _letter(ES_PARTS, ES_BASE, "es")


class _Llm:
    model = "fake"

    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.fail = fail
        self.delay = delay
        self.calls = 0
        self.last_system = ""
        self.last_user = ""

    async def json_completion(
        self, system: str, user: str, schema: type[BaseModel], *, model=None
    ):
        self.calls += 1
        self.last_system, self.last_user = system, user
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise LlmResponseError("boom")
        return schema.model_validate(ES_PARTS)


class _NoCompletionLlm:
    model = "fake"


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:
        raise AssertionError("no fetch")


def _service(
    tmp_path: Path,
    llm: object,
    xlsx: Path | None = None,
    *,
    review: bool = False,
):
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    service = JobService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(SYNONYMS_PATH, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        now_fn=lambda: NOW,
        tracker_xlsx=xlsx,
        cover_letter_review=review,
    )
    oid = repo.upsert_offer(
        OfferInput(
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="Buscamos AI Engineer en Valencia. Adjunta una carta de presentación. Python y RAG.",
            source="link",
            profile="ai",
        )
    )
    return service, repo, oid


# --- migration v9 -------------------------------------------------------------------


def _old_db(path: Path, version: int = 8) -> None:
    repo = OfferRepository(sqlite3.connect(path))
    repo.upsert_offer(OfferInput(title="t", url="https://x/1", text="x", source="link"))
    repo._conn.execute("ALTER TABLE offers DROP COLUMN carta_presentacion")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_alta")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_publicacion")
    repo._conn.execute("ALTER TABLE offers DROP COLUMN fecha_limite")
    repo._conn.execute("DROP TABLE app_state")
    repo._conn.execute(f"PRAGMA user_version = {version}")
    repo._conn.commit()
    repo._conn.close()


def test_fresh_db_has_nullable_column_at_v9(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "new.db"))
    assert SCHEMA_VERSION == 24
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    oid = repo.upsert_offer(
        OfferInput(title="t", url="https://x/1", text="x", source="link")
    )
    assert repo.get_offer(oid).carta_presentacion is None


def test_migration_v9_adds_column_to_old_db(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    _old_db(path)
    repo = OfferRepository(sqlite3.connect(path))
    assert repo._conn.execute("PRAGMA user_version").fetchone()[0] == 24
    assert repo.get_offer(1).carta_presentacion is None
    repo.set_cover_letter(1, "hola")
    assert repo.get_offer(1).carta_presentacion == "hola"
    assert (
        OfferRepository(sqlite3.connect(path)).get_offer(1).carta_presentacion == "hola"
    )


def test_migration_v9_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.tracker.repo as repo_mod

    path = tmp_path / "old.db"
    _old_db(path)
    broken = repo_mod._MIGRATION_V9.replace(
        "PRAGMA user_version = 9;",
        "SELECT * FROM no_such_table;\nPRAGMA user_version = 9;",
    )
    assert broken != repo_mod._MIGRATION_V9
    monkeypatch.setattr(repo_mod, "_MIGRATION_V9", broken)
    with pytest.raises(sqlite3.OperationalError):
        OfferRepository(sqlite3.connect(path))
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    assert "carta_presentacion" not in [
        r[1] for r in conn.execute("PRAGMA table_info(offers)")
    ]


def test_column_is_synced() -> None:
    assert SYNCED_COLUMNS[-1] == "carta_presentacion"


# --- service ------------------------------------------------------------------------


def test_cover_letters_returns_the_assembled_letter_built_from_the_offer(
    tmp_path: Path,
) -> None:
    llm = _Llm()
    service, repo, oid = _service(tmp_path, llm)
    result = asyncio.run(service.cover_letters(oid))
    assert result.text == ES_LETTER and result.warnings == []
    # An offer without company facts (no web search) still gets a letter.
    assert result.thin_company is True and result.facts == []
    assert "Valencia" in llm.last_user and "Acme" in llm.last_user
    assert "cover_letter" in llm.last_user  # apply finding reaches the prompt
    assert "Ya tenéis mi CV" in llm.last_user  # voice sample loaded from data/
    assert "Language of the letter: Spanish" in llm.last_user
    assert ES_BASE.paragraph1 in llm.last_user
    assert repo.get_offer(oid).carta_presentacion is None  # nothing stored until chosen


def test_each_press_regenerates(tmp_path: Path) -> None:
    llm = _Llm()
    service, _repo, oid = _service(tmp_path, llm)
    asyncio.run(service.cover_letters(oid))
    asyncio.run(service.cover_letters(oid))
    assert llm.calls == 2


def test_concurrent_presses_are_serialised(tmp_path: Path) -> None:
    llm = _Llm(delay=0.05)
    service, _repo, oid = _service(tmp_path, llm)
    active = 0
    peak = 0
    original = llm.json_completion

    async def tracked(*a, **k):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            return await original(*a, **k)
        finally:
            active -= 1

    llm.json_completion = tracked  # type: ignore[method-assign]

    async def both():
        return await asyncio.gather(
            service.cover_letters(oid), service.cover_letters(oid)
        )

    asyncio.run(both())
    assert peak == 1 and llm.calls == 2


def test_llm_failure_propagates_and_stores_nothing(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm(fail=True))
    with pytest.raises(LlmResponseError):
        asyncio.run(service.cover_letters(oid))
    assert repo.get_offer(oid).carta_presentacion is None
    with pytest.raises(ServiceError):
        service.choose_cover_letter(oid, "a")


def test_provider_without_completion_is_a_service_error(tmp_path: Path) -> None:
    service, _repo, oid = _service(tmp_path, _NoCompletionLlm())
    with pytest.raises(ServiceError):
        asyncio.run(service.cover_letters(oid))


def test_choose_persists_the_letter_and_refreshes_tracker(tmp_path: Path) -> None:
    target = tmp_path / "tracker.xlsx"
    service, repo, oid = _service(tmp_path, _Llm(), xlsx=target)
    asyncio.run(service.cover_letters(oid))
    assert service.choose_cover_letter(oid, "a") == ES_LETTER
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER
    for sheet_name in ("Enviadas", "Todas"):
        header = [c.value for c in load_workbook(target)[sheet_name][1]]
        assert "carta_presentacion" in header
    sheet = load_workbook(target)["Todas"]
    header = [c.value for c in sheet[1]]
    assert (
        sheet.cell(row=2, column=header.index("carta_presentacion") + 1).value
        == ES_LETTER
    )


def test_choose_rejects_unknown_version(tmp_path: Path) -> None:
    service, _repo, oid = _service(tmp_path, _Llm())
    asyncio.run(service.cover_letters(oid))
    with pytest.raises(ServiceError):
        service.choose_cover_letter(oid, "b")


def test_choose_from_an_older_generation_is_rejected(tmp_path: Path) -> None:
    """[✅ Usar esta] on a message from an earlier press must not save the
    text of the newer letter: the button carries its generation."""
    service, repo, oid = _service(tmp_path, _Llm())
    first = asyncio.run(service.cover_letters(oid))
    second = asyncio.run(service.cover_letters(oid))
    assert second.generation == first.generation + 1
    with pytest.raises(ServiceError):
        service.choose_cover_letter(oid, "a", generation=first.generation)
    assert repo.get_offer(oid).carta_presentacion is None
    service.choose_cover_letter(oid, "a", generation=second.generation)
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER


def test_service_runs_the_reviewer_only_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "off").mkdir()
    (tmp_path / "on").mkdir()
    off = _Llm()
    service, _, oid = _service(tmp_path / "off", off)  # type: ignore[arg-type]
    asyncio.run(service.cover_letters(oid))
    assert off.calls == 1

    on = _Llm()
    service, _, oid = _service(tmp_path / "on", on, review=True)  # type: ignore[arg-type]
    result = asyncio.run(service.cover_letters(oid))
    assert on.calls == 2 and "REVIEW TASK" in on.last_user
    assert result.text == ES_LETTER and result.warnings == []
