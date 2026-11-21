"""bot-i18n T3: ``ui_language`` reaches every user-facing output.

The bot, the service and the bridge are built with ``ui_language="en"`` and
driven through their public entry points (handlers, callbacks, the service
seam, the bridge endpoint); every reply must be the English catalog text.
Spanish (``es``) stays byte-identical: the rest of the suite runs with
``CVA_UI_LANGUAGE=es`` and pins today's texts.
"""

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

import bot
from cva import bridge
from cva.calibration import SentJudgment
from cva.calibration_ui import list_message as calibration_list_message
from cva.calibration_ui import reason_message as calibration_reason_message
from cva.claimsdb import load_claims
from cva.commands import help_text
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY
from cva.gmail.client import GmailAuthError
from cva.gmail.sync import auth_warning
from cva.i18n import t
from cva.match.apply_instructions import detect_apply_instructions
from cva.match.cover_letter import validate_text
from cva.match.extraction import ExtractionCache
from cva.match.salary_reference import salary_line
from cva.match.scorer import load_synonyms
from cva.notifier import OE, outcome_note_prompt
from cva.service import JobService, ServiceError
from cva.star import ST, question_message
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_MASTERS
from tests.helpers import run_coro
from tests.test_bot import (
    CLAIMS_PATH,
    JSONLD_BODY,
    OFFER_URL,
    SYNONYMS_PATH,
    FakeContext,
    FakeFetcher,
    FakeLlmClient,
    FakeMessage,
    FakeQuery,
    FakeUpdate,
    FetchResponse,
)


def _service(tmp_path: Path, body: str = JSONLD_BODY) -> JobService:
    conn = sqlite3.connect(tmp_path / "app.db")
    db = load_claims(CLAIMS_PATH)
    return JobService(
        repo=OfferRepository(conn),
        llm=FakeLlmClient(),
        fetcher=FakeFetcher(FetchResponse(200, body, OFFER_URL)),
        db=db,
        syn=load_synonyms(SYNONYMS_PATH, db),
        cache=ExtractionCache(conn),
        threshold=65,
        masters_dir=DEMO_MASTERS,
        ui_language="en",
    )


def _context(service: JobService, args: list[str] | None = None) -> FakeContext:
    """A context as ``build_app`` wires it for ``ui_language = "en"``."""
    context = FakeContext(service, args=args)
    context.bot_data[bot._LANG_KEY] = "en"
    return context


def _command(
    handler: Callable[..., Any], context: FakeContext, text: str | None = None
) -> FakeMessage:
    message = FakeMessage(text)
    run_coro(handler(FakeUpdate(message=message), context))
    return message


def _press(context: FakeContext, data: str) -> FakeQuery:
    query = FakeQuery(data)
    run_coro(bot.callback_router(FakeUpdate(query=query), context))
    return query


def _labels(markup: Any) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _settings(monkeypatch: pytest.MonkeyPatch, **values: Any) -> Settings:
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "123456:TEST")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-test")
    return Settings(_env_file=None, ui_language="en", **values)  # type: ignore[call-arg]


def _sent_offer(service: JobService) -> int:
    repo = service.repository
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id="1",
            title="T",
            company="C",
            url="https://x/1",
            text="t",
            source="link",
        )
    )
    repo.mark_sent(
        offer_id,
        cv_path="cv.pdf",
        cambios="c",
        match=52,
        judgment=SentJudgment(score=52),
    )
    return offer_id


# --- settings → app/service wiring ----------------------------------------------


def test_build_app_and_service_carry_the_ui_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(monkeypatch)
    service = JobService.from_settings(settings, db_path=tmp_path / "app.db")
    app = bot.build_app(settings, service)
    assert service.ui_language == "en"
    assert app.bot_data[bot._LANG_KEY] == "en"


# --- chat flows ---------------------------------------------------------------------


def test_analyze_card_and_its_buttons_are_english(tmp_path: Path) -> None:
    message = _command(bot.analyze_command, _context(_service(tmp_path), [OFFER_URL]))
    card = message.replies[0]
    assert "score" in card and "puntuación" not in card
    labels = _labels(message.markups[0])
    assert t("commands.button.discard.label", "en") in labels
    assert t("commands.button.discard.label", "es") not in labels


def test_status_help_and_usage_lines_are_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert _command(bot.status_command, _context(service)).replies == [
        "No offers recorded yet."
    ]
    assert _command(bot.start_help, _context(service)).replies == [
        help_text(BUILTIN_REGISTRY, lang="en")
    ]
    usage = _command(bot.cv_command, _context(service)).replies[0]
    assert usage.startswith("Usage: /cv ")
    assert _command(bot.rescatar_command, _context(service)).replies == [
        "Usage: /rescue <id>"
    ]


