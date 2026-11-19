"""Telegram bot entrypoint (design D12, tasks 8.5 + 8b.5): PTB long polling.

Thin shell over the service seam; run_polling only. Single-owner whitelist
(unset owner 0 refuses everyone); WallError → paste prompt (D3); heartbeat =
cva.scheduler (one JobQueue job / 10 min, first tick = startup catch-up).
User-facing strings follow ``Settings.ui_language`` (English or Spanish; the
catalog is ``cva/i18n/bot.py``); code and comments are English.
"""

import asyncio
import io
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from cva import bridge, scheduler
from cva.calibration import (
    FIT_REASONS,
    REASON_OTHER,
    CalibrationExample,
)
from cva.calibration_ui import (
    CAL,
)
from cva.calibration_ui import (
    list_keyboard as calibration_list_keyboard,
)
from cva.calibration_ui import (
    list_message as calibration_list_message,
)
from cva.calibration_ui import (
    question_keyboard as calibration_question_keyboard,
)
from cva.calibration_ui import (
    question_message as calibration_question_message,
)
from cva.calibration_ui import (
    reason_keyboard as calibration_reason_keyboard,
)
from cva.calibration_ui import (
    reason_message as calibration_reason_message,
)
from cva.commands import COMMANDS, cv_usage, help_text, menu_commands
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.gmail.client import GoogleGmailClient
from cva.gmail.sync import GmailSync, should_propose
from cva.i18n import CATALOGS, LEGACY_LANG, Lang, t
from cva.ingest import NotFoundError, OfferParseError
from cva.llm import build_llm_client, describe_llm_setup
from cva.match import infer_language
from cva.match.apply_instructions import ApplyInstruction, detect_apply_instructions
from cva.match.cover_letter import assemble_open_letter
from cva.match.interview_prep import (
    PREP_ESTADOS,
    STAGE_LABEL,
    STAGES,
    default_stage,
)
from cva.notifier import (
    BEACON_DISABLE,
    BEACON_RESUME,
    CL,
    CLC,
    FU,
    GM,
    OE,
    OUT,
    PREP,
    SAL,
    SentMessage,
    analyze_failure_message,
    asks_cover_letter,
    backstop_error_message,
    beacon_callback_reply,
    beacon_command_reply,
    beacons_list_message,
    breakdown_message,
    chunk_message,
    cover_letter_keyboard,
    cover_letter_message,
    cover_letter_sources_message,
    cv_keyboard,
    cv_ready_message,
    discard_message,
    discard_no_send_message,
    failure_reason,
    next_lang,
    next_role,
    no_cv_reply,
    normalize_role,
    not_found_message,
    offer_closed_message,
    offer_status_line,
    outcome_keyboard,
    outcome_message,
    outcome_note_prompt,
    paste_prompt,
    prep_keyboard,
    prep_stage_keyboard,
    salary_research_message,
    sent_keyboard,
    sent_message,
    sent_outcome_keyboard,
    unknown_role_message,
    with_no_cv_line,
)
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import LETTER_LANGUAGES, ProfileError, load_letter_bases
from cva.report.panel import PANEL_FILENAME
from cva.service import JobService, OfferClosedError, ServiceError, display_result
from cva.star import ST, star_fields
from cva.star import candidates_keyboard as star_candidates_keyboard
from cva.star import candidates_message as star_candidates_message
from cva.star import delete_keyboard as star_delete_keyboard
from cva.star import edit_keyboard as star_edit_keyboard
from cva.star import field_label as star_field_label
from cva.star import intro_message as star_intro_message
from cva.star import list_keyboard as star_list_keyboard
from cva.star import list_message as star_list_message
from cva.star import question_message as star_question_message
from cva.star import story_keyboard as star_story_keyboard
from cva.star import story_message as star_story_message
from cva.tracker import OUTCOME_ESTADOS, STAR_PLACEHOLDER, RepositoryError, StarStory
from cva.wall import WallError

_LOG = logging.getLogger(__name__)
_PASTE_KEY = "awaiting_paste"
_NOTE_KEY = "awaiting_outcome_note"
_NOTE_AT_KEY = "awaiting_outcome_note_at"
_NOTE_TTL_SECONDS = 30 * 60  # a forgotten note prompt must not capture later text
_STAR_KEY = "star_pending"  # {"id": story id, "queue": [fields still to ask]}
_STAR_AT_KEY = "star_pending_at"
_STAR_TTL_SECONDS = 30 * 60  # same expiry as the outcome note
_SERVICE_KEY = "service"
_OWNER_KEY = "owner_id"
# Path of the profile's cover_letter_base.toml (set by build_app from the same
# profile the service loads; unset -> the default profile resolution).
_LETTER_BASE_KEY = "letter_base"
_LANG_KEY = "ui_language"  # Settings.ui_language, set by build_app


def is_owner(user_id: int | None, owner_id: int) -> bool:
    """Single-user whitelist gate; an unset owner (``0``) refuses everyone."""
    return bool(user_id) and bool(owner_id) and user_id == owner_id


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1 guard: strangers are ignored with no reply and no side effects."""
    user = update.effective_user
    if not is_owner(user.id if user else None, int(context.bot_data[_OWNER_KEY])):
        raise ApplicationHandlerStop


async def start_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_text(_families(context), lang=_lang(context)))


def _service(context: ContextTypes.DEFAULT_TYPE) -> JobService:
    return context.bot_data[_SERVICE_KEY]


def _lang(context: ContextTypes.DEFAULT_TYPE | None) -> Lang:
    """The UI language ``build_app`` stored (``Settings.ui_language``); else
    the service's; ``LEGACY_LANG`` (Spanish) for contexts built without
    either (test doubles)."""
    if context is None:
        return LEGACY_LANG
    stored = context.bot_data.get(_LANG_KEY)
    if stored is not None:
        lang: Lang = stored
        return lang
    return _ui(context.bot_data.get(_SERVICE_KEY))


def _ui(service: object) -> Lang:
    """The service's UI language (``LEGACY_LANG`` for a test double without one)."""
    lang: Lang = getattr(service, "ui_language", LEGACY_LANG)
    return lang


