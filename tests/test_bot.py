"""Bot handler tests: direct invocation with fake update/context (tasks 8.5).

Zero real Telegram: handlers are plain ``async`` functions exercised with duck
-typed fakes that capture replies. The service seam uses FakeLlmClient +
FakeFetcher over a tmp SQLite repository (same approach as test_service.py).
"""

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from telegram.ext import ApplicationHandlerStop

import bot
from cva.claimsdb import load_claims
from cva.config import Settings
from cva.fetch import FetchResponse
from cva.ingest import WallError  # noqa: F401  (re-exported seam used by handlers)
from cva.llm import LlmResponseError
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
OFFER_URL = "https://www.linkedin.com/jobs/view/4459804598/"
SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPagePostings/search"
    "?keywords=ingeniero%20de%20ia&geoId=105088894"
)
JSONLD_BODY = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "AI Engineer", '
    '"hiringOrganization": {"name": "Acme"}, '
    '"description": "Imprescindible experiencia con Python."}'
    "</script></head></html>"
)
SHELL_BODY = (
    '<html><head><title>LinkedIn</title></head><body><div id="app-js-root"></div>'
    "</body></html>"
)
PASTE_TEXT = (
    "Data Scientist en Beta. Imprescindible experiencia con Python y SQL. "
    "Se valoran conocimientos de LLMs."
)


class FakeLlmClient:
    def __init__(self) -> None:
        self.model = "test-model"
        self.calls = 0

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return OfferExtraction(
            title="AI Engineer",
            company="Acme",
            requirements=[
                Requirement(
                    name="Python",
                    category="skill",
                    must_have=True,
                    evidence="experiencia con Python",
                )
            ],
            keywords=["Python"],
        )


class FailingLlmClient(FakeLlmClient):
    """LLM seam that fails like the real one did in the smoke test (HTTP 401)."""

    def __init__(self, reason: str = "LLM endpoint returned HTTP 401") -> None:
        super().__init__()
        self._reason = reason

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        raise LlmResponseError(self._reason)


class FakeFetcher:
    def __init__(self, response: FetchResponse) -> None:
        self._response = response

    async def get(self, url: str) -> FetchResponse:
        return self._response


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[str] = []
        self.markups: list[object] = []
        self.documents: list[str] = []
        self.chat_id = 5
        self.message_id = 11
        self.reply_markup: object = None
        self.deleted = 0
        self.delete_error: Exception | None = None
        # Shared event log + edit sinks (set by FakeQuery) so order across the
        # original and any re-sent card can be asserted.
        self.events: list[str] = []
        self.edited: list[str] = []
        self.edited_markups: list[object] = []
        self.sent: list[FakeMessage] = []
        self.entities: list[object] = []
        self.text_html: str | None = None
        self.parse_modes: list[str | None] = []

    async def reply_text(
        self,
        text: str,
        reply_markup: object = None,
        parse_mode: str | None = None,
    ) -> "FakeMessage":
        self.events.append("reply_text")
        self.replies.append(text)
        self.markups.append(reply_markup)
        self.parse_modes.append(parse_mode)
        sent = FakeMessage(text)
        sent.message_id = self.message_id + 100 + len(self.sent)
        sent.reply_markup = reply_markup
        sent.events = self.events
        sent.edited = self.edited
        sent.edited_markups = self.edited_markups
        sent.documents = self.documents
        self.sent.append(sent)
        return sent

    async def delete(self) -> bool:
        self.events.append("delete")
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted += 1
        return True

    async def edit_text(self, text: str, reply_markup: object = None) -> None:
        self.events.append("edit_text")
        self.edited.append(text)
        self.edited_markups.append(reply_markup)

    async def reply_document(
        self, document: object, filename: str | None = None
    ) -> None:
        self.events.append("reply_document")
        self.documents.append(filename or "")


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = FakeMessage()
        self.answered = False
        self.edited = self.message.edited
        self.edited_markups = self.message.edited_markups

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, reply_markup: object = None) -> None:
        self.edited.append(text)
        self.edited_markups.append(reply_markup)

    async def edit_message_reply_markup(self, reply_markup: object = None) -> None:
        self.edited_markups.append(reply_markup)


