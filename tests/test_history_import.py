"""``import-history``: idempotent import of the owner's manual offer log.

Real JobService over tmp SQLite (zero network, zero LLM). The import never
analyzes, notifies or generates CVs — it only writes ``offers`` rows.
"""

import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from cva.fetch import FetchResponse
from cva.service import JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.test_auto_triage import (
    JSONLD_BODY,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)

WWW_URL = "https://www.linkedin.com/jobs/view/4459804598/"
BOT_URL = "https://linkedin.com/jobs/view/4459804598"


def _record(log_id: int = 1, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "log_id": log_id,
        "empresa": "Acme",
        "posicion": "AI Engineer",
        "url_oferta": WWW_URL,
        "estado": "enviada",
        "fecha_envio": "2026-09-01",
        "fecha_update": "2026-09-02",
        "match": 80,
        "cv_base": "AI_ES",
        "cv_adaptado": "cv_acme.pdf",
        "sueldo_rango": "40-50k",
        "contacto": "ana@acme.com",
        "notas": "buen ajuste",
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "historial.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


@pytest.fixture()
def svc(tmp_path: Path) -> tuple[JobService, OfferRepository]:
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    return make_service(tmp_path, llm, fetcher)


def _bot_offer(repo: OfferRepository, *, url: str = BOT_URL) -> int:
    return repo.upsert_offer(
        OfferInput(
            job_id="4459804598",
            title="AI Engineer",
            company="Acme",
            url=url,
            text="offer text",
            source="link",
        )
    )


def _count(repo: OfferRepository) -> int:
    return len(repo.all_offers())


def test_insert_new_linkedin_record(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    result = service.import_history(_write(tmp_path, [_record(7)]))
    assert (result.inserted, result.merged, result.skipped) == (1, 0, 0)
    assert result.invalid == []
    (row,) = repo.all_offers()
    assert row.origen == "linkedin"
    assert row.url_oferta == BOT_URL  # normalized like bot rows
    assert row.estado == "enviada"
    assert row.fecha_envio == "2026-09-01"
    assert row.fecha_update == "2026-09-02"
    assert (row.match, row.cv_base, row.cv_adaptado) == (80, "AI_ES", "cv_acme.pdf")
    assert (row.sueldo_rango, row.contacto) == ("40-50k", "ana@acme.com")
    assert row.notas is not None
    assert "buen ajuste" in row.notas and "[log #7]" in row.notas
    assert row.source_data == "Importado de ofertas_log.md #7"


def test_insert_non_linkedin_and_urlless_records(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    records = [
        _record(1, url_oferta="https://acme.com/jobs/1"),
        _record(2, url_oferta=None, fecha_envio=None, fecha_update=None),
    ]
    result = service.import_history(_write(tmp_path, records))
    assert result.inserted == 2
    by_log = {r.notas: r for r in repo.all_offers() if r.notas}
    web = next(r for n, r in by_log.items() if "[log #1]" in n)
    nourl = next(r for n, r in by_log.items() if "[log #2]" in n)
    assert (web.origen, web.url_oferta) == ("otro", "https://acme.com/jobs/1")
    assert nourl.origen == "otro" and nourl.url_oferta is None
    assert nourl.fecha_update  # defaults to today when both dates are null


def test_merge_by_job_id_across_url_variants_fills_only_empty(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    offer_id = _bot_offer(repo)  # stored without www / trailing slash
    repo.mark_sent(offer_id, cv_path="bot.pdf", cambios="x", match=91)
    result = service.import_history(_write(tmp_path, [_record(3)]))
    assert (result.inserted, result.merged, result.skipped) == (0, 1, 0)
    assert _count(repo) == 1
    row = repo.get_offer(offer_id)
    assert row.match == 91 and row.cv_adaptado == "bot.pdf"  # kept
    assert row.fecha_envio != "2026-09-01"  # bot's own send date kept
    assert row.cv_base == "AI_ES"  # was empty -> filled
    assert (row.sueldo_rango, row.contacto) == ("40-50k", "ana@acme.com")
    assert row.notas is not None and "[log #3]" in row.notas
    assert row.estado == "enviada"


@pytest.mark.parametrize("pre", ["pendiente_analisis", "analizada", "preparada"])
def test_merge_advances_presend_estado(
    svc: tuple[JobService, OfferRepository], tmp_path: Path, pre: str
) -> None:
    service, repo = svc
    offer_id = _bot_offer(repo)
    repo.set_estado(offer_id, pre)
    service.import_history(_write(tmp_path, [_record(4, estado="entrevista_tecnica")]))
    assert repo.get_offer(offer_id).estado == "entrevista_tecnica"


@pytest.mark.parametrize("post", ["enviada", "screening", "rechazada", "descartada"])
def test_merge_never_changes_postsend_estado(
    svc: tuple[JobService, OfferRepository], tmp_path: Path, post: str
) -> None:
    service, repo = svc
    offer_id = _bot_offer(repo)
    repo.set_estado(offer_id, post)
    service.import_history(_write(tmp_path, [_record(4, estado="entrevista_final")]))
    assert repo.get_offer(offer_id).estado == post


def test_merge_does_not_advance_to_presend_estado(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    offer_id = _bot_offer(repo)
    repo.set_estado(offer_id, "analizada")
    service.import_history(_write(tmp_path, [_record(4, estado="preparada")]))
    assert repo.get_offer(offer_id).estado == "analizada"


def test_merge_appends_marker_after_existing_notes(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    offer_id = _bot_offer(repo)
    repo._conn.execute("UPDATE offers SET notas = 'mia' WHERE id = ?", (offer_id,))
    service.import_history(_write(tmp_path, [_record(5, notas="otra")]))
    notas = repo.get_offer(offer_id).notas
    assert notas is not None
    assert notas.startswith("mia") and notas.endswith("[log #5]")


def test_rerun_is_idempotent(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    bot_id = _bot_offer(repo)
    records = [
        _record(1),  # merges into the bot row
        _record(2, url_oferta="https://www.linkedin.com/jobs/view/999/"),  # insert
        _record(3, url_oferta=None),  # URL-less insert
    ]
    path = _write(tmp_path, records)
    first = service.import_history(path)
    assert (first.inserted, first.merged) == (2, 1)
    snapshot = [r.model_dump() for r in repo.all_offers()]

    second = service.import_history(path)
    assert (second.inserted, second.merged, second.skipped) == (0, 0, 3)
    assert [r.model_dump() for r in repo.all_offers()] == snapshot
    assert repo.get_offer(bot_id).notas is not None


def test_urlless_dedupe_by_marker(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    path = _write(
        tmp_path, [_record(12, url_oferta=None), _record(123, url_oferta=None)]
    )
    assert service.import_history(path).inserted == 2
    again = service.import_history(path)
    assert (again.inserted, again.skipped) == (0, 2)  # #12 does not match #123
    assert _count(repo) == 2


def test_invalid_records_are_reported_and_skipped(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    records = [
        _record(1),
        _record(2, estado="inventado"),
        _record(3, cv_base="XX_ZZ"),
        _record(4, match=101),
        _record(5, match=-1),
        {"empresa": "SinId", "posicion": "x", "estado": "enviada"},
        _record(6, fecha_envio="01/09/2026"),
        _record(
            8,
            url_oferta="https://www.linkedin.com/jobs/view/8/",
            match=None,
            cv_base=None,
        ),
    ]
    result = service.import_history(_write(tmp_path, records))
    assert (result.inserted, len(result.invalid)) == (2, 6)
    reported = "\n".join(result.invalid)
    for log_id in ("#2", "#3", "#4", "#5", "#6"):
        assert log_id in reported
    assert _count(repo) == 2


def test_non_list_json_raises_service_error(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, _ = svc
    path = tmp_path / "bad.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(ServiceError):
        service.import_history(path)
    with pytest.raises(ServiceError):
        service.import_history(tmp_path / "missing.json")


def test_dry_run_reports_counts_and_writes_nothing(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    bot_id = _bot_offer(repo)
    before = [r.model_dump() for r in repo.all_offers()]
    path = _write(
        tmp_path, [_record(1), _record(2, url_oferta=None), _record(3, estado="x")]
    )
    result = service.import_history(path, dry_run=True)
    assert result.dry_run is True
    assert (result.inserted, result.merged, result.skipped) == (1, 1, 0)
    assert len(result.invalid) == 1
    assert [r.model_dump() for r in repo.all_offers()] == before
    assert repo.get_offer(bot_id).notas is None
    # a real run afterwards still imports everything the dry run promised
    real = service.import_history(path)
    assert (real.inserted, real.merged) == (1, 1)


def test_refresh_runs_once_after_a_real_import(
    svc: tuple[JobService, OfferRepository],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = svc
    calls: list[int] = []
    monkeypatch.setattr(service, "_refresh_tracker", lambda: calls.append(1))
    path = _write(tmp_path, [_record(1), _record(2, url_oferta=None)])
    service.import_history(path, dry_run=True)
    assert calls == []
    service.import_history(path)
    assert calls == [1]  # once, not per record
    service.import_history(path)  # nothing written -> no refresh
    assert calls == [1]


def test_real_import_rewrites_the_synced_xlsx(tmp_path: Path) -> None:
    target = tmp_path / "sync" / "ofertas_db.xlsx"
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, _ = make_service(tmp_path, llm, fetcher, tracker_xlsx=target)
    service.import_history(_write(tmp_path, [_record(1)]))
    sheet = openpyxl.load_workbook(target).active
    assert sheet is not None
    assert len(list(sheet.iter_rows())) == 2  # header + the imported row


def test_imported_rows_stay_unanalyzed(
    svc: tuple[JobService, OfferRepository], tmp_path: Path
) -> None:
    service, repo = svc
    service.import_history(_write(tmp_path, [_record(1)]))
    assert repo.latest_match(repo.all_offers()[0].id) is None
