"""Synced full-tracker xlsx: all-offers export + best-effort atomic refresh.

Zero network, zero real LLM: reuses the auto-triage fakes. The target file
lives under ``tmp_path``; failures are warn-only (same contract as the CV
mirror) and never change a service action's result.
"""

import asyncio
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from cva.config import Settings
from cva.fetch import FetchResponse
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from cva.tracker.xlsx import (
    S1_COLUMNS,
    SYNCED_COLUMNS,
    export_all_xlsx,
    refresh_tracker_xlsx,
)
from tests.test_auto_triage import (
    JSONLD_BODY,
    OFFER_TEXT,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)


def _rows(path: Path, sheet_name: str = "Todas") -> list[dict[str, Any]]:
    sheet = openpyxl.load_workbook(path)[sheet_name]
    header, *body = list(sheet.iter_rows(values_only=True))
    return [dict(zip(header, row, strict=True)) for row in body]


def _tracker_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: Path | None
) -> tuple[JobService, OfferRepository, int]:
    monkeypatch.chdir(tmp_path)
    llm = FakeLlmClient(_extraction())
    fetcher = FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    service, repo = make_service(tmp_path, llm, fetcher, tracker_xlsx=target)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    return service, repo, offer_id


@pytest.fixture()
def repo(tmp_path: Path) -> OfferRepository:
    return OfferRepository(sqlite3.connect(tmp_path / "app.db"))


def _offer(n: int) -> OfferInput:
    return OfferInput(
        job_id=str(n),
        title=f"T{n}",
        company=f"C{n}",
        url=f"https://x/{n}",
        text=f"text {n}",
        source="link",
    )


# --- export -------------------------------------------------------------------


def test_export_all_lists_every_estado_newest_first(
    repo: OfferRepository, tmp_path: Path
) -> None:
    a, b, c = (repo.upsert_offer(_offer(n)) for n in (1, 2, 3))
    repo.set_estado(b, "analizada")
    repo.set_estado(c, "descartada")
    target = tmp_path / "all.xlsx"

    assert export_all_xlsx(repo, target) == target

    rows = _rows(target)
    assert [r["id"] for r in rows] == [c, b, a]
    assert [r["estado"] for r in rows] == [
        "descartada",
        "analizada",
        "pendiente_analisis",
    ]
    sheet = openpyxl.load_workbook(target)["Todas"]
    assert [cell.value for cell in sheet[1]] == list(SYNCED_COLUMNS)


def test_sent_sheet_opens_first_and_lists_only_sent_offers(
    repo: OfferRepository, tmp_path: Path
) -> None:
    """Owner layout: the workbook opens on 'Enviadas' (sent offers, newest
    first); 'Todas' is the second tab and still lists every offer."""
    a, b, c = (repo.upsert_offer(_offer(n)) for n in (1, 2, 3))
    repo.mark_sent(a, cv_path="a.pdf", cambios="x", match=60)
    repo.set_estado(b, "descartada")
    repo.mark_sent(c, cv_path="c.pdf", cambios="x", match=70)
    repo.set_estado(c, "sin_respuesta")  # post-send state stays "sent"
    target = export_all_xlsx(repo, tmp_path / "all.xlsx")

    book = openpyxl.load_workbook(target)
    assert book.sheetnames == ["Enviadas", "Todas"]
    assert book.active is not None and book.active.title == "Enviadas"
    assert [r["id"] for r in _rows(target, "Enviadas")] == [c, a]
    assert [r["id"] for r in _rows(target, "Todas")] == [c, b, a]


def test_all_sheet_rows_are_coloured_by_estado(
    repo: OfferRepository, tmp_path: Path
) -> None:
    from cva.tracker.xlsx import ESTADO_FILLS

    a, b = (repo.upsert_offer(_offer(n)) for n in (1, 2))
    repo.mark_sent(a, cv_path="a.pdf", cambios="x", match=60)
    repo.set_estado(b, "descartada")
    target = export_all_xlsx(repo, tmp_path / "all.xlsx")
    sheet = openpyxl.load_workbook(target)["Todas"]
    by_id = {row[0].value: row for row in sheet.iter_rows(min_row=2)}
    assert ESTADO_FILLS["enviada"] != ESTADO_FILLS["descartada"]
    for offer_id, estado in ((a, "enviada"), (b, "descartada")):
        cells = by_id[offer_id]
        colours = {c.fill.fgColor.rgb[-6:] for c in cells}
        assert colours == {ESTADO_FILLS[estado]}


