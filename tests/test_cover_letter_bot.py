"""Cover letters: keyboards and bot callbacks (T3). No network."""

import asyncio
from pathlib import Path

from telegram import InlineKeyboardMarkup

import bot
from cva.notifier import cv_keyboard, sent_keyboard
from tests.test_cover_letter_service import ES_LETTER, _Llm, _NoCompletionLlm, _service


def _buttons(markup: InlineKeyboardMarkup) -> dict[str, str]:
    return {b.text: str(b.callback_data) for row in markup.inline_keyboard for b in row}


def test_both_keyboards_carry_the_cover_letter_button() -> None:
    for markup in (cv_keyboard(7), sent_keyboard(7, "AI_ES")):
        assert _buttons(markup)["✉️ Carta de presentación"] == "cl:7"


def test_label_highlights_when_the_offer_asks_for_a_letter() -> None:
    for markup in (
        cv_keyboard(7, cover_letter_asked=True),
        sent_keyboard(7, "AI_ES", cover_letter_asked=True),
    ):
        buttons = _buttons(markup)
        assert buttons["✉️ Carta de presentación (la piden)"] == "cl:7"
        assert "✉️ Carta de presentación" not in buttons


class _Msg:
    def __init__(self) -> None:
        self.chat_id = 5
        self.message_id = 11
        self.text: str | None = None  # text-less card: nothing to re-send
        self.replies: list[tuple[str, object]] = []
        self.documents: list[str | None] = []

    async def reply_text(self, text: str, reply_markup: object = None) -> None:
        self.replies.append((text, reply_markup))

    async def reply_document(
        self, document: object, filename: str | None = None
    ) -> None:
        self.documents.append(filename)


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _Msg()

    async def answer(self) -> None: ...


class _Update:
    def __init__(self, query: _Query) -> None:
        self.callback_query = query


class _Ctx:
    def __init__(self, service) -> None:
        self.bot_data = {bot._SERVICE_KEY: service, bot._OWNER_KEY: 5}
        self.user_data: dict[str, object] = {}


def _press(service, data: str) -> list[tuple[str, object]]:
    query = _Query(data)
    asyncio.run(bot.callback_router(_Update(query), _Ctx(service)))
    return query.message.replies


def test_press_sends_progress_then_the_letter_with_a_use_button(tmp_path: Path) -> None:
    service, _repo, oid = _service(tmp_path, _Llm())
    replies = _press(service, f"cl:{oid}")
    assert "Escribiendo carta" in replies[0][0]
    assert replies[1][0].startswith("✉️ Carta") and ES_LETTER in replies[1][0]
    assert all(len(text) <= 4096 for text, _ in replies)
    # The button carries the generation (1 = first press) to reject stale picks.
    assert _buttons(replies[1][1]) == {
        "✅ Usar esta": f"clc:{oid}:1:a",
        "🗑 No usar": f"cld:{oid}",
    }  # type: ignore[arg-type]
    # No researched facts (no web search): the owner is told paragraph 2 is thin.
    assert "No encontré datos fiables de la empresa" in replies[2][0]
    assert len(replies) == 3  # no warnings for a clean letter


def test_choose_persists_and_confirms(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    _press(service, f"cl:{oid}")
    assert _press(service, f"clc:{oid}:1:a")[-1][0] == "Guardada la carta en la oferta"
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER


def test_choose_without_generated_pair_replies_error(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    assert "⚠️" in _press(service, f"clc:{oid}:1:a")[-1][0]
    assert repo.get_offer(oid).carta_presentacion is None


def test_stale_use_button_is_refused(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    _press(service, f"cl:{oid}")
    _press(service, f"cl:{oid}")
    assert "⚠️" in _press(service, f"clc:{oid}:1:a")[-1][0]
    assert repo.get_offer(oid).carta_presentacion is None


def test_llm_failure_replies_spanish_error_and_stores_nothing(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm(fail=True))
    replies = _press(service, f"cl:{oid}")
    assert "No pude escribir la carta" in replies[-1][0]
    assert repo.get_offer(oid).carta_presentacion is None


def test_unsupported_provider_replies_spanish_error(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _NoCompletionLlm())
    assert "No pude escribir la carta" in _press(service, f"cl:{oid}")[-1][0]
    assert repo.get_offer(oid).carta_presentacion is None
