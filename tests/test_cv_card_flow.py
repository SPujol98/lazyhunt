"""cv-card-flow: portals whose applications need no CV (Tecnoempleo) and the
CV press that re-sends its card right before the generated document."""

import asyncio

from telegram import InlineKeyboardMarkup

import bot
from cva.ingest.sources import SOURCES, needs_cv, no_cv_label
from cva.notifier import cv_keyboard, no_cv_line, no_cv_reply, with_no_cv_line
from cva.tracker import OfferInput
from tests.test_bot import (
    OFFER_URL,
    PASTE_TEXT,
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    FetchResponse,
    make_service,
)

TECNO_URL = "https://www.tecnoempleo.com/python-dev/rf-a1b2c3d4e5f6a1b2c3d4"


def _payloads(markup: InlineKeyboardMarkup) -> list[str]:
    # Callback buttons only: the "Search elsewhere" row holds URL buttons.
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data
    ]


# --- C1: SourceSpec flag --------------------------------------------------------


def test_only_tecnoempleo_skips_the_cv() -> None:
    assert SOURCES["tecnoempleo"].cv_needed is False
    assert [s for s, spec in SOURCES.items() if not spec.cv_needed] == ["tecnoempleo"]


def test_needs_cv_by_origen_or_url() -> None:
    assert needs_cv("tecnoempleo", None) is False
    assert needs_cv(None, TECNO_URL) is False
    assert needs_cv("linkedin", None) is True
    assert needs_cv(None, OFFER_URL) is True
    assert needs_cv(None, None) is True  # pasted offers keep the CV
    assert needs_cv("otro", None) is True  # unknown origin


# --- C1: keyboard + card line ---------------------------------------------------


def test_cv_keyboard_without_cv_drops_generate_and_toggles() -> None:
    data = _payloads(cv_keyboard(7, cv_needed=False))
    assert not any(d.split(":")[0] in ("cv", "lang", "variant") for d in data)
    assert any(d.startswith("discard:") for d in data)
    assert any(d.startswith("cl:") for d in data)


def test_cv_keyboard_default_unchanged() -> None:
    kinds = [d.split(":")[0] for d in _payloads(cv_keyboard(7))]
    assert kinds[:3] == ["cv", "lang", "variant"]


def test_with_no_cv_line_appends_spanish_line() -> None:
    assert with_no_cv_line("card", "Tecnoempleo").endswith(no_cv_line("Tecnoempleo"))
    assert no_cv_line("Tecnoempleo") == (
        "📨 Tecnoempleo: se aplica con tu perfil del portal (sin CV)"
    )
    assert "Tecnoempleo" in no_cv_reply("Tecnoempleo")


def test_no_cv_text_is_built_from_the_portal_label() -> None:
    assert "OtroPortal" in no_cv_line("OtroPortal")
    assert "Tecnoempleo" not in no_cv_line("OtroPortal")
    assert "Tecnoempleo" not in no_cv_reply("OtroPortal")
    assert no_cv_label("tecnoempleo", None) == SOURCES["tecnoempleo"].label
    assert no_cv_label(None, TECNO_URL) == SOURCES["tecnoempleo"].label
    assert no_cv_label("linkedin", None) is None
    assert no_cv_label(None, None) is None


# --- C1: bot paths --------------------------------------------------------------


def _tecno_offer(tmp_path):  # type: ignore[no-untyped-def]
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = service._repo.upsert_offer(
        OfferInput(
            title="Dev",
            company="Acme",
            url=TECNO_URL,
            text=PASTE_TEXT,
            source="bridge-inline",
        )
    )
    return service, offer_id