def test_export_all_drops_source_data(repo: OfferRepository, tmp_path: Path) -> None:
    repo.upsert_offer(_offer(1))
    target = export_all_xlsx(repo, tmp_path / "all.xlsx")
    assert "source_data" not in SYNCED_COLUMNS
    assert "source_data" not in _rows(target)[0]
    assert SYNCED_COLUMNS == (
        *(c for c in S1_COLUMNS if c != "source_data"),
        "perfil",
        "salario_investigado",
        "carta_presentacion",
    )


def test_export_all_match_falls_back_to_latest_score(
    repo: OfferRepository, tmp_path: Path
) -> None:
    analyzed, both, none = (repo.upsert_offer(_offer(n)) for n in (1, 2, 3))
    repo.save_match_result(analyzed, score=72, threshold=65, breakdown_json="{}")
    repo.save_match_result(both, score=40, threshold=65, breakdown_json="{}")
    repo.mark_sent(both, cv_path="cv.pdf", cambios="c", match=88)

    rows = {r["id"]: r for r in _rows(export_all_xlsx(repo, tmp_path / "all.xlsx"))}

    assert rows[analyzed]["match"] == 72  # fallback to match_results.score
    assert rows[both]["match"] == 88  # offers.match wins
    assert rows[none]["match"] is None  # no match anywhere


