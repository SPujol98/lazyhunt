"""Reopened offers: a closed-discarded offer that shows up again is reconsidered."""

import sqlite3
from pathlib import Path
from typing import Any

from cva import bridge
from cva.ingest.closed import CLOSED_NOTE
from cva.notifier import DuplicateOffer
from cva.service import CLOSED_NOTE as SERVICE_CLOSED_NOTE
from cva.tracker import OfferInput, OfferRepository


class _Clock:
    def __init__(self, today: str = "2026-10-18") -> None:
        self.today = today

    def __call__(self) -> str:
        return self.today


def _repo(tmp_path: Path, clock: _Clock | None = None) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "r.db"), clock=clock or _Clock())


def _add(repo: OfferRepository, url: str, company: str = "Acme") -> int:
    return repo.upsert_offer(
        OfferInput(
            job_id=url[-3:],
            title="AI Engineer",
            company=company,
            url=url,
            text="texto",
            source="bridge-inline",
        )
    )


def _close(repo: OfferRepository, offer_id: int) -> None:
    repo.set_estado(offer_id, "descartada")
    repo.append_note(offer_id, CLOSED_NOTE)
    repo.add_event(offer_id, "note", note=CLOSED_NOTE)


def test_service_reuses_the_ingest_closed_note() -> None:
    assert SERVICE_CLOSED_NOTE == CLOSED_NOTE == "oferta cerrada"