def test_service_cv_needed_follows_the_offer_origin(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, tecno_id = _tecno_offer(tmp_path)
    plain_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    assert service.cv_needed(tecno_id) is False
    assert service.cv_needed(plain_id) is True


def test_analyze_reply_for_tecnoempleo_has_no_cv_row(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id = _tecno_offer(tmp_path)
    message = FakeMessage("/analyze")
    asyncio.run(
        bot._analyze_and_reply(service, message, FakeContext(service), offer_id)
    )
    assert message.replies[-1].endswith(no_cv_line("Tecnoempleo"))
    data = _payloads(message.markups[-1])  # type: ignore[arg-type]
    assert not any(d.split(":")[0] in ("cv", "lang", "variant") for d in data)


def test_stale_cv_callback_for_tecnoempleo_does_not_generate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id = _tecno_offer(tmp_path)
    query = FakeQuery(f"cv:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.message.documents == []
    assert service.offer(offer_id).cv_base is None
    assert "Tecnoempleo" in query.message.replies[-1]
    assert query.message.deleted == 0  # the card stays where it is


# --- C2: the card moves next to its CV ------------------------------------------


def _press_cv(tmp_path, *, delete_error=None, fail=False):  # type: ignore[no-untyped-def]
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    service._repo.save_card(offer_id, 5, 11, "CARD", None, None)
    query = FakeQuery(f"cv:{offer_id}:AI:es")
    query.message.text = "CARD TEXT"
    query.message.reply_markup = cv_keyboard(offer_id)
    query.message.delete_error = delete_error
    if fail:

        async def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("render failed")

        service.generate_cv = boom  # type: ignore[method-assign]
    return service, offer_id, query


def test_cv_press_resends_card_then_deletes_old_then_sends_cv(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_cv(tmp_path)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.events[:3] == ["reply_text", "delete", "reply_document"]
    assert msg.replies[0] == "CARD TEXT"
    assert msg.markups[0] is msg.reply_markup  # same keyboard
    assert msg.documents  # CV delivered
    # Final "CV listo" feedback lands on the NEW card, not the deleted one.
    assert msg.events[-1] == "edit_text"
    assert query.edited and msg.sent[0].documents is msg.documents
    assert service._repo.get_card(offer_id) is None  # rewritten card is untracked


def test_cv_press_tolerates_delete_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _oid, query = _press_cv(tmp_path, delete_error=RuntimeError("too old"))
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.events[:3] == ["reply_text", "delete", "reply_document"]
    assert msg.deleted == 0
    assert msg.documents


def test_cv_press_failure_leaves_resent_card_and_moves_stored_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_cv(tmp_path, fail=True)
    try:
        asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    except RuntimeError:
        pass  # surfaced to the global error handler, as before
    else:
        raise AssertionError("generation error must propagate")
    msg = query.message
    assert msg.events == ["reply_text", "delete"]  # card stays, no CV
    card = service._repo.get_card(offer_id)
    assert card is not None
    assert (card.chat_id, card.message_id) == (msg.chat_id, msg.sent[0].message_id)


def test_resend_failure_keeps_the_original_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_cv(tmp_path)

    async def no_network(*_a: object, **_k: object) -> None:
        raise RuntimeError("network down")

    query.message.reply_text = no_network  # type: ignore[method-assign]
    try:
        asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    except RuntimeError as exc:
        assert "network down" in str(exc)
    else:
        raise AssertionError("re-send error must propagate")
    assert query.message.deleted == 0  # never delete before the copy exists
    assert query.message.documents == []  # no CV generated
    assert service.offer(offer_id).cv_base is None
    card = service._repo.get_card(offer_id)
    assert card is not None and card.message_id == 11  # still the original


def test_other_buttons_still_forget_the_pressed_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, _q = _press_cv(tmp_path)
    query = FakeQuery(f"lang:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert service._repo.get_card(offer_id) is None


def test_repo_move_card_repoints_only_that_offer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    repo = service._repo
    first = asyncio.run(service.ingest(None, PASTE_TEXT))
    second = asyncio.run(service.ingest(None, PASTE_TEXT + " Otra."))
    repo.move_card(first, 5, 11, 1, 2)  # no stored card: no-op
    assert repo.get_card(first) is None
    repo.save_card(first, 5, 11, "a", None, None)
    repo.save_card(second, 5, 12, "b", None, None)
    repo.move_card(first, 5, 11, 6, 77)
    moved = repo.get_card(first)
    assert moved is not None and (moved.chat_id, moved.message_id) == (6, 77)
    other = repo.get_card(second)
    assert other is not None and other.message_id == 12


def test_repo_move_card_ignores_a_message_that_is_not_the_stored_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    repo = service._repo
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    repo.save_card(offer_id, 5, 11, "tracked", None, None)
    repo.move_card(offer_id, 5, 999, 6, 77)  # other message of the same offer
    card = repo.get_card(offer_id)
    assert card is not None and (card.chat_id, card.message_id) == (5, 11)


def test_cv_press_on_an_untracked_message_leaves_the_stored_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_cv(tmp_path)
    query.message.message_id = 42  # e.g. a separate /analyze reply
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    card = service._repo.get_card(offer_id)
    assert card is not None and (card.chat_id, card.message_id) == (5, 11)


def test_resend_keeps_formatting_of_entity_cards(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _oid, query = _press_cv(tmp_path)
    query.message.entities = [object()]
    query.message.text_html = '<b>CARD</b> <a href="https://x.y">link</a>'
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.replies[0] == query.message.text_html
    assert msg.parse_modes[0] == "HTML"


def test_resend_keeps_plain_cards_plain(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _oid, query = _press_cv(tmp_path)
    query.message.text_html = "CARD TEXT"  # present but no entities
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.replies[0] == "CARD TEXT"
    assert msg.parse_modes[0] is None


# --- C3: the cover-letter press moves the card too ------------------------------


class _Letters:
    text = "Carta"
    facts: tuple[()] = ()
    generation = 1
    warnings: tuple[str, ...] = ()
    quality = None  # no score: no quality line (telegram-improvements I3)


def _press_letter(tmp_path, *, delete_error=None, fail=False):  # type: ignore[no-untyped-def]
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    service._repo.save_card(offer_id, 5, 11, "CARD", None, None)

    async def letters(_offer_id: int) -> _Letters:
        if fail:
            raise RuntimeError("llm down")
        return _Letters()

    service.cover_letters = letters  # type: ignore[method-assign]
    query = FakeQuery(f"cl:{offer_id}")
    query.message.text = "CARD TEXT"
    query.message.reply_markup = cv_keyboard(offer_id)
    query.message.delete_error = delete_error
    return service, offer_id, query


def test_letter_press_resends_card_then_deletes_old_then_sends_letters(
    tmp_path,
) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_letter(tmp_path)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.events[:2] == ["reply_text", "delete"]
    assert msg.replies[0] == "CARD TEXT"
    assert msg.markups[0] is msg.reply_markup
    new_card = msg.sent[0]
    # Status line, the letter and its sources notice are delivered after the card, on the new card.
    assert new_card.replies[0] == "✍️ Escribiendo carta…"
    assert len(new_card.replies) == 3
    card = service._repo.get_card(offer_id)
    assert card is not None
    assert (card.chat_id, card.message_id) == (msg.chat_id, new_card.message_id)


def test_letter_press_tolerates_delete_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, _oid, query = _press_letter(tmp_path, delete_error=RuntimeError("old"))
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.events[:2] == ["reply_text", "delete"]
    assert msg.deleted == 0
    assert len(msg.sent[0].replies) == 3


def test_letter_failure_leaves_resent_card_and_reports_on_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_letter(tmp_path, fail=True)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    msg = query.message
    assert msg.events[:2] == ["reply_text", "delete"]
    assert msg.sent[0].replies[-1].startswith("⚠️ No pude escribir la carta")
    card = service._repo.get_card(offer_id)
    assert card is not None and card.message_id == msg.sent[0].message_id


def test_letter_press_on_an_untracked_message_leaves_the_stored_card(tmp_path) -> None:  # type: ignore[no-untyped-def]
    service, offer_id, query = _press_letter(tmp_path)
    query.message.message_id = 42
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    card = service._repo.get_card(offer_id)
    assert card is not None and (card.chat_id, card.message_id) == (5, 11)
