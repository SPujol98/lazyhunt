"""Proposed letters: [Use this one] + [Don't use] (S3). No network."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import bot
from cva.notifier import cover_letter_keyboard
from tests.test_cover_letter_service import _Llm, _service


def _labels(markup) -> dict[str, str]:
    return {b.text: str(b.callback_data) for r in markup.inline_keyboard for b in r}


def test_keyboard_has_both_buttons_in_both_languages() -> None:
    en = _labels(cover_letter_keyboard(7, "a", 3, lang="en"))
    assert en == {"✅ Use this one": "clc:7:3:a", "🗑 Don't use": "cld:7"}
    es = _labels(cover_letter_keyboard(7, "a", 3, lang="es"))
    assert es == {"✅ Usar esta": "clc:7:3:a", "🗑 No usar": "cld:7"}
    assert all(len(d.encode()) <= 64 for d in (*en.values(), *es.values()))


class _Sent:
    _next = 100

    def __init__(self) -> None:
        _Sent._next += 1
        self.message_id = _Sent._next
        self.chat_id = 5


class _Msg:
    def __init__(self, message_id: int = 11) -> None:
        self.chat_id = 5
        self.message_id = message_id
        self.text: str | None = None
        self.replies: list[str] = []
        self.sent: list[_Sent] = []

    async def reply_text(self, text: str, reply_markup: object = None) -> _Sent:
        self.replies.append(text)
        sent = _Sent()
        self.sent.append(sent)
        return sent


class _Bot:
    def __init__(self, fail: bool = False) -> None:
        self.deleted: list[tuple[int, int]] = []
        self.fail = fail

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self.fail:
            raise RuntimeError("Message can't be deleted for everyone")
        self.deleted.append((chat_id, message_id))


class _Query:
    def __init__(self, data: str, message: _Msg) -> None:
        self.data = data
        self.message = message

    async def answer(self) -> None: ...


def _ctx(service, tg: _Bot):
    return SimpleNamespace(
        bot_data={bot._SERVICE_KEY: service, bot._OWNER_KEY: 5},
        user_data={},
        bot=tg,
    )


def _press(service, ctx, data: str, message: _Msg) -> None:
    update = SimpleNamespace(callback_query=_Query(data, message))
    asyncio.run(bot.callback_router(update, ctx))


def test_dont_use_deletes_letter_and_followers(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    tg = _Bot()
    ctx = _ctx(service, tg)
    card = _Msg()
    asyncio.run(
        bot._write_cover_letters_and_reply(
            service, card, oid, ctx.bot_data.setdefault(bot._LETTER_EXTRAS_KEY, {})
        )
    )
    letter, sources = card.sent[1], card.sent[2]
    pressed = _Msg(letter.message_id)
    _press(service, ctx, f"cld:{oid}", pressed)
    assert tg.deleted == [(5, letter.message_id), (5, sources.message_id)]
    assert repo.get_offer(oid).carta_presentacion is None
    assert pressed.replies == []


def test_deletion_failure_replies_a_note(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    ctx = _ctx(service, _Bot(fail=True))
    pressed = _Msg(300)
    _press(service, ctx, f"cld:{oid}", pressed)
    assert len(pressed.replies) == 1 and "No pude quitar" in pressed.replies[0]
    assert repo.get_offer(oid).carta_presentacion is None


def test_use_this_one_still_saves(tmp_path: Path) -> None:
    service, repo, oid = _service(tmp_path, _Llm())
    ctx = _ctx(service, _Bot())
    card = _Msg()
    asyncio.run(bot._write_cover_letters_and_reply(service, card, oid, {}))
    pressed = _Msg(300)
    _press(service, ctx, f"clc:{oid}:1:a", pressed)
    assert pressed.replies[0] == "Guardada la carta en la oferta"
    assert repo.get_offer(oid).carta_presentacion is not None