def test_find_duplicate_ignores_a_closed_original(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/111")
    _close(repo, first)
    assert repo.find_duplicate("acme_ai-engineer", "https://infojobs/9") is None


def test_find_duplicate_still_matches_other_discarded_originals(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    first = _add(repo, "https://linkedin/111")
    repo.set_estado(first, "descartada")
    repo.append_note(first, "no me interesa")
    dup = repo.find_duplicate("acme_ai-engineer", "https://infojobs/9")
    assert dup is not None and dup.id == first


def test_is_closed_discarded_requires_closed_note_and_descartada(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    closed = _add(repo, "https://linkedin/111")
    _close(repo, closed)
    manual = _add(repo, "https://linkedin/222", "Beta")
    repo.set_estado(manual, "descartada")
    live = _add(repo, "https://linkedin/333", "Gamma")
    assert repo.is_closed_discarded(repo.get_offer(closed))
    assert not repo.is_closed_discarded(repo.get_offer(manual))
    assert not repo.is_closed_discarded(repo.get_offer(live))


def test_is_closed_discarded_false_for_an_offer_that_was_sent(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    offer = _add(repo, "https://linkedin/111")
    repo.record_estado_event(offer, "enviada")
    _close(repo, offer)
    assert not repo.is_closed_discarded(repo.get_offer(offer))


def test_is_closed_discarded_false_with_fecha_envio(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer = _add(repo, "https://linkedin/111")
    _close(repo, offer)
    repo._conn.execute(
        "UPDATE offers SET fecha_envio = '2026-10-01' WHERE id = ?", (offer,)
    )
    assert not repo.is_closed_discarded(repo.get_offer(offer))


def test_closed_at_is_the_latest_closed_event(tmp_path: Path) -> None:
    clock = _Clock("2026-10-10")
    repo = _repo(tmp_path, clock)
    offer = _add(repo, "https://linkedin/111")
    _close(repo, offer)
    clock.today = "2026-10-15"
    repo.add_event(offer, "note", note=CLOSED_NOTE)
    assert repo.closed_at(offer) == "2026-10-15"


def test_closed_at_falls_back_to_fecha_update_without_an_event(
    tmp_path: Path,
) -> None:
    clock = _Clock("2026-10-10")
    repo = _repo(tmp_path, clock)
    offer = _add(repo, "https://linkedin/111")
    repo.set_estado(offer, "descartada")
    repo.append_note(offer, CLOSED_NOTE)  # legacy row: no event
    assert repo.closed_at(offer) == "2026-10-10"


def test_reopen_resets_estado_cleans_note_and_logs_event(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer = _add(repo, "https://linkedin/111")
    repo.append_note(offer, "otra nota")
    _close(repo, offer)
    repo._conn.execute(
        "UPDATE offers SET analyze_attempts = 3, next_analyze_at = 'x' WHERE id = ?",
        (offer,),
    )
    repo.reopen_closed(offer, "LinkedIn")
    row = repo.get_offer(offer)
    assert row.estado == "pendiente_analisis"
    assert row.notas == "otra nota"
    assert not repo.is_closed_discarded(row)
    assert row.analyze_attempts == 0
    last = repo.list_events(offer)[-1]
    assert last.kind == "note"
    assert last.note == "reabierta: volvió a aparecer en LinkedIn"


def test_reopen_forgets_a_delivered_fallback_card(tmp_path: Path) -> None:
    """A reopened offer is brand-new again: its old fallback-card mark must not
    make the queue skip it (beta10 S1)."""
    repo = _repo(tmp_path)
    offer = _add(repo, "https://linkedin/111")
    repo.mark_auto_fallback(offer)
    _close(repo, offer)
    repo.reopen_closed(offer, "LinkedIn")
    assert repo.has_auto_fallback(offer) is False


# --- bridge reopen path -------------------------------------------------------------

LI = "https://www.linkedin.com/jobs/view/111"
IJ = (
    "https://www.infojobs.net/barcelona/ai-engineer/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0"
)
OPEN_TEXT = "Buscamos AI Engineer. Python, LLMs. Envía tu CV."
CLOSED_TEXT = "Esta oferta ya no está disponible. oferta cerrada"


def _process(
    repo: OfferRepository, jobs: list[dict[str, Any]]
) -> tuple[dict[str, int], list[Any], list[DuplicateOffer]]:
    auto: list[tuple[int, str, dict[str, Any]]] = []
    dupes: list[DuplicateOffer] = []
    result = bridge._process_payload(
        "b",
        jobs,
        repo=repo,
        sender=lambda *a: None,
        threshold=65,
        auto_jobs=auto,
        duplicates=dupes,
    )
    return result, auto, dupes


def _ij(**over: Any) -> dict[str, Any]:
    job = {"url": IJ, "title": "AI Engineer", "company": "Acme", "text": OPEN_TEXT}
    job.update(over)
    return job


def _li(**over: Any) -> dict[str, Any]:
    job = {"url": LI, "title": "AI Engineer", "company": "Acme"}
    job.update(over)
    return job


def _ij_offer(repo: OfferRepository) -> int:
    canonical = bridge.canonical_url(
        bridge.source_from_url(IJ), bridge.job_id_from_url(IJ), original=IJ
    )
    offer = repo.find_offer_by_url(canonical)
    assert offer is not None
    return offer.id


def _closed_ij(repo: OfferRepository) -> int:
    _process(repo, [_ij(text=OPEN_TEXT)])
    offer_id = _ij_offer(repo)
    _close(repo, offer_id)
    return offer_id


def test_same_url_with_open_text_is_reopened_and_enqueued(tmp_path: Path) -> None:
    clock = _Clock()
    repo = _repo(tmp_path, clock)
    offer_id = _closed_ij(repo)
    clock.today = "2026-10-19"  # REOPEN_INLINE_MIN_DAYS since the closure
    result, auto, dupes = _process(repo, [_ij(text=OPEN_TEXT + " nuevo")])
    assert result == {"received": 1, "new": 1, "skipped": 0}
    assert [a[0] for a in auto] == [offer_id]
    row = repo.get_offer(offer_id)
    assert row.estado == "pendiente_analisis"
    assert (row.source_data or "").endswith("nuevo")
    assert "reabierta" in (repo.list_events(offer_id)[-1].note or "")
    assert dupes == []


def test_same_url_with_still_closed_text_is_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer_id = _closed_ij(repo)
    result, auto, _ = _process(repo, [_ij(text=CLOSED_TEXT)])
    assert result == {"received": 1, "new": 0, "skipped": 1}
    assert auto == []
    assert repo.get_offer(offer_id).estado == "descartada"


def test_url_only_job_reopens_only_after_the_window(tmp_path: Path) -> None:
    clock = _Clock("2026-10-10")
    repo = _repo(tmp_path, clock)
    _process(repo, [_li()])
    canonical = bridge.canonical_url(
        bridge.source_from_url(LI), bridge.job_id_from_url(LI), original=LI
    )
    offer = repo.find_offer_by_url(canonical)
    assert offer is not None
    _close(repo, offer.id)
    clock.today = "2026-10-12"  # 2 days: inside REOPEN_AFTER_DAYS
    result, auto, _ = _process(repo, [_li()])
    assert result["skipped"] == 1 and auto == []
    assert repo.get_offer(offer.id).estado == "descartada"
    clock.today = "2026-10-13"  # 3 days: window reached
    result, auto, _ = _process(repo, [_li()])
    assert result == {"received": 1, "new": 1, "skipped": 0}
    assert [a[0] for a in auto] == [offer.id]
    assert repo.get_offer(offer.id).estado == "pendiente_analisis"


def test_repost_at_another_url_of_a_closed_original_is_a_new_offer(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    original = _closed_ij(repo)
    result, auto, dupes = _process(repo, [_li()])
    assert result == {"received": 1, "new": 1, "skipped": 0}
    assert dupes == []
    assert len(auto) == 1 and auto[0][0] != original


def test_sent_offer_is_never_reopened(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    offer_id = _closed_ij(repo)
    repo.record_estado_event(offer_id, "enviada")
    repo.set_estado(offer_id, "descartada")  # sent, then discarded again
    result, auto, _ = _process(repo, [_ij(text=OPEN_TEXT + " nuevo")])
    assert result["skipped"] == 1 and auto == []
    assert repo.get_offer(offer_id).estado == "descartada"


def test_manually_discarded_offer_is_never_reopened(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _process(repo, [_ij()])
    offer_id = _ij_offer(repo)
    repo.set_estado(offer_id, "descartada")
    repo.append_note(offer_id, "no me interesa")
    result, auto, _ = _process(repo, [_ij(text=OPEN_TEXT + " nuevo")])
    assert result["skipped"] == 1 and auto == []
    assert repo.get_offer(offer_id).estado == "descartada"


def test_live_offer_at_same_url_is_still_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _process(repo, [_ij()])
    result, auto, _ = _process(repo, [_ij()])
    assert result == {"received": 1, "new": 0, "skipped": 1}
    assert auto == []


def test_service_closure_writes_the_event_the_window_reads(tmp_path: Path) -> None:
    import asyncio

    import pytest

    from cva.fetch import FetchResponse
    from cva.service import OfferClosedError
    from tests.test_service import (
        OFFER_URL,
        FakeFetcher,
        FakeLlmClient,
        _extraction,
        make_service,
    )

    service, repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, "", OFFER_URL)),
    )
    offer_id = asyncio.run(service.ingest(None, "AI Engineer. Oferta cerrada."))
    with pytest.raises(OfferClosedError):
        asyncio.run(service.analyze(offer_id))
    events = [e for e in repo.list_events(offer_id) if e.note == CLOSED_NOTE]
    assert len(events) == 1
    assert repo.closed_at(offer_id) == events[0].at[:10]
    assert repo.days_since_closed(offer_id) is not None


def test_reopened_offer_with_a_live_sibling_is_a_duplicate(tmp_path: Path) -> None:
    clock = _Clock("2026-10-18")
    repo = _repo(tmp_path, clock)
    a = _closed_ij(repo)  # A closed at InfoJobs
    result, auto, _ = _process(repo, [_li()])  # B re-posted live at LinkedIn
    assert result["new"] == 1
    b = auto[0][0]
    clock.today = "2026-10-19"
    result, auto, dupes = _process(repo, [_ij(text=OPEN_TEXT + " nuevo")])
    assert result == {"received": 1, "new": 0, "skipped": 1}
    assert auto == []
    assert [d.original_id for d in dupes] == [b]
    row = repo.get_offer(a)
    assert row.estado == "descartada"
    assert f"duplicada de #{b}" in (row.notas or "")


def test_inline_reopen_loop_is_bounded_to_one_per_day(tmp_path: Path) -> None:
    clock = _Clock("2026-10-18")
    repo = _repo(tmp_path, clock)
    offer_id = _closed_ij(repo)  # closed today
    for _ in range(3):  # portal snippet keeps looking open the same day
        result, auto, _ = _process(repo, [_ij(text=OPEN_TEXT + " otra")])
        assert result["skipped"] == 1 and auto == []
    clock.today = "2026-10-19"
    result, auto, _ = _process(repo, [_ij(text=OPEN_TEXT + " otra")])
    assert [a[0] for a in auto] == [offer_id]
    _close(repo, offer_id)  # analysis closes it again the same day
    result, auto, _ = _process(repo, [_ij(text=OPEN_TEXT + " otra")])
    assert result["skipped"] == 1 and auto == []