# --- refresh helper -----------------------------------------------------------


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_refresh_disabled_does_nothing(
    repo: OfferRepository,
    tmp_path: Path,
    configured: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    repo.upsert_offer(_offer(1))
    before = {p.name for p in tmp_path.iterdir()}
    assert refresh_tracker_xlsx(repo, configured) is False
    assert {p.name for p in tmp_path.iterdir()} == before


def test_refresh_creates_parent_dir_and_leaves_only_target(
    repo: OfferRepository, tmp_path: Path
) -> None:
    repo.upsert_offer(_offer(1))
    target = tmp_path / "synced" / "sub" / "tracker.xlsx"
    assert refresh_tracker_xlsx(repo, target) is True
    assert [p.name for p in target.parent.iterdir()] == ["tracker.xlsx"]


def test_refresh_is_atomic_temp_then_replace(
    repo: OfferRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.upsert_offer(_offer(1))
    target = tmp_path / "tracker.xlsx"
    calls: list[tuple[Path, Path]] = []
    real_replace = __import__("os").replace

    def spy(src: Any, dst: Any) -> None:
        assert Path(src).is_file() and Path(src) != target
        assert Path(src).parent == target.parent
        assert not target.exists()  # target untouched until the swap
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("cva.tracker.xlsx.os.replace", spy)
    assert refresh_tracker_xlsx(repo, target) is True
    assert len(calls) == 1 and calls[0][1] == target
    assert not calls[0][0].exists()


def test_refresh_overwrites_previous_content(
    repo: OfferRepository, tmp_path: Path
) -> None:
    target = tmp_path / "tracker.xlsx"
    repo.upsert_offer(_offer(1))
    refresh_tracker_xlsx(repo, target)
    repo.upsert_offer(_offer(2))
    refresh_tracker_xlsx(repo, target)
    assert len(_rows(target)) == 2


@pytest.mark.parametrize("failing", ["os.replace", "openpyxl.Workbook.save"])
def test_refresh_failure_warns_cleans_temp_and_keeps_old_file(
    repo: OfferRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failing: str,
) -> None:
    repo.upsert_offer(_offer(1))
    target = tmp_path / "tracker.xlsx"
    refresh_tracker_xlsx(repo, target)
    repo.upsert_offer(_offer(2))

    def boom(*_a: Any, **_k: Any) -> None:
        raise PermissionError("file open in Excel")

    monkeypatch.setattr(
        "cva.tracker.xlsx.os.replace"
        if failing == "os.replace"
        else "openpyxl.Workbook.save",
        boom,
    )
    with caplog.at_level(logging.WARNING, logger="cva.tracker.xlsx"):
        assert refresh_tracker_xlsx(repo, target) is False

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "tracker.xlsx").is_file()
    assert len(_rows(target)) == 1  # previous projection intact


# --- bounded retry on a locked target -----------------------------------------


def _lock_replace(
    monkeypatch: pytest.MonkeyPatch, failures: int | None
) -> dict[str, int]:
    """Make os.replace onto *.xlsx raise PermissionError ``failures`` times
    (None = forever); ``calls`` counts the attempts."""
    real_replace = os.replace
    state = {"calls": 0, "unlocked": 0}

    def flaky(src: Any, dst: Any, *a: Any, **k: Any) -> None:
        if str(dst).endswith(".xlsx"):
            state["calls"] += 1
            if not state["unlocked"] and (
                failures is None or state["calls"] <= failures
            ):
                raise PermissionError("locked by sync")
        real_replace(src, dst, *a, **k)

    monkeypatch.setattr("cva.tracker.xlsx.os.replace", flaky)
    return state


def test_refresh_retries_a_locked_target_with_backoff_then_succeeds(
    repo: OfferRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.upsert_offer(_offer(1))
    target = tmp_path / "tracker.xlsx"
    state = _lock_replace(monkeypatch, failures=2)
    sleeps: list[float] = []
    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", sleeps.append)

    assert refresh_tracker_xlsx(repo, target, attempts=3) is True

    assert state["calls"] == 3
    assert sleeps == [0.5, 1.0]  # backoff between attempts, none after success
    assert len(_rows(target)) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_refresh_gives_up_after_the_bounded_attempts_with_one_warning(
    repo: OfferRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo.upsert_offer(_offer(1))
    target = tmp_path / "tracker.xlsx"
    state = _lock_replace(monkeypatch, failures=None)
    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", lambda _s: None)

    with caplog.at_level(logging.WARNING, logger="cva.tracker.xlsx"):
        assert refresh_tracker_xlsx(repo, target, attempts=3) is False

    assert state["calls"] == 3
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_refresh_default_is_a_single_attempt_without_sleeping(
    repo: OfferRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.upsert_offer(_offer(1))
    state = _lock_replace(monkeypatch, failures=None)

    def no_sleep(_s: float) -> None:
        raise AssertionError("the default path must never block")

    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", no_sleep)
    assert refresh_tracker_xlsx(repo, tmp_path / "tracker.xlsx") is False
    assert state["calls"] == 1


# --- service seam -------------------------------------------------------------


def test_each_mutator_refreshes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "synced" / "tracker.xlsx"
    service, _repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)

    rows = _rows(target)  # ingest
    assert [(r["id"], r["estado"]) for r in rows] == [(offer_id, "pendiente_analisis")]

    asyncio.run(service.analyze(offer_id))
    assert _rows(target)[0]["estado"] == "analizada"

    asyncio.run(service.generate_cv(offer_id, "AI"))
    assert _rows(target)[0]["cv_adaptado"] is not None

    service.mark_sent(offer_id)
    sent = _rows(target)[0]
    assert sent["estado"] == "enviada" and sent["fecha_envio"] is not None

    other = asyncio.run(service.ingest(None, OFFER_TEXT + " otra"))
    service.discard(other)
    by_id = {r["id"]: r["estado"] for r in _rows(target)}
    assert by_id == {offer_id: "enviada", other: "descartada"}


def test_disabled_setting_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repo, offer_id = _tracker_service(tmp_path, monkeypatch, None)
    asyncio.run(service.analyze(offer_id))
    service.discard(offer_id)
    assert not list(tmp_path.glob("**/*.xlsx"))


def test_locked_target_never_fails_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = tmp_path / "synced" / "tracker.xlsx"
    service, repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)

    real_replace = os.replace

    def locked(src: Any, dst: Any, *a: Any, **k: Any) -> None:
        # Only the synced tracker file is locked (Excel open on Windows);
        # other atomic writes (e.g. CV delivery) must keep working.
        if str(dst).endswith(".xlsx"):
            raise PermissionError("locked")
        real_replace(src, dst, *a, **k)

    monkeypatch.setattr("cva.tracker.xlsx.os.replace", locked)
    with caplog.at_level(logging.WARNING, logger="cva.tracker.xlsx"):
        result = asyncio.run(service.analyze(offer_id))
        pdf = asyncio.run(service.generate_cv(offer_id, "AI"))
        service.mark_sent(offer_id)
        other = asyncio.run(service.ingest(None, OFFER_TEXT + " otra"))
        service.discard(other)

    assert result.score >= 0 and pdf.is_file()
    assert repo.get_offer(offer_id).estado == "enviada"
    assert repo.get_offer(other).estado == "descartada"
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not list((tmp_path / "synced").glob("*.tmp"))


def test_failed_refresh_marks_dirty_and_the_retry_catches_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    target = tmp_path / "synced" / "tracker.xlsx"
    state = _lock_replace(monkeypatch, failures=None)
    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", lambda _s: None)
    service, repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)

    # The mutation succeeded, the projection did not: dirty, nothing blocked.
    assert repo.get_offer(offer_id).estado == "pendiente_analisis"
    assert service.tracker_dirty is True
    assert not target.exists()
    inline_calls = state["calls"]

    # Still locked: the heartbeat retry keeps it dirty (bounded attempts).
    assert asyncio.run(service.retry_tracker_refresh()) is False
    assert service.tracker_dirty is True
    assert state["calls"] > inline_calls

    # Unlocked: next tick writes the current state, clears dirty, logs INFO.
    state["unlocked"] = 1
    with caplog.at_level(logging.INFO, logger="cva.service"):
        assert asyncio.run(service.retry_tracker_refresh()) is True
    assert service.tracker_dirty is False
    assert [(r["id"], r["estado"]) for r in _rows(target)] == [
        (offer_id, "pendiente_analisis")
    ]
    assert any(
        r.levelno == logging.INFO and "tracker" in r.getMessage()
        for r in caplog.records
    )


def test_retry_succeeds_when_the_lock_clears_within_the_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "synced" / "tracker.xlsx"
    service, _repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)
    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", lambda _s: None)
    service._tracker_dirty = True
    target.unlink()
    state = _lock_replace(monkeypatch, failures=2)

    assert asyncio.run(service.retry_tracker_refresh()) is True
    assert state["calls"] == 3
    assert service.tracker_dirty is False
    assert [r["id"] for r in _rows(target)] == [offer_id]