def test_service_errors_reach_the_chat_in_english(tmp_path: Path) -> None:
    replies = _command(bot.rescatar_command, _context(_service(tmp_path), ["99"]))
    assert replies.replies == ["⚠️ Offer #99 does not exist."]


def test_service_error_keeps_its_spanish_str_and_renders_english() -> None:
    error = ServiceError.keyed("bot.err.no_offer", id=7)
    assert isinstance(error, ValueError)
    assert str(error) == "No existe la oferta #7."
    assert error.text("en") == "Offer #7 does not exist."
    assert error.text("es") == "No existe la oferta #7."
    plain = ServiceError("developer message")
    assert plain.text("en") == "developer message"


def test_panel_is_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    html = service.panel_html([], [])
    assert '<html lang="en">' in html


def test_star_question_is_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(service)
    candidate = service.star_candidates()[0]
    query = _press(context, f"{ST}:new:{candidate.key}")
    assert query.message.replies[-1] == question_message(1, 5, "situation", "en")


def test_outcome_calibration_question_and_note_prompt_are_english(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    offer_id = _sent_offer(service)
    query = _press(_context(service), f"{OE}:{offer_id}:rechazada")
    replies = query.message.replies
    assert replies[0] == f"✅ #{offer_id} → rejected."
    assert calibration_reason_message("en") in replies
    assert replies[-1] == outcome_note_prompt("en")


def test_calibration_list_is_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    message = _command(bot.calibracion_command, _context(service))
    assert message.replies == [
        calibration_list_message([], service.calibration_summary(), "en")
    ]


def test_pause_and_keyword_replies_are_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert _command(bot.keywords_command, _context(service)).replies == [
        "No keywords recorded yet."
    ]
    assert _command(bot.reanudar_command, _context(service)).replies == [
        "▶️ Resumed: everything works again."
    ]


# --- service texts -----------------------------------------------------------------


def test_card_salary_line_and_apply_labels_are_english() -> None:
    text = "Salary: 30.000 - 40.000 € per year. Please include a cover letter."
    line = salary_line(title="AI Engineer", text=text, perfil=None, table=[], lang="en")
    assert line.startswith("💶 Published salary: 30.000–40.000 € gross/year")
    labels = [item.label for item in detect_apply_instructions(text, "en")]
    assert "They ask for a cover letter" in labels


def test_validator_warnings_follow_the_requested_language() -> None:
    kwargs: dict[str, Any] = {
        "hard_min": 100,
        "hard_max": 200,
        "min_words": 120,
        "max_words": 180,
    }
    english = validate_text("Short text.", "en", lang="en", **kwargs)
    spanish = validate_text("Short text.", "en", **kwargs)
    assert english[0] == "has 2 words (must have between 120 and 180)"
    assert spanish[0] == "tiene 2 palabras (debe tener entre 120 y 180)"


# --- bridge beacon alert ------------------------------------------------------------


def test_bridge_register_passes_the_ui_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge._Bridge, "start", lambda self: None)
    monkeypatch.setattr(bridge.atexit, "register", lambda fn: None)
    monkeypatch.setenv("CVA_BRIDGE_SECRET", "s")
    settings = _settings(monkeypatch, owner_telegram_id=5)
    app = type("App", (), {"bot_data": {}, "post_init": None, "post_shutdown": None})()
    made = bridge.register(app, settings, _service(tmp_path))
    assert made is not None and made._lang == "en"