class FakeUpdate:
    def __init__(
        self,
        *,
        message: FakeMessage | None = None,
        query: FakeQuery | None = None,
        user_id: int | None = 5,
    ) -> None:
        self.message = message
        self.callback_query = query
        self.effective_user = (
            None if user_id is None else type("U", (), {"id": user_id})()
        )


class FakeBot:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self._fail = fail

    async def send_message(self, chat_id: int, text: str) -> None:
        if self._fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(
        self,
        service: JobService,
        *,
        args: list[str] | None = None,
        tg_bot: FakeBot | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.bot_data = {bot._SERVICE_KEY: service, bot._OWNER_KEY: 5}
        self.user_data: dict[str, object] = {}
        self.args = args or []
        self.bot = tg_bot if tg_bot is not None else FakeBot()
        self.error = error


def make_service_with_llm(
    tmp_path: Path, fetcher: FakeFetcher, llm: FakeLlmClient
) -> JobService:
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn)
    db = load_claims(CLAIMS_PATH)
    return JobService(
        repo=repo,
        llm=llm,
        fetcher=fetcher,
        db=db,
        syn=load_synonyms(SYNONYMS_PATH, db),
        cache=ExtractionCache(conn),
        threshold=65,
        masters_dir=DEMO_MASTERS,
    )


def make_service(
    tmp_path: Path, fetcher: FakeFetcher
) -> tuple[JobService, FakeLlmClient]:
    llm = FakeLlmClient()
    return make_service_with_llm(tmp_path, fetcher, llm), llm


# --- Whitelist gate -----------------------------------------------------------


def test_gate_stops_strangers_without_reply(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage()
    update = FakeUpdate(message=message, user_id=999)  # stranger
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.gate(update, FakeContext(service)))
    assert message.replies == []  # no reply, no side effects


def test_gate_passes_the_owner(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    asyncio.run(bot.gate(FakeUpdate(user_id=5), FakeContext(service)))  # no raise


# --- /analyze: URL, paste prompt, WallError degradation -----------------------


def test_analyze_with_url_replies_breakdown(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    )
    message = FakeMessage()
    asyncio.run(
        bot.analyze_command(
            FakeUpdate(message=message), FakeContext(service, args=[OFFER_URL])
        )
    )
    assert llm.calls == 1
    assert "puntuación" in message.replies[0]
    assert message.markups[0] is not None  # Generate-CV keyboard attached


def test_analyze_without_args_prompts_for_paste(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage()
    context = FakeContext(service, args=[])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    assert context.user_data[bot._PASTE_KEY] is True
    assert "pega" in message.replies[0].lower()


def test_walled_url_degrades_to_paste_prompt(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, SHELL_BODY, OFFER_URL))
    )
    message = FakeMessage()
    context = FakeContext(service, args=[OFFER_URL])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    assert "pega" in message.replies[0].lower()  # prompt, never a traceback
    assert context.user_data[bot._PASTE_KEY] is True


def test_unparseable_page_degrades_to_paste_prompt(tmp_path: Path) -> None:
    # An anti-bot challenge served as a 403 is not a classified wall; it still
    # must end in the paste prompt, never an unhandled OfferParseError.
    service, _llm = make_service(
        tmp_path,
        FakeFetcher(
            FetchResponse(403, "<html><body>Just a moment</body></html>", OFFER_URL)
        ),
    )
    message = FakeMessage()
    context = FakeContext(service, args=[OFFER_URL])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    assert "pega" in message.replies[0].lower()
    assert context.user_data[bot._PASTE_KEY] is True


