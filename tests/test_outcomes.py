"""Outcome recording: offer_events table, service, /outcome, buttons, notes."""

import asyncio
import sqlite3
import time
from pathlib import Path

import openpyxl
import pytest

import bot
from cva.notifier import (
    OE,
    OUT,
    outcome_keyboard,
    sent_outcome_keyboard,
)
from cva.service import JobService, ServiceError
from cva.tracker import OfferRepository
from tests.helpers import numbered_offer, run_coro
from tests.test_bot import (
    PASTE_TEXT,
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    make_service,
)

ESTADOS = [
    "screening",
    "entrevista_tecnica",
    "entrevista_final",
    "oferta",
    "aceptada",
    "rechazada",
    "sin_respuesta",
    "retirada",
]
PROMPT = "¿Quieres añadir una nota (feedback)? Escríbela o /skip"


def _service(tmp_path: Path) -> tuple[JobService, OfferRepository]:
    from tests.test_bot import FetchResponse

    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", "https://x/1"))
    )
    return service, service._repo


def _sent_offer(tmp_path: Path) -> tuple[JobService, OfferRepository, int]:
    service, repo = _service(tmp_path)
    offer_id = repo.upsert_offer(numbered_offer())
    repo.mark_sent(offer_id, cv_path="cv.pdf", cambios="c", match=70)
    return service, repo, offer_id


# --- repository -------------------------------------------------------------