def _err(exc: Exception, lang: Lang) -> str:
    """``⚠️`` + the error in ``lang`` (keyed service errors are translated)."""
    text = exc.text(lang) if isinstance(exc, ServiceError) else str(exc)
    return f"⚠️ {text}"


def _reason(exc: BaseException | None, lang: Lang) -> str:
    """:func:`failure_reason` in ``lang``; keyed service errors translated."""
    if isinstance(exc, ServiceError):
        return exc.text(lang)
    return failure_reason(exc, lang=lang)


def _estado(estado: str, lang: Lang) -> str:
    """A stored estado as the owner reads it (the raw value in Spanish)."""
    key = f"notifier.estado.{estado}"
    return t(key, lang) if key in CATALOGS[lang] else estado


def _families(context: ContextTypes.DEFAULT_TYPE | None) -> FamilyRegistry:
    """The profile's role families (role-families T5); the built-ins when no
    service is wired (e.g. /help before start-up)."""
    service = context.bot_data.get(_SERVICE_KEY) if context is not None else None
    prefs = getattr(service, "preferences", None)
    families = getattr(prefs, "families", None)
    return families if isinstance(families, FamilyRegistry) else BUILTIN_REGISTRY


def _apply_findings(service: JobService, offer_id: int) -> list[ApplyInstruction]:
    """Extra application steps in the offer text (pure; never blocks a card)."""
    return detect_apply_instructions(
        service.offer(offer_id).source_data or "", _ui(service)
    )