def test_beacon_health_alert_is_english(tmp_path: Path) -> None:
    repo = OfferRepository(sqlite3.connect(tmp_path / "h.db", check_same_thread=False))
    sent: list[str] = []
    handler = bridge._make_handler(
        secret="s",
        repo=repo,
        sender=lambda message, keyboard: sent.append(message),
        threshold=65,
        lock=threading.Lock(),
        lang="en",
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(
        target=httpd.serve_forever,
        kwargs={"poll_interval": bridge.SERVE_POLL_INTERVAL},
        daemon=True,
    ).start()
    try:
        httpx.post(
            f"http://127.0.0.1:{httpd.server_address[1]}/scan-report",
            headers={"X-CVA-Bridge-Secret": "s"},
            json={
                "beacon_id": "b1",
                "beacon_name": "Barcelona AI",
                "at": datetime.now(tz=UTC).isoformat(),
                "status": "ok",
                "jobs_found": 6,
                "empty_company": 5,
            },
        )
        for _ in range(60):  # the alert is sent after the 200 (best effort)
            if sent:
                break
            threading.Event().wait(0.05)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert sent and sent[0].startswith("⚠️ The beacon «Barcelona AI»")


# --- gmail ----------------------------------------------------------------------------


class _AuthFailingClient:
    def list_messages(self, *args: Any, **kwargs: Any) -> Any:
        raise GmailAuthError("expired")

    def __getattr__(self, name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise GmailAuthError("expired")

        return fail


def test_gmail_sync_and_client_get_the_ui_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(monkeypatch, gmail_enabled=True)
    sync = bot.build_gmail_sync(settings, _service(tmp_path))
    assert sync is not None
    assert sync._lang == "en" and sync._client._lang == "en"  # type: ignore[attr-defined]
    sync._client = _AuthFailingClient()  # type: ignore[assignment]
    warnings: list[str] = []

    async def proposal(_: Any) -> None:
        return None

    async def warning(text: str) -> None:
        warnings.append(text)

    run_coro(sync.run(proposal, warning))
    assert warnings == [auth_warning("en")]


def test_cover_letter_warnings_are_english_but_the_retry_prompt_stays_spanish() -> None:
    from cva.match.cover_letter import LetterParts, generate_cover_letter
    from cva.profile_persona import load_letter_bases
    from tests.demo import DEMO_DIR

    base = load_letter_bases(DEMO_DIR / "cover_letter_base.toml")["es"]
    parts = LetterParts(
        tone="cercano", linking="Una. Dos.", why_company="x", why_role="y"
    )
    users: list[str] = []

    class _Llm:
        async def json_completion(
            self, system: str, user: str, schema: Any, *, model: str | None = None
        ) -> Any:
            users.append(user)
            return parts

    result = asyncio.run(
        generate_cover_letter(
            _Llm(),
            offer_text="Oferta",
            title="T",
            company="Acme",
            language="es",
            branch="ai",
            claims_digest="c",
            apply_findings=[],
            voice_sample="v",
            base=base,
            ui_lang="en",
        )
    )
    assert "the linking sentence must be a single sentence" in result.warnings
    assert "- la frase de enlace debe ser una sola frase" in users[-1]


# --- T3 gaps: heartbeat, STAR list, guard line, stack wall, salary city -----------


def test_heartbeat_new_job_card_is_english(tmp_path: Path) -> None:
    from cva.scheduler import heartbeat
    from tests.test_scheduler import (
        HEALTHY,
        NOW,
        SEARCH_URL,
        FakeBot,
        RecordingFetcher,
        fake_context,
        make_service,
    )

    service = make_service(
        sqlite3.connect(tmp_path / "hb.db"),
        RecordingFetcher(HEALTHY),
        lambda: NOW,
        ui_language="en",
    )
    service.create_beacon("ai", SEARCH_URL)
    tg = FakeBot()
    asyncio.run(heartbeat(fake_context(service, 5, tg)))
    _, text, markup = tg.sent[0]
    assert "Top-Choice threshold: 65" in text
    assert t("commands.button.analyze.label", "en") in _labels(markup)


def test_star_list_and_its_keyboards_are_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    context = _context(service)
    first = _command(bot.star_command, context)
    assert first.replies[0].startswith("⭐ You have no STAR stories yet.")
    assert all(label.startswith("➕ ") for label in _labels(first.markups[0]))
    candidate = service.star_candidates()[0]
    intro = _press(context, f"{ST}:new:{candidate.key}").message.replies[0]
    assert "I will ask you 5 questions" in intro
    listing = _command(bot.star_command, context)
    assert listing.replies[0].startswith("⭐ Your STAR stories:")
    assert "✅ complete · ✏️ parts missing" in listing.replies[0]
    assert "➕ Add another" in _labels(listing.markups[0])
    story = service.star_stories()[0]
    view = _press(context, f"{ST}:v:{story.id}")
    assert "⬅️ Back" in _labels(view.message.markups[-1])
    delete = _press(context, f"{ST}:d:{story.id}")
    assert _labels(delete.message.markups[-1]) == ["🗑 Yes, delete", "Cancel"]


def test_guard_warning_line_is_english() -> None:
    from cva.ingest.posting_guard import GuardReport, warning_line

    report = GuardReport(text="", removed=3, hidden_blocks=1, tampering=True)
    assert warning_line(report, "en") == (
        "⚠️ Hidden text in the offer: 3 invisible characters, 1 hidden block (removed)"
    )


def test_stack_wall_reason_is_stored_spanish_and_shown_english() -> None:
    from cva.match.scorer import MatchResult
    from cva.service import display_reason, display_result

    stored = "stack principal Java, no Python"
    assert display_reason(stored, "en") == "core stack Java, not Python"
    assert display_reason(stored, "es") == stored
    result = MatchResult.model_construct(stack_wall=stored, gates=[])
    assert display_result(result, "en").stack_wall == "core stack Java, not Python"


def test_salary_research_city_label_is_english(tmp_path: Path) -> None:
    service = _service(tmp_path)
    offer_id = service.repository.upsert_offer(
        OfferInput(
            job_id="2",
            title="AI Engineer",
            company="C",
            url="https://x/2",
            text="Remote job in Spain.",
            source="link",
        )
    )
    outcome = asyncio.run(service.research_salary(offer_id))
    assert outcome.city == "Spain (remote)"