def test_retry_is_a_noop_when_clean_or_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "synced" / "tracker.xlsx"
    service, _repo, _offer_id = _tracker_service(tmp_path, monkeypatch, target)
    assert service.tracker_dirty is False
    target.unlink()
    assert asyncio.run(service.retry_tracker_refresh()) is False  # clean: no I/O
    assert not target.exists()

    (tmp_path / "off").mkdir()
    disabled, _r, _i = _tracker_service(tmp_path / "off", monkeypatch, None)
    disabled._tracker_dirty = True  # even if somehow dirty: nothing to write
    assert asyncio.run(disabled.retry_tracker_refresh()) is False
    assert not list((tmp_path / "off").glob("**/*.xlsx"))


def test_mutation_during_retry_leaves_the_tracker_dirty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry writes a snapshot off-loop; a mutation that lands meanwhile
    may be overwritten by that older snapshot, so it must stay dirty."""
    target = tmp_path / "synced" / "tracker.xlsx"
    service, _repo, _offer_id = _tracker_service(tmp_path, monkeypatch, target)
    service._tracker_dirty = True

    async def scenario() -> bool:
        real_to_thread = asyncio.to_thread

        async def to_thread_with_mutation(fn: Any, *a: Any, **k: Any) -> Any:
            service._refresh_tracker()  # a handler mutated while we wrote
            return await real_to_thread(fn, *a, **k)

        monkeypatch.setattr("cva.service.asyncio.to_thread", to_thread_with_mutation)
        return await service.retry_tracker_refresh()

    asyncio.run(scenario())
    assert service.tracker_dirty is True


# --- deferred (bot-mode) writes -----------------------------------------------


def _deferred_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, debounce: float = 0.05
) -> tuple[JobService, OfferRepository, int, Path]:
    """Tracker service already written once (ingest), then switched to the
    bot's deferred mode with a short debounce."""
    target = tmp_path / "synced" / "tracker.xlsx"
    service, repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)
    monkeypatch.setattr("cva.service.TRACKER_DEBOUNCE_SECONDS", debounce)
    service.enable_deferred_tracker_writes()
    return service, repo, offer_id, target