async def _analyze_and_reply(
    service: JobService, reply, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    """Analyze a persisted offer; ANY failure replies naming the cause and
    RE-ARMS the paste state so the owner can re-paste without /analyze."""
    ui = _lang(context)
    try:
        result = await service.analyze(offer_id)
        offer = service.offer(offer_id)
        lang = infer_language(offer.source_data)
        role = _families(context).role_for(offer.perfil)
    except OfferClosedError:
        await reply.reply_text(offer_closed_message(lang=ui))
        return
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        context.user_data[_PASTE_KEY] = True
        await reply.reply_text(analyze_failure_message(exc, lang=ui))
        return
    no_cv = service.no_cv_label(offer_id)
    cv_needed = no_cv is None
    card = breakdown_message(
        display_result(result, ui),
        offer_id,
        url=service.offer(offer_id).url_oferta,
        apply_findings=_apply_findings(service, offer_id),
        salary_line=service.card_extras(offer_id),
        lang=ui,
    )
    await reply.reply_text(
        card if no_cv is None else with_no_cv_line(card, no_cv, lang=ui),
        reply_markup=cv_keyboard(
            offer_id,
            role=role,
            lang=lang,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            cv_needed=cv_needed,
            families=_families(context),
            ui_lang=ui,
        ),
    )


async def _ingest_url(
    service: JobService, reply, context: ContextTypes.DEFAULT_TYPE, url: str
) -> int | None:
    """Ingest by URL; on a wall (D3) or an unreadable page set the paste flag and
    prompt. None on failure."""
    try:
        return await service.ingest(url, None)
    except (WallError, OfferParseError):
        context.user_data[_PASTE_KEY] = True
        await reply.reply_text(paste_prompt(lang=_lang(context)))
    except NotFoundError:
        await reply.reply_text(not_found_message(lang=_lang(context)))
    return None


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        context.user_data[_PASTE_KEY] = True  # next plain message = pasted offer
        await update.message.reply_text(paste_prompt(lang=_lang(context)))
        return
    service = _service(context)
    offer_id = await _ingest_url(service, update.message, context, args[0])
    if offer_id is not None:
        await _analyze_and_reply(service, update.message, context, offer_id)


async def paste_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get(_PASTE_KEY):
        note_offer = _take_pending_note(context)
        if note_offer is not None:
            await _save_outcome_note(update, context, int(note_offer))
        elif (pending := _live_star(context)) is not None:
            await _save_star_answer(update, context, pending)
        return  # not awaiting a paste: plain messages are ignored silently
    context.user_data[_PASTE_KEY] = False
    _take_pending_note(context)  # a pasted offer wins over a pending note
    _clear_star(context)
    service = _service(context)
    try:
        offer_id = await service.ingest(None, update.message.text or "")
    except ValueError:
        await update.message.reply_text(t("bot.empty_paste", _lang(context)))
        return
    await _analyze_and_reply(service, update.message, context, offer_id)


def _take_pending_note(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Pop the pending outcome-note offer id; None when absent or expired."""
    offer_id = context.user_data.pop(_NOTE_KEY, None)
    asked_at = context.user_data.pop(_NOTE_AT_KEY, None)
    if offer_id is None:
        return None
    if (
        not isinstance(asked_at, int | float)
        or time.time() - asked_at > _NOTE_TTL_SECONDS
    ):
        return None
    return int(offer_id)


async def _save_outcome_note(
    update: Update, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    """The free-text reply after an outcome button: stored as a timeline note."""
    try:
        _service(context).add_outcome_note(offer_id, update.message.text or "")
    except (ServiceError, RepositoryError) as exc:
        await update.message.reply_text(_err(exc, _lang(context)))
        return
    await update.message.reply_text(t("bot.note_saved", _lang(context), id=offer_id))


def _clear_star(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_STAR_KEY, None)
    context.user_data.pop(_STAR_AT_KEY, None)


def _live_star(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    """The pending STAR question, or None (expired state is dropped)."""
    pending = context.user_data.get(_STAR_KEY)
    asked_at = context.user_data.get(_STAR_AT_KEY)
    if not isinstance(pending, dict) or not pending.get("queue"):
        _clear_star(context)
        return None
    if (
        not isinstance(asked_at, int | float)
        or time.time() - asked_at > _STAR_TTL_SECONDS
    ):
        _clear_star(context)
        return None
    return pending


async def _reply_long(reply, text: str, markup=None) -> None:
    """Reply in Telegram-sized chunks, the keyboard on the last one."""
    parts = chunk_message(text)
    for index, part in enumerate(parts):
        await reply.reply_text(
            part, reply_markup=markup if index == len(parts) - 1 else None
        )


async def _ask_star(
    context: ContextTypes.DEFAULT_TYPE,
    reply,
    star_id: int,
    queue: list[str],
    *,
    total: int,
) -> None:
    """Ask the next question of the flow and (re)arm the 30-minute expiry."""
    context.user_data[_STAR_KEY] = {"id": star_id, "queue": queue, "total": total}
    context.user_data[_STAR_AT_KEY] = time.time()
    position = total - len(queue) + 1
    await reply.reply_text(
        star_question_message(position, total, queue[0], _lang(context))
    )


async def _begin_star(
    context: ContextTypes.DEFAULT_TYPE,
    reply,
    story: StarStory,
    fields: list[str],
    *,
    intro: str | None = None,
) -> None:
    """Start a (possibly single-field) guided flow on a story."""
    context.user_data[_PASTE_KEY] = False
    _take_pending_note(context)
    if intro:
        await reply.reply_text(intro)
    await _ask_star(context, reply, story.id, fields, total=len(fields))


async def _star_progress(
    context: ContextTypes.DEFAULT_TYPE,
    reply,
    pending: dict[str, Any],
    story: StarStory,
) -> None:
    queue = list(pending["queue"])[1:]
    if queue:
        await _ask_star(context, reply, story.id, queue, total=int(pending["total"]))
        return
    _clear_star(context)
    lang = _lang(context)
    await _reply_long(
        reply,
        f"{star_story_message(story, lang)}\n\n{t('bot.star_saved', lang)}",
        star_story_keyboard(story.id, lang),
    )


async def _save_star_answer(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict[str, Any]
) -> None:
    """The free-text reply to a STAR question: stored verbatim (strip only)."""
    service = _service(context)
    field = str(pending["queue"][0])
    try:
        story = service.answer_star(
            int(pending["id"]), field, update.message.text or ""
        )
    except ServiceError:
        lang = _lang(context)
        await update.message.reply_text(
            star_question_message(
                int(pending["total"]) - len(pending["queue"]) + 1,
                int(pending["total"]),
                field,
                lang,
            )
            + "\n"
            + t("bot.star_answer_hint", lang)
        )
        return
    except RepositoryError as exc:
        _clear_star(context)
        await update.message.reply_text(_err(exc, _lang(context)))
        return
    await _star_progress(context, update.message, pending, story)


async def star_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_star(context)
    await _star_list(_service(context), update.message, _lang(context))


async def _star_list(service: JobService, reply, lang: Lang) -> None:
    stories = service.star_stories()
    if not stories:
        candidates = service.star_candidates()
        await reply.reply_text(
            star_candidates_message(candidates, first=True, lang=lang),
            reply_markup=star_candidates_keyboard(candidates, lang),
        )
        return
    await _reply_long(
        reply, star_list_message(stories, lang), star_list_keyboard(stories, lang)
    )


async def _star_callback(
    service: JobService, query, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    """``st:`` buttons: candidates, view, per-field edit, delete."""
    reply = query.message
    lang = _lang(context)
    try:
        action = parts[0] if parts else ""
        if action == "l" and len(parts) == 1:
            _clear_star(context)
            await _star_list(service, reply, lang)
        elif action == "add" and len(parts) == 1:
            candidates = service.star_candidates(unused_only=True)
            await reply.reply_text(
                star_candidates_message(candidates, first=False, lang=lang),
                reply_markup=star_candidates_keyboard(candidates, lang)
                if candidates
                else None,
            )
        elif action == "new" and len(parts) == 2:
            story, created = service.start_star(parts[1])
            if not created:
                await _reply_long(
                    reply,
                    t("bot.star_exists", lang) + "\n" + star_story_message(story, lang),
                    star_story_keyboard(story.id, lang),
                )
                return
            await _begin_star(
                context,
                reply,
                story,
                [f for f, _q in star_fields(lang)],
                intro=star_intro_message(story, service.star_source_texts(story), lang),
            )
        elif action in ("v", "e", "d", "dy") and len(parts) == 2 and parts[1].isdigit():
            story = service.star_story(int(parts[1]))
            if action == "v":
                await _reply_long(
                    reply,
                    star_story_message(story, lang),
                    star_story_keyboard(story.id, lang),
                )
            elif action == "e":
                await reply.reply_text(
                    t("bot.star_edit_which", lang, title=story.title),
                    reply_markup=star_edit_keyboard(story.id, lang),
                )
            elif action == "d":
                await reply.reply_text(
                    t("bot.star_delete_confirm", lang, title=story.title),
                    reply_markup=star_delete_keyboard(story.id, lang),
                )
            else:
                service.delete_star(story.id)
                _clear_star(context)
                await reply.reply_text(t("bot.star_deleted", lang, title=story.title))
        elif (
            action == "f"
            and len(parts) == 3
            and parts[1].isdigit()
            and parts[2].isdigit()
        ):
            story = service.star_story(int(parts[1]))
            index = int(parts[2])
            fields = star_fields(lang)
            if index >= len(fields):
                await reply.reply_text(t("bot.unknown_action", lang))
                return
            await _begin_star(context, reply, story, [fields[index][0]])
        else:
            await reply.reply_text(t("bot.unknown_action", lang))
    except (ServiceError, RepositoryError) as exc:
        await reply.reply_text(_err(exc, lang))


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _lang(context)
    pending = _live_star(context)
    if pending is not None:
        service = _service(context)
        try:
            field = str(pending["queue"][0])
            kept = service.star_story(int(pending["id"]))
            story = service.skip_star(int(pending["id"]), field)
        except (ServiceError, RepositoryError) as exc:
            _clear_star(context)
            await update.message.reply_text(_err(exc, lang))
            return
        if getattr(kept, field) != STAR_PLACEHOLDER:
            await update.message.reply_text(
                t("bot.star_unchanged", lang, field=star_field_label(field, lang))
            )
        await _star_progress(context, update.message, pending, story)
        return
    if _take_pending_note(context) is None:
        await update.message.reply_text(t("bot.skip_nothing", lang))
        return
    await update.message.reply_text(t("bot.skip_note", lang))


async def _reply_outcome_menu(service: JobService, reply, offer_id: int) -> None:
    """Header + timeline, with the estado buttons once the offer is sent."""
    lang = _ui(service)
    try:
        offer = service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(_err(exc, lang))
        return
    text = outcome_message(offer, service.outcome_events(offer_id), lang=lang)
    if offer.fecha_envio is None:
        await reply.reply_text(f"{text}\n" + t("bot.not_sent_yet", lang, id=offer_id))
        return
    await reply.reply_text(
        text, reply_markup=outcome_keyboard(offer_id, offer.estado, lang=lang)
    )


async def _followup_action(
    service: JobService, query, offer_id: int, action: str
) -> None:
    """Follow-up reminder buttons: log a manual follow-up, snooze, or give up.

    Buttons are stale once the offer left ``enviada``: nothing is changed.
    """
    lang = _ui(service)
    try:
        estado = service.offer(offer_id).estado
        if estado != "enviada":
            await query.message.reply_text(
                t("bot.already_in", lang, estado=_estado(estado, lang))
            )
            return
        if action == "ok":
            number = service.record_followup_sent(offer_id)
            done = t("bot.fu_ok", lang, number=number, id=offer_id)
        elif action == "skip":
            service.snooze_followup(offer_id)
            done = t("bot.fu_skip", lang, id=offer_id)
        else:
            service.record_outcome(offer_id, "sin_respuesta")
            done = t("bot.fu_none", lang, id=offer_id)
    except (ServiceError, RepositoryError) as exc:
        await query.message.reply_text(_err(exc, lang))
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(done)


async def _ask_calibration(
    service: JobService, reply, offer_id: int, estado: str
) -> None:
    """After an outcome: the calibration question, as its own message.

    It is a separate message with buttons (callbacks), so it never consumes the
    optional note capture armed just before (the note prompt follows it): the owner can answer it and still
    write the note (or /skip) in any order. Asked once per offer and polarity.
    """
    lang = _ui(service)
    try:
        if service.calibration_needs_reason(offer_id, estado):
            await reply.reply_text(
                calibration_reason_message(lang),
                reply_markup=calibration_reason_keyboard(offer_id, lang),
            )
            return
        example = service.propose_calibration(offer_id, estado)
    except (ServiceError, RepositoryError) as exc:
        _LOG.warning("calibration question skipped for #%s: %s", offer_id, exc)
        return
    if example is not None:
        await _send_calibration_question(reply, example, lang)


async def _send_calibration_question(
    reply, example: CalibrationExample, lang: Lang
) -> None:
    await reply.reply_text(
        calibration_question_message(example, lang),
        reply_markup=calibration_question_keyboard(example.id, lang),
    )


async def _calibration_callback(service: JobService, query, parts: list[str]) -> None:
    """``cal:`` buttons: rejection reason, Sí/No on an example, deactivate."""
    reply = query.message
    action = parts[0]
    lang = _ui(service)
    try:
        if (
            action == "r"
            and len(parts) == 3
            and parts[2] in (*FIT_REASONS, REASON_OTHER)
        ):
            offer_id = _parse_id(parts[1])
            if offer_id is None:
                return
            if not service.calibration_needs_reason(offer_id, "rechazada"):
                # A reason already chosen whose Sí/No never arrived is re-asked.
                pending = service.propose_calibration(offer_id, "rechazada", parts[2])
                if pending is None:
                    await reply.reply_text(t("bot.cal_stale", lang))
                    return
                await _send_calibration_question(reply, pending, lang)
                return
            example = service.propose_calibration(offer_id, "rechazada", parts[2])
            if example is not None:
                await query.edit_message_reply_markup(reply_markup=None)
                await _send_calibration_question(reply, example, lang)
            else:
                await query.edit_message_reply_markup(reply_markup=None)
                await reply.reply_text(t("bot.cal_declined", lang))
        elif action in ("y", "n", "d") and len(parts) == 2:
            example_id = _parse_id(parts[1])
            if example_id is None:
                return
            await _calibration_answer(service, query, action, example_id)
    except (ServiceError, RepositoryError) as exc:
        await reply.reply_text(_err(exc, lang))


async def _calibration_answer(
    service: JobService, query, action: str, example_id: int
) -> None:
    reply = query.message
    lang = _ui(service)
    if action == "d":
        if not service.deactivate_calibration(example_id):
            await reply.reply_text(t("bot.cal_stale", lang))
            return
        await reply.reply_text(t("bot.cal_deactivated", lang))
        return
    example = (
        service.approve_calibration(example_id)
        if action == "y"
        else service.decline_calibration(example_id)
    )
    if example is None:
        await reply.reply_text(t("bot.cal_stale", lang))
        return
    done = t("bot.cal_approved" if action == "y" else "bot.cal_declined", lang)
    await query.edit_message_text(done, reply_markup=None)


async def calibracion_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    service = _service(context)
    lang = _lang(context)
    examples = service.active_calibrations()
    await _reply_long(
        update.message,
        calibration_list_message(examples, service.calibration_summary(), lang),
        calibration_list_keyboard(examples, lang) if examples else None,
    )


async def outcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text(
            t("bot.usage", _lang(context), usage="/outcome <id>")
        )
        return
    await _reply_outcome_menu(_service(context), update.message, offer_id)


async def _resend_card_below(service: JobService, message, offer_id: int):
    """Re-send the pressed card as the newest message, then delete the old one
    (best effort), so the CV that follows sits right next to its offer.

    Returns the message to keep working on: the new card, or the original when
    it has no plain text to re-send. The copy goes out first, so a failed
    re-send never loses the card. A delete failure (e.g. a message older than
    48 h) is logged and ignored. The stored "También en" row follows the new
    message id so later silent edits still target the visible card.
    """
    if not message.text:
        return message
    if getattr(message, "entities", None):
        # Links/bold would be lost re-sending plain text: keep them as HTML.
        sent = await message.reply_text(
            message.text_html,
            reply_markup=message.reply_markup,
            parse_mode="HTML",
        )
    else:
        sent = await message.reply_text(message.text, reply_markup=message.reply_markup)
    try:
        await message.delete()
    except Exception as exc:  # noqa: BLE001 — too old / already gone: keep going
        _LOG.warning("could not delete card %s: %s", message.message_id, exc)
    # Only the tracked card follows: a separate reply for the same offer
    # (e.g. /analyze next to the auto card) must not hijack the stored row.
    service.move_card(
        offer_id, message.chat_id, message.message_id, sent.chat_id, sent.message_id
    )
    return sent


async def _send_pdf(reply, pdf_path) -> None:
    """Deliver a generated PDF as a Telegram document."""
    with pdf_path.open("rb") as document:
        await reply.reply_document(document, filename=pdf_path.name)


async def _send_letter_pdf(reply, render: Callable[[], Path], lang: Lang) -> None:
    """Render a letter PDF and send it; a failure only warns (the text stays)."""
    try:
        pdf_path = render()
        await _send_pdf(reply, pdf_path)
    except Exception as exc:  # noqa: BLE001 — the text letter already went out
        _LOG.warning("letter PDF not delivered: %s", exc)
        await reply.reply_text(
            t("bot.letter_pdf_failed", lang, reason=_reason(exc, lang))
        )


def _parse_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


async def cv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    families = _families(context)
    ui = _lang(context)
    usage = t("bot.usage", ui, usage=cv_usage(families, ui))
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text(usage)
        return
    family = families.by_role(args[1]) if len(args) > 1 else None
    if len(args) > 1 and family is None:
        await update.message.reply_text(usage)
        return
    role = family.role if family is not None else None
    lang = args[2].lower() if len(args) > 2 else None
    service = _service(context)
    pdf_path = await service.generate_cv(offer_id, role, lang)  # type: ignore[arg-type]
    await _send_pdf(update.message, pdf_path)
    await update.message.reply_text(
        cv_ready_message(
            pdf_path,
            url=service.offer(offer_id).url_oferta,
            apply_findings=_apply_findings(service, offer_id),
            salary_line=service.card_extras(offer_id),
            lang=ui,
        ),
        reply_markup=sent_keyboard(
            offer_id,
            cv_base=service.offer(offer_id).cv_base,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            families=families,
            lang=ui,
        ),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    lang = _lang(context)
    args = context.args or []
    if args and (offer_id := _parse_id(args[0])) is not None:
        await update.message.reply_text(
            offer_status_line(
                service.offer(offer_id),
                also_on=service.also_on(offer_id),
                families=_families(context),
                lang=lang,
            )
        )
        return
    if args:
        await update.message.reply_text(t("bot.usage", lang, usage="/status [id]"))
        return
    rows = service.recent_offers()
    families = _families(context)
    listing = "\n".join(
        offer_status_line(row, families=families, lang=lang) for row in rows
    )
    await update.message.reply_text(listing or t("bot.no_offers", lang))


async def sent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    lang = _lang(context)
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text(t("bot.usage", lang, usage="/sent <id>"))
        return
    try:
        _service(context).mark_sent(offer_id)
    except ServiceError as exc:
        await update.message.reply_text(_err(exc, lang))
        return
    await update.message.reply_text(
        sent_message(offer_id, _service(context).offer(offer_id).match or 0, lang=lang),
        reply_markup=sent_outcome_keyboard(offer_id, lang=lang),
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = _service(context).export()
    await update.message.reply_text(t("bot.exported", _lang(context), path=path))


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    rows = service.panel_rows()  # SQLite stays on the loop thread
    discarded = service.panel_discarded()
    html_text = await asyncio.to_thread(service.panel_html, rows, discarded)
    await update.message.reply_document(
        io.BytesIO(html_text.encode("utf-8")), filename=PANEL_FILENAME
    )


async def rescatar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-send the analysis card of an offer the bot discarded (see /panel)."""
    args = context.args or []
    lang = _lang(context)
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text(t("bot.usage_rescue", lang))
        return

    async def notify(text: str, keyboard: Any) -> SentMessage:
        sent = await update.message.reply_text(text, reply_markup=keyboard)
        return SentMessage(sent.chat_id, sent.message_id)

    try:
        await _service(context).rescue_offer(offer_id, notify)
    except ServiceError as exc:
        await update.message.reply_text(_err(exc, lang))


async def keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    rows = _service(context).keywords(args[0] if args else None)
    if not rows:
        await update.message.reply_text(t("bot.no_keywords", _lang(context)))
        return
    lines = [f"{row.status or '·'} {row.keyword} ×{row.frequency}" for row in rows]
    await update.message.reply_text("\n".join(lines))


async def pausa_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _service(context).set_paused(True)
    await update.message.reply_text(t("bot.paused", _lang(context)))


async def reanudar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _service(context).set_paused(False)
    await update.message.reply_text(t("bot.resumed", _lang(context)))


async def beacons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        beacons_list_message(_service(context).beacons(), lang=_lang(context))
    )


async def cartabase_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/cartabase [es|en|ca]``: the open letter (fixed text, no LLM call).

    The letter goes alone in its own plain-text message so it copies cleanly;
    a short note follows. The base is read on every call (edits apply live).
    """
    lang = _lang(context)
    args = [arg.lower() for arg in context.args or []]
    language = args[0] if args else "es"
    if len(args) > 1 or language not in LETTER_LANGUAGES:
        await update.message.reply_text(t("bot.cartabase_usage", lang))
        return
    path = context.bot_data.get(_LETTER_BASE_KEY) or resolve_profile_paths().letter_base
    try:
        bases = load_letter_bases(Path(path))
    except ProfileError as exc:
        await update.message.reply_text(t("bot.letter_base_error", lang, error=exc))
        return
    letter = assemble_open_letter(bases[language])
    await update.message.reply_text(letter)
    await update.message.reply_text(t("bot.cartabase_note", lang))
    service = _service(context)
    await _send_letter_pdf(
        update.message, lambda: service.open_letter_pdf(letter, language), lang
    )


async def beacon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        beacon_command_reply(
            _service(context), list(context.args or []), lang=_lang(context)
        )
    )


def _cover_letter_asked(service: JobService, offer_id: int) -> bool:
    try:
        return asks_cover_letter(_apply_findings(service, offer_id))
    except RepositoryError:  # stateless toggles may outlive the offer row
        return False


async def _write_cover_letters_and_reply(
    service: JobService, reply, offer_id: int
) -> None:
    """ "✉️ Carta de presentación" press: two plain-text versions, each with a
    [✅ Usar esta] button. Any failure replies (UI language) and stores nothing."""
    lang = _ui(service)
    try:
        await reply.reply_text(t("bot.writing_letter", lang))
        result = await service.cover_letters(offer_id)
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(t("bot.letter_failed", lang, reason=_reason(exc, lang)))
        return
    await reply.reply_text(
        cover_letter_message(t("bot.letter_label", lang), result.text),
        reply_markup=cover_letter_keyboard(offer_id, "a", result.generation, lang=lang),
    )
    await reply.reply_text(cover_letter_sources_message(result.facts, lang=lang))
    if result.warnings:
        await reply.reply_text(
            t("bot.letter_warnings", lang)
            + "\n"
            + "\n".join(f"- {warning}" for warning in result.warnings)
        )


async def _research_salary_and_reply(service: JobService, reply, offer_id: int) -> None:
    """ "💶 Investigar salario" press: cited range, cached same-day result, or a
    reason in the UI language (no web-search provider / LLM failure). Never
    fabricates."""
    lang = _ui(service)
    try:
        await reply.reply_text(t("bot.researching_salary", lang))
        outcome = await service.research_salary(offer_id)
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(t("bot.salary_failed", lang, reason=_reason(exc, lang)))
        return
    await reply.reply_text(salary_research_message(outcome, lang=lang))


async def _prep_stage_choice(service: JobService, reply, offer_id: int) -> None:
    """Ask which stage to prepare, suggesting one from the offer's estado."""
    lang = _ui(service)
    try:
        offer = service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(_err(exc, lang))
        return
    suggested = default_stage(offer.estado)
    await reply.reply_text(
        t(
            "bot.prep_choice",
            lang,
            id=offer_id,
            company=offer.empresa or t("bot.no_company", lang),
            stage=t(f"notifier.stage.{suggested}", lang)
            if f"notifier.stage.{suggested}" in CATALOGS[lang]
            else STAGE_LABEL[suggested],
            estado=_estado(offer.estado, lang),
        ),
        reply_markup=prep_stage_keyboard(offer_id, suggested, lang=lang),
    )


async def _prepare_and_reply(
    service: JobService, reply, offer_id: int, stage: str
) -> None:
    """Stage press: progress, then the HTML pack + a short summary. Any failure
    replies in the UI language and sends no partial pack."""
    lang = _ui(service)
    try:
        service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(_err(exc, lang))
        return
    if not service.claim_prep(offer_id, stage):  # sync: atomic with the check
        await reply.reply_text("⏳ " + t("bot.err.prep_busy", lang))
        return
    try:
        await reply.reply_text(t("bot.preparing", lang))
        delivery = await service.prepare_interview(offer_id, stage, claimed=True)
        await reply.reply_document(
            io.BytesIO(delivery.html.encode("utf-8")), filename=delivery.filename
        )
        await reply.reply_text(delivery.summary)
    except ValidationError:
        await reply.reply_text(t("bot.prep_invalid", lang))
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(t("bot.prep_failed", lang, reason=_reason(exc, lang)))
    finally:
        service.release_prep(offer_id, stage)


async def prep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text(
            t("bot.usage", _lang(context), usage="/prep <id>")
        )
        return
    await _prep_stage_choice(_service(context), update.message, offer_id)


async def _apply_outcome(
    service: JobService, query, context, offer_id: int, estado: str
) -> bool:
    """Record an outcome estado and run the follow-up flow (confirmation,
    calibration question, note prompt). Shared by the outcome menu buttons and
    the Gmail "Sí" button. True when the estado was recorded."""
    lang = _lang(context)
    try:
        service.record_outcome(offer_id, estado)
    except (ServiceError, RepositoryError) as exc:
        await query.message.reply_text(_err(exc, lang))
        return False
    context.user_data[_PASTE_KEY] = False
    _clear_star(context)
    context.user_data[_NOTE_KEY] = offer_id
    context.user_data[_NOTE_AT_KEY] = time.time()
    await query.message.reply_text(
        t("bot.outcome_set", lang, id=offer_id, estado=_estado(estado, lang)),
        reply_markup=prep_keyboard(offer_id, lang=lang)
        if estado in PREP_ESTADOS
        else None,
    )
    await _ask_calibration(service, query.message, offer_id, estado)
    await query.message.reply_text(outcome_note_prompt(lang))
    return True


async def _clear_markup(query) -> None:
    """Remove a card's buttons; best-effort (a Telegram hiccup, an already-edited
    message) so a failed edit can never undo or lose the owner's decision."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as exc:  # noqa: BLE001 — cosmetic; the decision is stored
        _LOG.warning("gmail proposal buttons not removed: %s", type(exc).__name__)


async def _gmail_callback(
    service: JobService, query, context, action: str, message_id: str
) -> None:
    """Gmail proposal buttons: ``y`` applies the proposed estado through the
    normal outcome path, ``o`` opens the outcome menu, ``i`` ignores.

    Idempotent: the decision is claimed atomically from ``pending``, so a
    double press or a press on an old proposal only gets a polite answer, and
    a proposal the owner already outran (offer at that estado or later) is
    closed as stale without touching the offer.
    """
    lang = _lang(context)
    row = service.gmail_proposal(message_id)
    if row is None:
        await query.message.reply_text(t("bot.gm_gone", lang))
        return
    if row.decision != "pending":
        await query.message.reply_text(t("bot.gm_resolved", lang))
        return
    if action == "i":
        if service.resolve_gmail_proposal(message_id, "ignored", expect="pending"):
            await _clear_markup(query)
            await query.message.reply_text(t("bot.gm_ignored", lang))
        return
    if action == "o":
        if service.resolve_gmail_proposal(message_id, "other", expect="pending"):
            await _reply_outcome_menu(service, query.message, row.offer_id)
            await _clear_markup(query)
        return
    try:
        current = service.offer(row.offer_id).estado
    except RepositoryError as exc:
        await query.message.reply_text(_err(exc, lang))
        return
    if not should_propose(current, row.proposed_estado):
        service.resolve_gmail_proposal(message_id, "stale", expect="pending")
        await _clear_markup(query)
        await query.message.reply_text(
            t("bot.already_in", lang, estado=_estado(current, lang))
        )
        return
    if not service.resolve_gmail_proposal(message_id, "accepted", expect="pending"):
        await query.message.reply_text(t("bot.gm_resolved", lang))
        return
    # Record the outcome FIRST; the buttons stay on the card until it is
    # recorded, so a failure leaves the proposal retryable from the same card.
    try:
        recorded = await _apply_outcome(
            service, query, context, row.offer_id, row.proposed_estado
        )
    except Exception:
        if _reopen_unless_recorded(service, message_id, row):
            await _clear_markup(query)  # landed: the card is resolved too
        raise
    if recorded:
        await _clear_markup(query)
    else:
        service.resolve_gmail_proposal(message_id, "pending")


def _reopen_unless_recorded(service: JobService, message_id: str, row) -> bool:
    """After an unexpected error mid-"Sí": reopen the proposal unless the
    estado did land (then the approval is honoured and stays accepted).
    Returns whether it landed. Never raises, so it cannot mask the original
    error."""
    try:
        landed = service.offer(row.offer_id).estado == row.proposed_estado
    except Exception:  # noqa: BLE001 — unknown state: reopen, keep the original error
        landed = False
    if not landed:
        service.resolve_gmail_proposal(message_id, "pending")
    return landed


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stateless callback router (notifier payload scheme)."""
    query = update.callback_query
    await query.answer()
    service = _service(context)
    families = _families(context)
    ui = _lang(context)
    kind, parts = (query.data or "").split(":", 1)[0], (query.data or "").split(":")[1:]
    if query.message is not None and kind not in ("cv", CL):
        # The handlers below rewrite this card: stop tracking it so a later
        # "También en" edit can't clobber what the owner just did. (A "cv" or
        # cover-letter press re-sends the card instead and re-points the
        # stored row.)
        service.forget_card(query.message.chat_id, query.message.message_id)
    if kind == "an" and len(parts) == 1:
        url = f"https://linkedin.com/jobs/view/{parts[0]}"
        offer_id = await _ingest_url(service, query.message, context, url)
        if offer_id is not None:
            await _analyze_and_reply(service, query.message, context, offer_id)
    elif kind == "cv" and parts and (offer_id := _parse_id(parts[0])) is not None:
        role = normalize_role(parts[1], families=families) if len(parts) > 1 else None
        lang = parts[2] if len(parts) > 2 else None
        if (no_cv := service.no_cv_label(offer_id)) is not None:  # stale button
            await query.message.reply_text(no_cv_reply(no_cv, lang=ui))
            return
        if len(parts) > 1 and families.get(parts[1]) is None:
            # An old button of a family the profile no longer declares.
            await query.message.reply_text(
                unknown_role_message(parts[1], families.default.label, lang=ui)
            )
            if lang not in families.default.languages:
                lang = families.default.languages[0]
        card = await _resend_card_below(service, query.message, offer_id)
        pdf_path = await service.generate_cv(offer_id, role, lang)  # type: ignore[arg-type]
        await _send_pdf(card, pdf_path)
        # The re-sent card is rewritten now: stop tracking it (as every other
        # button does) so a later "También en" edit can't restore the old text.
        service.forget_card(card.chat_id, card.message_id)
        await card.edit_text(
            cv_ready_message(
                pdf_path,
                url=service.offer(offer_id).url_oferta,
                apply_findings=_apply_findings(service, offer_id),
                salary_line=service.card_extras(offer_id),
                lang=ui,
            ),
            reply_markup=sent_keyboard(
                offer_id,
                cv_base=service.offer(offer_id).cv_base,
                cover_letter_asked=_cover_letter_asked(service, offer_id),
                families=families,
                lang=ui,
            ),
        )
    elif kind in ("lang", "variant") and len(parts) == 3:
        offer_id = int(parts[0])
        role, lang = normalize_role(parts[1], families=families), parts[2]
        if kind == "lang":
            lang = next_lang(role, lang, families=families)
        else:
            role = next_role(role, families=families)
        await query.edit_message_reply_markup(
            reply_markup=cv_keyboard(
                offer_id,
                role=role,
                lang=lang,
                cover_letter_asked=_cover_letter_asked(service, offer_id),
                families=families,
                ui_lang=ui,
            )
        )
    elif kind == "discard" and len(parts) == 1:
        offer_id = _parse_id(parts[0])
        if offer_id is not None:
            service.discard(offer_id)
            await query.edit_message_text(discard_message(offer_id, lang=ui))
    elif kind == "disc" and parts and (offer_id := _parse_id(parts[0])) is not None:
        try:
            service.discard(offer_id)
        except ServiceError as exc:
            await query.message.reply_text(_err(exc, ui))
            return
        await query.edit_message_text(
            discard_no_send_message(lang=ui), reply_markup=None
        )
    elif kind == "sent" and parts and (offer_id := _parse_id(parts[0])) is not None:
        try:
            service.mark_sent(offer_id)
        except ServiceError as exc:
            await query.message.reply_text(_err(exc, ui))
            return
        await query.edit_message_text(
            sent_message(offer_id, service.offer(offer_id).match or 0, lang=ui),
            reply_markup=sent_outcome_keyboard(offer_id, lang=ui),
        )
    elif kind == OUT and parts and (offer_id := _parse_id(parts[0])) is not None:
        await _reply_outcome_menu(service, query.message, offer_id)
    elif (
        kind == OE
        and len(parts) == 2
        and parts[1] in OUTCOME_ESTADOS
        and (offer_id := _parse_id(parts[0])) is not None
    ):
        await _apply_outcome(service, query, context, offer_id, parts[1])
    elif kind == GM and len(parts) == 2 and parts[0] in ("y", "o", "i"):
        await _gmail_callback(service, query, context, parts[0], parts[1])
    elif kind == CAL and parts:
        await _calibration_callback(service, query, parts)
    elif (
        kind == FU
        and len(parts) == 2
        and parts[1] in ("ok", "skip", "none")
        and (offer_id := _parse_id(parts[0])) is not None
    ):
        await _followup_action(service, query, offer_id, parts[1])
    elif kind == SAL and parts and (offer_id := _parse_id(parts[0])) is not None:
        await _research_salary_and_reply(service, query.message, offer_id)
    elif kind == CL and parts and (offer_id := _parse_id(parts[0])) is not None:
        card = await _resend_card_below(service, query.message, offer_id)
        await _write_cover_letters_and_reply(service, card, offer_id)
    elif (
        kind == CLC
        and len(parts) == 3
        and parts[2] in ("a", "b")
        and parts[1].isdigit()
        and (offer_id := _parse_id(parts[0])) is not None
    ):
        try:
            service.choose_cover_letter(offer_id, parts[2], generation=int(parts[1]))
        except ServiceError as exc:
            await query.message.reply_text(_err(exc, ui))
            return
        await query.message.reply_text(t("bot.letter_saved", ui))
        await _send_letter_pdf(
            query.message, lambda: service.cover_letter_pdf(offer_id), ui
        )
    elif (
        kind == PREP
        and len(parts) in (1, 2)
        and (offer_id := _parse_id(parts[0])) is not None
        and (len(parts) == 1 or parts[1] in STAGES)
    ):
        if len(parts) == 1:
            await _prep_stage_choice(service, query.message, offer_id)
        else:
            await _prepare_and_reply(service, query.message, offer_id, parts[1])
    elif kind == ST:
        await _star_callback(service, query, context, parts)
    elif kind in (BEACON_RESUME, BEACON_DISABLE) and len(parts) == 1:
        if (
            reply := beacon_callback_reply(service, kind, parts[0], lang=ui)
        ) is not None:
            await query.edit_message_text(reply)
    else:
        await query.message.reply_text(t("bot.unknown_action", ui))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB backstop: log + best-effort owner notification; never crashes."""
    error = context.error
    _LOG.error("unhandled handler error: %s", error, exc_info=error)
    try:
        await context.bot.send_message(
            int(context.bot_data[_OWNER_KEY]),
            backstop_error_message(_reason(error, _lang(context)), lang=_lang(context)),
        )
    except Exception:  # noqa: BLE001 — a failing backstop must not crash either
        _LOG.exception("could not notify the owner about the failure")


def register_menu(app: Application, lang: Lang) -> None:
    """Publish the ``lang`` command menu at startup (chains any existing
    ``post_init``).

    Call AFTER ``bridge.register``, which assigns ``app.post_init`` itself.
    A Telegram failure is logged, never fatal.
    """
    previous = app.post_init

    async def _post_init(application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                [BotCommand(name, text) for name, text in menu_commands(lang)]
            )
        except Exception:  # noqa: BLE001 — the menu is cosmetic
            _LOG.warning("could not set the Telegram command menu", exc_info=True)
        if previous is not None:
            await previous(application)

    app.post_init = _post_init


# Callback per ``cva.commands.COMMANDS`` entry (by ``Command.name``); every
# Spanish and English name and alias of the table is registered to it.
_COMMAND_HANDLERS: dict[
    str, Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
] = {
    "help": start_help,
    "analyze": analyze_command,
    "cv": cv_command,
    "status": status_command,
    "sent": sent_command,
    "export": export_command,
    "panel": panel_command,
    "rescatar": rescatar_command,
    "outcome": outcome_command,
    "calibracion": calibracion_command,
    "skip": skip_command,
    "star": star_command,
    "prep": prep_command,
    "keywords": keywords_command,
    "pausa": pausa_command,
    "reanudar": reanudar_command,
    "beacons": beacons_command,
    "beacon": beacon_command,
    "cartabase": cartabase_command,
}


def build_app(settings: Settings, service: JobService) -> Application:
    """Wire handlers: whitelist gate (group -1) → commands → paste → callbacks."""
    app = (
        Application.builder().token(settings.telegram_token.get_secret_value()).build()
    )
    app.bot_data[_SERVICE_KEY] = service
    app.bot_data[_OWNER_KEY] = settings.owner_telegram_id
    app.bot_data[_LANG_KEY] = settings.ui_language
    app.bot_data[_LETTER_BASE_KEY] = resolve_profile_paths(
        settings.profile_dir
    ).letter_base
    app.add_error_handler(on_error)
    app.add_handler(TypeHandler(Update, gate), group=-1)
    for command in COMMANDS:
        app.add_handler(
            CommandHandler(
                [*command.names, *command.aliases], _COMMAND_HANDLERS[command.name]
            )
        )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, paste_handler))
    app.add_handler(CallbackQueryHandler(callback_router))
    return app


def configure_logging() -> None:
    """INFO for the app; WARNING for the HTTP client libraries.

    httpx logs every request URL at INFO, and Telegram URLs embed the bot token
    (``/bot<TOKEN>/getUpdates``) — at INFO the token lands in data/bot.log and
    long polling floods the file. Their warnings and errors still get through.
    """
    logging.basicConfig(level=logging.INFO)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def build_gmail_sync(settings: Settings, service: JobService) -> GmailSync | None:
    """The Gmail poller, or ``None`` while ``gmail_enabled`` is off (default):
    nothing then touches the mailbox, a token or a credentials file."""
    if not settings.gmail_enabled:
        return None
    return GmailSync(
        repo=service.repository,
        client=GoogleGmailClient(
            Path(settings.gmail_credentials_path).expanduser(),
            Path(settings.gmail_token_path).expanduser(),
            lang=settings.ui_language,
        ),
        llm=build_llm_client(settings),
        poll_minutes=settings.gmail_poll_minutes,
        lang=settings.ui_language,
    )


def main() -> None:
    configure_logging()
    settings = Settings()
    _LOG.info(describe_llm_setup(settings))
    if settings.owner_telegram_id == 0:
        _LOG.warning("owner_telegram_id is 0 (unset): every update will be ignored")
    service = JobService.from_settings(settings)
    app = build_app(settings, service)
    scheduler.register(
        app,
        service,
        settings.owner_telegram_id,
        gmail=build_gmail_sync(settings, service),
    )
    bridge.register(app, settings, service)
    register_menu(
        app, settings.ui_language
    )  # after the bridge: it overwrites app.post_init
    app.run_polling()  # long polling — no public endpoint, TLS, or webhook


if __name__ == "__main__":
    main()