def test_next_plain_message_is_analyzed_as_paste(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    context = FakeContext(service)
    context.user_data[bot._PASTE_KEY] = True
    message = FakeMessage(text=PASTE_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    assert llm.calls == 1
    assert "puntuación" in message.replies[0]
    assert (
        bot._PASTE_KEY not in context.user_data
        or context.user_data[bot._PASTE_KEY] is False
    )


def test_plain_message_without_paste_state_is_ignored(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage(text="hola")
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), FakeContext(service)))
    assert message.replies == []
    assert llm.calls == 0


# --- Callback router -----------------------------------------------------------


def test_analyze_callback_from_notification(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    )
    query = FakeQuery("an:4459804598")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.answered
    assert "puntuación" in query.message.replies[0]
    assert llm.calls == 1


def test_walled_analyze_callback_asks_for_paste(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, SHELL_BODY, OFFER_URL))
    )
    context = FakeContext(service)
    query = FakeQuery("an:4459804598")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), context))
    assert "pega" in query.message.replies[0].lower()
    assert context.user_data[bot._PASTE_KEY] is True


def test_lang_toggle_edits_keyboard_statelessly(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:1:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    payloads = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row  # type: ignore[attr-defined]
    ]
    assert "cv:1:AI:en" in payloads
    assert "lang:1:AI:en" in payloads


def test_lang_toggle_cycles_ai_to_catalan(tmp_path: Path) -> None:
    """v3 part B: AI ES→EN→CA→ES cycle (AI_CA master exists)."""
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:1:AI:en")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    payloads = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row  # type: ignore[attr-defined]
    ]
    assert "cv:1:AI:ca" in payloads
    assert "lang:1:AI:ca" in payloads


def test_lang_toggle_wraps_catalan_back_to_spanish(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:1:AI:ca")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    payloads = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row  # type: ignore[attr-defined]
    ]
    assert "cv:1:AI:es" in payloads


def test_lang_toggle_legacy_ds_card_follows_ai_cycle(tmp_path: Path) -> None:
    """Cards sent before DS was removed: DS callbacks are mapped to AI."""
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:1:DS:en")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    payloads = [
        b.callback_data
        for row in query.edited_markups[-1].inline_keyboard  # type: ignore[attr-defined]
        for b in row
    ]
    assert "cv:1:AI:ca" in payloads
    assert not any(":DS:" in p for p in payloads)


def test_variant_toggle_flips_role(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("variant:1:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    markup = query.edited_markups[-1]
    payloads = [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row  # type: ignore[attr-defined]
    ]
    assert "cv:1:BACKEND:es" in payloads


def _payloads_of(markup: object) -> list[str]:
    return [
        b.callback_data
        for row in markup.inline_keyboard  # type: ignore[attr-defined]
        for b in row
    ]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("variant:1:AI:es", "cv:1:BACKEND:es"),
        ("variant:1:AI:ca", "cv:1:BACKEND:es"),
        ("variant:1:BACKEND:en", "cv:1:AI:en"),
        ("variant:1:DS:es", "cv:1:BACKEND:es"),  # legacy card: DS -> AI -> Backend
    ],
)
def test_variant_toggle_cycles_ai_and_backend(
    tmp_path: Path, data: str, expected: str
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery(data)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert expected in _payloads_of(query.edited_markups[-1])


def test_lang_toggle_backend_never_offers_catalan(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    query = FakeQuery("lang:1:BACKEND:en")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    payloads = _payloads_of(query.edited_markups[-1])
    assert "cv:1:BACKEND:es" in payloads
    assert "cv:1:BACKEND:ca" not in payloads


def test_cv_callback_backend_role_generates_backend_cv(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"cv:{offer_id}:BACKEND:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert service.offer(offer_id).cv_base == "BACKEND_ES"


def test_cv_command_accepts_backend_role(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    message = FakeMessage("/cv")
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message),
            FakeContext(service, args=[str(offer_id), "backend", "en"]),
        )
    )
    assert service.offer(offer_id).cv_base == "BACKEND_EN"


def test_cv_command_without_role_follows_the_offer_profile(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    conn = service._repo._conn  # type: ignore[attr-defined]
    conn.execute("UPDATE offers SET perfil = 'backend' WHERE id = ?", (offer_id,))
    conn.commit()
    message = FakeMessage("/cv")
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message), FakeContext(service, args=[str(offer_id)])
        )
    )
    assert service.offer(offer_id).cv_base.startswith("BACKEND_")  # type: ignore[union-attr]