def _count_writes(
    monkeypatch: pytest.MonkeyPatch, delay: float = 0.0
) -> dict[str, int]:
    """Wrap the workbook writer: count writes, track concurrent writers, and
    optionally make each write slow (a big tracker / slow sync folder)."""
    import threading
    import time

    from cva.tracker import xlsx as xlsx_mod

    real = xlsx_mod._write_tracker_book
    state = {"writes": 0, "active": 0, "max_active": 0}
    guard = threading.Lock()

    def slow(offers: Any, path: Path) -> None:
        with guard:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(delay)
            real(offers, path)
            with guard:
                state["writes"] += 1
        finally:
            with guard:
                state["active"] -= 1

    monkeypatch.setattr(xlsx_mod, "_write_tracker_book", slow)
    return state


async def _until_clean(service: JobService, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while service.tracker_dirty or (
        service._tracker_flush_task is not None
        and not service._tracker_flush_task.done()
    ):
        assert asyncio.get_running_loop().time() < deadline, "never flushed"
        await asyncio.sleep(0.01)


def test_deferred_mutation_does_not_block_on_the_xlsx_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bot mode: a mutation returns immediately (the xlsx write is slow) and
    the file is written soon after, off the loop, with the new state."""
    import time

    service, _repo, offer_id, target = _deferred_service(tmp_path, monkeypatch)
    state = _count_writes(monkeypatch, delay=1.0)

    async def scenario() -> tuple[float, int]:
        started = time.perf_counter()
        service.discard(offer_id)
        elapsed = time.perf_counter() - started
        # The loop stays responsive while the slow write runs in a thread.
        ticks = 0
        while service.tracker_dirty:
            await asyncio.sleep(0.01)
            ticks += 1
        await _until_clean(service)
        return elapsed, ticks

    elapsed, ticks = asyncio.run(scenario())

    assert elapsed < 0.5  # the 1 s write did not run inside discard (slow CI margin)
    assert ticks > 10  # the loop kept ticking during the write
    assert state["writes"] == 1
    assert service.tracker_dirty is False
    assert _rows(target)[0]["estado"] == "descartada"


def test_two_quick_deferred_mutations_produce_one_consistent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repo, offer_id, target = _deferred_service(tmp_path, monkeypatch)
    other = asyncio.run(service.ingest(None, OFFER_TEXT + " otra"))
    state = _count_writes(monkeypatch)

    async def scenario() -> None:
        service.discard(offer_id)
        service.discard(other)
        await _until_clean(service)

    asyncio.run(scenario())

    assert state["writes"] == 1  # debounced: one write for the burst
    by_id = {r["id"]: r["estado"] for r in _rows(target)}
    assert by_id == {offer_id: "descartada", other: "descartada"}
    assert not list((tmp_path / "synced").glob("*.tmp"))


def test_mutation_during_a_deferred_write_is_written_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutation landing while the background write runs is not lost: the
    file ends with the newest state and the tracker clean."""
    service, _repo, offer_id, target = _deferred_service(tmp_path, monkeypatch)
    other = asyncio.run(service.ingest(None, OFFER_TEXT + " otra"))
    state = _count_writes(monkeypatch, delay=0.2)

    async def scenario() -> None:
        service.discard(offer_id)
        while state["active"] == 0:  # first write in flight
            await asyncio.sleep(0.005)
        service.discard(other)
        await _until_clean(service)

    asyncio.run(scenario())

    by_id = {r["id"]: r["estado"] for r in _rows(target)}
    assert by_id == {offer_id: "descartada", other: "descartada"}
    assert service.tracker_dirty is False
    assert state["max_active"] == 1


def test_deferred_flush_and_heartbeat_never_write_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repo, offer_id, target = _deferred_service(
        tmp_path, monkeypatch, debounce=0.0
    )
    state = _count_writes(monkeypatch, delay=0.1)

    async def scenario() -> None:
        service.discard(offer_id)
        await asyncio.sleep(0.02)  # the debounced flush is writing
        await service.retry_tracker_refresh()  # heartbeat tick meanwhile
        await _until_clean(service)

    asyncio.run(scenario())

    assert state["max_active"] == 1
    assert _rows(target)[0]["estado"] == "descartada"


def test_older_snapshot_never_overwrites_a_newer_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repo, offer_id, target = _deferred_service(tmp_path, monkeypatch)
    stale = repo.tracker_offers()
    service.discard(offer_id)  # no loop: written inline (newest)
    assert _rows(target)[0]["estado"] == "descartada"

    assert service._write_tracker_snapshot(stale, 0, 1) is True
    assert _rows(target)[0]["estado"] == "descartada"


def test_pending_deferred_write_is_flushed_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repo, offer_id, target = _deferred_service(
        tmp_path, monkeypatch, debounce=60.0
    )

    async def scenario() -> None:
        service.discard(offer_id)
        assert service.tracker_dirty is True
        assert _rows(target)[0]["estado"] == "pendiente_analisis"
        await service.flush_tracker()

    asyncio.run(scenario())

    assert service.tracker_dirty is False
    assert service._tracker_timer is None
    assert _rows(target)[0]["estado"] == "descartada"


def test_deferred_locked_write_stays_dirty_for_the_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locked file in bot mode: one quick attempt, the tracker stays dirty and
    the heartbeat retry writes it once the lock clears (unchanged backstop)."""
    service, _repo, offer_id, target = _deferred_service(tmp_path, monkeypatch)
    monkeypatch.setattr("cva.tracker.xlsx.time.sleep", lambda _s: None)
    state = _lock_replace(monkeypatch, failures=None)

    async def scenario() -> None:
        service.discard(offer_id)
        while state["calls"] == 0 or (
            service._tracker_flush_task is not None
            and not service._tracker_flush_task.done()
        ):
            await asyncio.sleep(0.01)
        assert service.tracker_dirty is True
        assert state["calls"] == 1  # one attempt, no backoff on the flush
        state["unlocked"] = 1
        assert await service.retry_tracker_refresh() is True

    asyncio.run(scenario())

    assert service.tracker_dirty is False
    assert _rows(target)[0]["estado"] == "descartada"


def test_without_deferred_mode_mutations_still_write_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI/scripts never enable deferral: even inside a running loop the
    write happens before the mutation returns, as before."""
    target = tmp_path / "synced" / "tracker.xlsx"
    service, _repo, offer_id = _tracker_service(tmp_path, monkeypatch, target)

    async def scenario() -> None:
        service.discard(offer_id)
        assert _rows(target)[0]["estado"] == "descartada"
        assert service.tracker_dirty is False

    asyncio.run(scenario())


def test_from_settings_wires_tracker_xlsx_path(tmp_path: Path) -> None:
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
        tracker_xlsx_path=str(tmp_path / "t.xlsx"),
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "s.db")
    assert service._tracker_xlsx == tmp_path / "t.xlsx"


def test_tracker_xlsx_path_defaults_none() -> None:
    settings = Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file="absent.toml",
    )
    assert settings.tracker_xlsx_path is None