def test_fresh_db_has_offer_events(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "a.db")
    OfferRepository(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(offer_events)")]
    assert cols == ["id", "offer_id", "at", "kind", "estado", "note"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 16


def test_v15_database_migrates_to_v16_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "v15.db"
    first = sqlite3.connect(path)
    OfferRepository(first)
    first.execute("PRAGMA user_version = 15")  # interrupted: table exists
    first.commit()
    first.close()
    again = sqlite3.connect(path)
    OfferRepository(again)
    assert again.execute("PRAGMA user_version").fetchone()[0] >= 16
    again.execute("DROP TABLE offer_events")
    again.execute("PRAGMA user_version = 15")
    again.commit()
    again.close()
    third = sqlite3.connect(path)
    OfferRepository(third)
    assert third.execute("PRAGMA user_version").fetchone()[0] >= 16
    assert third.execute("SELECT count(*) FROM offer_events").fetchone()[0] == 0


def test_add_event_roundtrip_in_order_and_touches_fecha_update(
    tmp_path: Path,
) -> None:
    today = ["2026-10-01"]
    conn = sqlite3.connect(tmp_path / "a.db")
    repo = OfferRepository(conn, clock=lambda: today[0])
    offer_id = repo.upsert_offer(numbered_offer())
    repo.add_event(offer_id, "estado", estado="screening")
    today[0] = "2026-10-02"
    repo.add_event(offer_id, "note", note="llamada con RRHH")
    events = repo.list_events(offer_id)
    assert [(e.kind, e.estado, e.note) for e in events] == [
        ("estado", "screening", None),
        ("note", None, "llamada con RRHH"),
    ]
    assert [e.at for e in events] == ["2026-10-01", "2026-10-02"]
    assert repo.get_offer(offer_id).fecha_update == "2026-10-02"


def test_add_event_rejects_unknown_kind(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "a.db"))
    offer_id = repo.upsert_offer(numbered_offer())
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_event(offer_id, "bogus")


# --- service ------------------------------------------------------------------


def test_record_outcome_updates_estado_and_logs_event(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    service.record_outcome(offer_id, "entrevista_tecnica")
    assert repo.get_offer(offer_id).estado == "entrevista_tecnica"
    (event,) = service.outcome_events(offer_id)
    assert (event.kind, event.estado) == ("estado", "entrevista_tecnica")


def test_record_outcome_refuses_unsent_offer_and_unknown_estado(
    tmp_path: Path,
) -> None:
    service, repo = _service(tmp_path)
    unsent = repo.upsert_offer(numbered_offer())
    with pytest.raises(ServiceError):
        service.record_outcome(unsent, "screening")
    sent = repo.upsert_offer(numbered_offer(2))
    repo.mark_sent(sent, cv_path="cv.pdf", cambios="c", match=70)
    with pytest.raises(ServiceError):
        service.record_outcome(sent, "descartada")
    assert service.outcome_events(sent) == []
    assert service.outcome_events(unsent) == []


def test_add_outcome_note_is_a_note_event(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    service.add_outcome_note(offer_id, "  Piden prueba técnica  ")
    (event,) = service.outcome_events(offer_id)
    assert (event.kind, event.note) == ("note", "Piden prueba técnica")


def test_record_estado_event_is_atomic(tmp_path: Path) -> None:
    _, repo, offer_id = _sent_offer(tmp_path)
    repo._conn.execute(
        "CREATE TRIGGER boom BEFORE INSERT ON offer_events "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    with pytest.raises(sqlite3.Error):
        repo.record_estado_event(offer_id, "rechazada")
    assert repo.get_offer(offer_id).estado == "enviada"
    assert repo.list_events(offer_id) == []


def test_record_estado_event_changes_estado_and_logs_in_one_call(
    tmp_path: Path,
) -> None:
    _, repo, offer_id = _sent_offer(tmp_path)
    repo.record_estado_event(offer_id, "screening")
    assert repo.get_offer(offer_id).estado == "screening"
    (event,) = repo.list_events(offer_id)
    assert (event.kind, event.estado) == ("estado", "screening")


def test_record_outcome_uses_the_atomic_repo_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    calls: list[tuple[int, str]] = []
    real = repo.record_estado_event
    monkeypatch.setattr(
        repo,
        "record_estado_event",
        lambda i, e: (calls.append((i, e)), real(i, e))[1],
    )
    service.record_outcome(offer_id, "oferta")
    assert calls == [(offer_id, "oferta")]


def test_resend_is_logged_as_event(tmp_path: Path) -> None:
    service, _repo = _service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    pdf = asyncio.run(service.generate_cv(offer_id, "AI"))
    service.mark_sent(offer_id, pdf)
    assert service.outcome_events(offer_id) == []
    service.mark_sent(offer_id, pdf)
    assert [e.note for e in service.outcome_events(offer_id)] == ["reenvío"]


def test_outcome_estado_reaches_the_synced_xlsx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_auto_triage import (
        JSONLD_BODY,
        OFFER_URL,
        FakeLlmClient,
        _extraction,
    )
    from tests.test_auto_triage import FakeFetcher as TriageFetcher
    from tests.test_auto_triage import make_service as triage_service
    from tests.test_bot import FetchResponse

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sync" / "tracker.xlsx"
    service, repo = triage_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        TriageFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        tracker_xlsx=target,
    )
    offer_id = repo.upsert_offer(numbered_offer())
    repo.mark_sent(offer_id, cv_path="cv.pdf", cambios="c", match=70)
    service.record_outcome(offer_id, "oferta")
    sheet = openpyxl.load_workbook(target)["Enviadas"]
    header, *rows = list(sheet.iter_rows(values_only=True))
    assert dict(zip(header, rows[0], strict=True))["estado"] == "oferta"


# --- notifier -------------------------------------------------------------------


def test_outcome_keyboards_payloads() -> None:
    data = [b.callback_data for r in outcome_keyboard(7).inline_keyboard for b in r]
    assert data == [f"{OE}:7:{e}" for e in ESTADOS]
    assert all(len(str(d).encode()) <= 64 for d in data)
    sent = [
        b.callback_data for r in sent_outcome_keyboard(7).inline_keyboard for b in r
    ]
    assert sent == [f"{OUT}:7"]


# --- bot --------------------------------------------------------------------------


def test_outcome_command_usage_and_unknown_offer(tmp_path: Path) -> None:
    service, _repo = _service(tmp_path)
    message = FakeMessage()
    run_coro(bot.outcome_command(FakeUpdate(message=message), FakeContext(service)))
    run_coro(
        bot.outcome_command(
            FakeUpdate(message=message), FakeContext(service, args=["999"])
        )
    )
    assert message.replies[0] == "Uso: /outcome <id>"
    assert message.replies[1].startswith("⚠️")


def test_outcome_command_unsent_offer_has_no_buttons(tmp_path: Path) -> None:
    service, repo = _service(tmp_path)
    offer_id = repo.upsert_offer(numbered_offer())
    message = FakeMessage()
    run_coro(
        bot.outcome_command(
            FakeUpdate(message=message),
            FakeContext(service, args=[str(offer_id)]),
        )
    )
    assert "/sent" in message.replies[0]
    assert message.markups == [None]


def test_outcome_command_shows_timeline_and_estado_buttons(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    service.record_outcome(offer_id, "screening")
    service.add_outcome_note(offer_id, "me llamó Ana")
    message = FakeMessage()
    run_coro(
        bot.outcome_command(
            FakeUpdate(message=message), FakeContext(service, args=[str(offer_id)])
        )
    )
    text = message.replies[0]
    assert "screening" in text
    assert "me llamó Ana" in text
    data = [b.callback_data for r in message.markups[0].inline_keyboard for b in r]  # type: ignore[attr-defined]
    # screening is an interview estado: the prep button follows the estados
    assert data == [f"{OE}:{offer_id}:{e}" for e in ESTADOS] + [f"prep:{offer_id}"]


def test_estado_button_records_and_asks_for_a_note(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    query = FakeQuery(f"{OE}:{offer_id}:entrevista_final")
    context = FakeContext(service)
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    assert repo.get_offer(offer_id).estado == "entrevista_final"
    assert query.message.replies[-1] == PROMPT
    assert context.user_data[bot._NOTE_KEY] == offer_id

    note = FakeMessage("Pidieron disponibilidad")
    run_coro(bot.paste_handler(FakeUpdate(message=note), context))
    assert (
        bot._NOTE_KEY not in context.user_data or not context.user_data[bot._NOTE_KEY]
    )
    assert "nota" in note.replies[0].lower()
    kinds = [(e.kind, e.note) for e in service.outcome_events(offer_id)]
    assert kinds == [("estado", None), ("note", "Pidieron disponibilidad")]

    later = FakeMessage("texto suelto")
    run_coro(bot.paste_handler(FakeUpdate(message=later), context))
    assert later.replies == []  # state consumed: plain text is ignored again


def test_skip_clears_pending_note(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    run_coro(
        bot.callback_router(
            FakeUpdate(query=FakeQuery(f"{OE}:{offer_id}:oferta")), context
        )
    )
    skip = FakeMessage()
    run_coro(bot.skip_command(FakeUpdate(message=skip), context))
    assert "sin nota" in skip.replies[0].lower()
    assert [e.kind for e in service.outcome_events(offer_id)] == ["estado"]
    again = FakeMessage()
    run_coro(bot.skip_command(FakeUpdate(message=again), context))
    assert "nada" in again.replies[0].lower()


def test_unknown_estado_callback_changes_nothing(tmp_path: Path) -> None:
    service, repo, offer_id = _sent_offer(tmp_path)
    query = FakeQuery(f"{OE}:{offer_id}:descartada")
    context = FakeContext(service)
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    assert query.message.replies == ["Acción no reconocida."]
    assert repo.get_offer(offer_id).estado == "enviada"
    assert bot._NOTE_KEY not in context.user_data


def test_estado_button_on_unsent_offer_is_refused(tmp_path: Path) -> None:
    service, repo = _service(tmp_path)
    offer_id = repo.upsert_offer(numbered_offer())
    query = FakeQuery(f"{OE}:{offer_id}:screening")
    context = FakeContext(service)
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    assert query.message.replies[0].startswith("⚠️")
    assert bot._NOTE_KEY not in context.user_data


def test_out_button_opens_the_outcome_menu(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    query = FakeQuery(f"{OUT}:{offer_id}")
    run_coro(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.message.markups[0] is not None


def test_sent_confirmations_carry_the_resultado_button(tmp_path: Path) -> None:
    service, _repo = _service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    query = FakeQuery(f"sent:{offer_id}")
    run_coro(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    assert [b.callback_data for r in markup.inline_keyboard for b in r] == [  # type: ignore[attr-defined]
        f"{OUT}:{offer_id}"
    ]
    message = FakeMessage()
    run_coro(
        bot.sent_command(
            FakeUpdate(message=message),
            FakeContext(service, args=[str(offer_id)]),
        )
    )
    assert message.markups[-1] is not None


def test_pasted_offer_wins_over_a_pending_note(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    run_coro(
        bot.callback_router(
            FakeUpdate(query=FakeQuery(f"{OE}:{offer_id}:oferta")), context
        )
    )
    run_coro(bot.analyze_command(FakeUpdate(message=FakeMessage()), context))
    run_coro(bot.paste_handler(FakeUpdate(message=FakeMessage(PASTE_TEXT)), context))
    assert [e.kind for e in service.outcome_events(offer_id)] == ["estado"]


def test_pending_note_expires_after_thirty_minutes(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    run_coro(
        bot.callback_router(
            FakeUpdate(query=FakeQuery(f"{OE}:{offer_id}:oferta")), context
        )
    )
    context.user_data[bot._NOTE_AT_KEY] -= 31 * 60  # type: ignore[operator]
    late = FakeMessage("llegó tarde")
    run_coro(bot.paste_handler(FakeUpdate(message=late), context))
    assert late.replies == []  # treated as ordinary text, nothing said
    assert [e.kind for e in service.outcome_events(offer_id)] == ["estado"]
    assert bot._NOTE_KEY not in context.user_data
    skip = FakeMessage()
    run_coro(bot.skip_command(FakeUpdate(message=skip), context))
    assert "nada" in skip.replies[0].lower()


def test_fresh_note_still_saved_within_the_window(tmp_path: Path) -> None:
    service, _repo, offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    run_coro(
        bot.callback_router(
            FakeUpdate(query=FakeQuery(f"{OE}:{offer_id}:oferta")), context
        )
    )
    context.user_data[bot._NOTE_AT_KEY] -= 29 * 60  # type: ignore[operator]
    run_coro(bot.paste_handler(FakeUpdate(message=FakeMessage("a tiempo")), context))
    assert [e.kind for e in service.outcome_events(offer_id)] == ["estado", "note"]


def test_note_save_reports_repository_errors(tmp_path: Path) -> None:
    service, _repo, _offer_id = _sent_offer(tmp_path)
    context = FakeContext(service)
    context.user_data[bot._NOTE_KEY] = 9999  # no such offer
    context.user_data[bot._NOTE_AT_KEY] = time.time()
    note = FakeMessage("hola")
    run_coro(bot.paste_handler(FakeUpdate(message=note), context))
    assert note.replies and note.replies[0].startswith("⚠️")