def test_cv_command_usage_lists_backend_and_rejects_unknown_role(
    tmp_path: Path,
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage("/cv")
    asyncio.run(
        bot.cv_command(FakeUpdate(message=message), FakeContext(service, args=[]))
    )
    assert "BACKEND" in message.replies[0]
    message2 = FakeMessage("/cv")
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message2), FakeContext(service, args=["1", "ZZ"])
        )
    )
    assert "BACKEND" in message2.replies[0]


def test_discard_callback_sets_descartada(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"discard:{offer_id}")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert "descartada" in query.edited[0]
    assert service.offer(offer_id).estado == "descartada"


def test_cv_callback_delivers_pdf_and_sent_keyboard(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"cv:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.message.documents and query.message.documents[0].endswith(".pdf")
    assert query.edited_markups[-1] is not None  # [Marcar enviada] keyboard
    regen = [
        b.callback_data
        for row in query.edited_markups[-1].inline_keyboard
        for b in row
        if "Rehacer" in b.text
    ]
    assert regen == [f"cv:{offer_id}:AI:es"]  # re-run the delivered variant


def test_sent_callback_marks_enviada(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    query = FakeQuery(f"sent:{offer_id}")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert "enviada" in query.edited[0]
    assert service.offer(offer_id).estado == "enviada"


def test_disc_callback_discards_and_clears_keyboard(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"disc:{offer_id}")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.edited == ["🗑 Oferta descartada — no se registra envío."]
    assert query.edited_markups == [None]  # keyboard removed
    assert service.offer(offer_id).estado == "descartada"
    assert query.message.replies == []  # success path never refuses


def test_disc_callback_idempotent_on_descartada(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    service.discard(offer_id)
    query = FakeQuery(f"disc:{offer_id}")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.edited == ["🗑 Oferta descartada — no se registra envío."]
    assert service.offer(offer_id).estado == "descartada"
    assert query.message.replies == []  # idempotent: no refusal, no error


def test_disc_callback_refused_after_sent(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    service.mark_sent(offer_id)
    query = FakeQuery(f"disc:{offer_id}")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert query.message.replies == ["⚠️ Ya está marcada como enviada."]
    assert query.edited == []  # card untouched
    assert service.offer(offer_id).estado == "enviada"


# --- Thin commands --------------------------------------------------------------


def test_cv_callback_legacy_ds_generates_the_ai_cv(tmp_path: Path) -> None:
    """A `cv:<id>:DS:<lang>` button from an old card must not crash: DS -> AI."""
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"cv:{offer_id}:DS:en")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert service.offer(offer_id).cv_base == "AI_EN"
    assert query.message.documents


def test_cv_command_rejects_ds_with_usage(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage("/cv")
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message), FakeContext(service, args=["1", "DS", "en"])
        )
    )
    assert message.replies == ["Uso: /cv <id> [AI|BACKEND] [es|en|ca]"]


def test_cv_command_generates_document(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    message = FakeMessage()
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message),
            FakeContext(service, args=[str(offer_id), "AI", "en"]),
        )
    )
    assert message.documents == ["Alex_Rivera_AI_Engineer.pdf"]
    assert service.offer(offer_id).cv_base == "AI_EN"


def test_sent_and_export_and_keywords_commands(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    asyncio.run(service.analyze(offer_id))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    message = FakeMessage()
    asyncio.run(
        bot.sent_command(
            FakeUpdate(message=message), FakeContext(service, args=[str(offer_id)])
        )
    )
    assert "enviada" in message.replies[0]
    message = FakeMessage()
    asyncio.run(bot.export_command(FakeUpdate(message=message), FakeContext(service)))
    assert ".xlsx" in message.replies[0]
    message = FakeMessage()
    asyncio.run(bot.keywords_command(FakeUpdate(message=message), FakeContext(service)))
    assert "Python" in message.replies[0]


def test_status_command_lists_recent(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    message = FakeMessage()
    asyncio.run(bot.status_command(FakeUpdate(message=message), FakeContext(service)))
    assert f"#{offer_id}" in message.replies[0]


# --- /beacons + /beacon resume|disable (8b; real surface replaces 8a stub) -----


def test_beacons_command_lists_rows_with_status(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    beacon_id = service.create_beacon("ia-espana", SEARCH_URL)
    message = FakeMessage()
    asyncio.run(bot.beacons_command(FakeUpdate(message=message), FakeContext(service)))
    assert "ia-espana" in message.replies[0]
    assert f"#{beacon_id}" in message.replies[0]
    assert "active" in message.replies[0]


def test_beacons_command_empty_state_answers(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage()
    asyncio.run(bot.beacons_command(FakeUpdate(message=message), FakeContext(service)))
    assert message.replies  # empty state still answers, no traceback


def test_beacon_command_resume_and_disable_wire_to_service(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    beacon_id = service.create_beacon("ia-espana", SEARCH_URL)
    repo = service._repo
    message = FakeMessage()
    asyncio.run(
        bot.beacon_command(
            FakeUpdate(message=message),
            FakeContext(service, args=["disable", str(beacon_id)]),
        )
    )
    assert repo.get_beacon(beacon_id).enabled is False
    message = FakeMessage()
    asyncio.run(
        bot.beacon_command(
            FakeUpdate(message=message),
            FakeContext(service, args=["resume", str(beacon_id)]),
        )
    )
    row = repo.get_beacon(beacon_id)
    assert row.enabled is True  # resume is the reactivation path (also from disable)
    assert row.status == "active"
    assert row.consecutive_walls == 0


def test_beacon_command_bad_usage_replies_usage_text(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage()
    asyncio.run(
        bot.beacon_command(
            FakeUpdate(message=message), FakeContext(service, args=["pause", "1"])
        )
    )
    assert "Uso" in message.replies[0]


def test_beacon_callbacks_resume_and_disable_pause_alert(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    beacon_id = service.create_beacon("ia-espana", SEARCH_URL)
    # Simulate a paused beacon (3 walls) via the repository state.
    repo = service._repo
    repo.record_beacon_check(
        beacon_id,
        checked_at="2026-09-02T12:00:00+00:00",
        next_check_after="2026-09-03T12:00:00+00:00",
        status="paused",
        consecutive_walls=3,
        last_wall_kind="js_shell",
        checks_today=1,
        checks_date="2026-09-02",
    )
    query = FakeQuery("beacon_resume:1")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    row = repo.get_beacon(beacon_id)
    assert row.status == "active" and row.consecutive_walls == 0
    assert "reactivado" in query.edited[0]

    repo.record_beacon_check(
        beacon_id,
        checked_at="2026-09-02T12:00:00+00:00",
        next_check_after="2026-09-03T12:00:00+00:00",
        status="paused",
        consecutive_walls=3,
        last_wall_kind="js_shell",
        checks_today=1,
        checks_date="2026-09-02",
    )
    query = FakeQuery("beacon_disable:1")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert repo.get_beacon(beacon_id).enabled is False
    assert "desactivado" in query.edited[0]


# --- In-chat error replies + re-armed paste state (smoke-test bugfix) ---------


def test_paste_llm_failure_replies_spanish_and_rearms(tmp_path: Path) -> None:
    service = make_service_with_llm(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL)), FailingLlmClient()
    )
    context = FakeContext(service)
    context.user_data[bot._PASTE_KEY] = True
    message = FakeMessage(text=PASTE_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    assert len(message.replies) == 1
    assert "El análisis falló" in message.replies[0]
    assert "HTTP 401" in message.replies[0]
    assert "CVA_LLM_" in message.replies[0]
    assert "pega el texto de nuevo" in message.replies[0]
    # State re-armed: the owner's SECOND paste must trigger a new attempt.
    message2 = FakeMessage(text=PASTE_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message2), context))
    assert len(message2.replies) == 1
    assert "El análisis falló" in message2.replies[0]


def test_link_analyze_llm_failure_replies_and_rearms(tmp_path: Path) -> None:
    llm = FailingLlmClient("LLM endpoint returned HTTP 401")
    service = make_service_with_llm(
        tmp_path, FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)), llm
    )
    message = FakeMessage()
    context = FakeContext(service, args=[OFFER_URL])
    asyncio.run(bot.analyze_command(FakeUpdate(message=message), context))
    assert any("El análisis falló" in r for r in message.replies)
    assert any("pega el texto de nuevo" in r for r in message.replies)
    assert context.user_data[bot._PASTE_KEY] is True


def test_non_llm_analyze_failure_names_the_cause(tmp_path: Path) -> None:
    llm = FailingLlmClient("completion does not satisfy OfferExtraction")
    service = make_service_with_llm(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL)), llm
    )
    context = FakeContext(service)
    context.user_data[bot._PASTE_KEY] = True
    message = FakeMessage(text=PASTE_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    assert len(message.replies) == 1
    assert "completion does not satisfy OfferExtraction" in message.replies[0]
    assert context.user_data[bot._PASTE_KEY] is True


def test_paste_success_path_stays_unchanged(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    context = FakeContext(service)
    context.user_data[bot._PASTE_KEY] = True
    message = FakeMessage(text=PASTE_TEXT)
    asyncio.run(bot.paste_handler(FakeUpdate(message=message), context))
    assert llm.calls == 1
    assert "puntuación" in message.replies[0]
    assert message.markups[0] is not None
    assert context.user_data[bot._PASTE_KEY] is False


# --- PTB application error handler backstop -----------------------------------


def test_error_handler_notifies_owner_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    bot_instance = FakeBot()
    context = FakeContext(
        service,
        tg_bot=bot_instance,
        error=LlmResponseError("LLM endpoint returned HTTP 401"),
    )
    with caplog.at_level(logging.ERROR, logger="bot"):
        asyncio.run(bot.on_error(FakeUpdate(), context))
    assert len(bot_instance.sent) == 1
    owner_id, text = bot_instance.sent[0]
    assert owner_id == 5
    assert "HTTP 401" in text
    assert any("HTTP 401" in record.getMessage() for record in caplog.records)


def test_error_handler_swallows_notification_failures(
    tmp_path: Path,
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    context = FakeContext(
        service, tg_bot=FakeBot(fail=True), error=RuntimeError("boom")
    )
    asyncio.run(bot.on_error(FakeUpdate(), context))  # must not raise


def test_build_app_registers_error_handler(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    settings = clean_settings()
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    app = bot.build_app(settings, service)
    assert bot.on_error in app.error_handlers


def test_configure_logging_silences_httpx_request_lines() -> None:
    """httpx logs every Telegram request URL at INFO, and that URL embeds the
    bot token (``/bot<TOKEN>/getUpdates``): the log must never carry it, and the
    long-polling lines would otherwise grow data/bot.log without bound."""
    root = logging.getLogger()
    saved_root = (root.level, list(root.handlers))
    noisy = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    saved_noisy = [lg.level for lg in noisy]
    try:
        # Fresh-process state: basicConfig is a no-op when handlers already
        # exist (pytest installs its own), so start from a bare root logger.
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        bot.configure_logging()
        assert root.getEffectiveLevel() == logging.INFO
        for lg in noisy:
            assert not lg.isEnabledFor(logging.INFO), lg.name
            assert lg.isEnabledFor(logging.WARNING), lg.name
    finally:
        root.setLevel(saved_root[0])
        root.handlers[:] = saved_root[1]
        for lg, level in zip(noisy, saved_noisy):
            lg.setLevel(level)


# --- apply-instruction warnings on cards ----------------------------------------

_EMAIL_APPLY_TEXT = PASTE_TEXT + " Si te encaja, remite tu CV a talent@acme-data.example."


def test_cv_callback_warns_about_extra_application_steps(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, _EMAIL_APPLY_TEXT))
    query = FakeQuery(f"cv:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert "⚠️ Ojo: esta oferta pide algo más al aplicar" in query.edited[0]
    assert "talent@acme-data.example" in query.edited[0]


def test_cv_command_and_cv_callback_plain_offer_has_no_warning(
    tmp_path: Path,
) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    query = FakeQuery(f"cv:{offer_id}:AI:es")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert "Ojo" not in query.edited[0]


def test_cv_command_warns_about_extra_application_steps(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, _EMAIL_APPLY_TEXT))
    message = FakeMessage()
    asyncio.run(
        bot.cv_command(
            FakeUpdate(message=message), FakeContext(service, args=[str(offer_id)])
        )
    )
    assert "talent@acme-data.example" in message.replies[-1]


def test_analyze_reply_warns_about_extra_application_steps(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, _EMAIL_APPLY_TEXT))
    message = FakeMessage()
    asyncio.run(
        bot._analyze_and_reply(service, message, FakeContext(service), offer_id)
    )
    assert "talent@acme-data.example" in message.replies[-1]


def test_callback_router_stops_tracking_the_card_it_rewrites(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    service._repo.save_card(offer_id, 5, 11, "card", None, None)
    query = FakeQuery("noop")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    assert service._repo.get_card(offer_id) is None


def test_status_command_for_one_offer_shows_also_on(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    offer_id = asyncio.run(service.ingest(None, PASTE_TEXT))
    dup = service._repo.upsert_offer(
        OfferInput(
            title="X",
            company="Y",
            url="https://www.infojobs.net/a/b/of-0f8d7e6c5b4a39281706f5e4d3c2b1a0",
            text="x",
            source="link",
        )
    )
    service._repo.append_note(dup, f"duplicada de #{offer_id}")
    message = FakeMessage()
    context = FakeContext(service, args=[str(offer_id)])
    asyncio.run(bot.status_command(FakeUpdate(message=message), context))
    assert "También en: InfoJobs" in message.replies[0]


# --- /pausa and /reanudar (safe-defaults S2): persistent kill switch -----------


def test_pausa_and_reanudar_toggle_the_persistent_pause(tmp_path: Path) -> None:
    service, _llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, "", OFFER_URL))
    )
    message = FakeMessage()
    asyncio.run(bot.pausa_command(FakeUpdate(message=message), FakeContext(service)))
    assert service.is_paused() is True
    assert "pausa" in message.replies[0].lower()
    message = FakeMessage()
    asyncio.run(bot.reanudar_command(FakeUpdate(message=message), FakeContext(service)))
    assert service.is_paused() is False
    assert "reanud" in message.replies[0].lower()


def test_analyze_still_works_while_paused(tmp_path: Path) -> None:
    service, llm = make_service(
        tmp_path, FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL))
    )
    service.set_paused(True)
    message = FakeMessage()
    asyncio.run(
        bot.analyze_command(
            FakeUpdate(message=message), FakeContext(service, args=[OFFER_URL])
        )
    )
    assert llm.calls == 1
    assert "puntuación" in message.replies[0]
