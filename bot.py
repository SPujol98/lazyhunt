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
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TextIO

from pydantic import ValidationError
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken
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
from cva.commands import (
    COMMANDS,
    LEGACY_HIDDEN,
    SETUP_COMMANDS,
    command_name,
    cv_usage,
    help_text,
    menu_commands,
    split_message,
)
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.fsutil import display_path
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
    AI_OUTAGE,
    BEACON_DISABLE,
    BEACON_RESUME,
    CL,
    CLC,
    CLD,
    CV_TAILOR,
    CV_TAILOR_ACTION,
    FU,
    GM,
    OE,
    OUT,
    PREP,
    SAL,
    SentMessage,
    ai_back_message,
    ai_outage_keyboard,
    ai_outage_message,
    ai_status_line,
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
    letter_quality_line,
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
    searches_overview_messages,
    sent_keyboard,
    sent_message,
    sent_outcome_keyboard,
    unknown_role_message,
    with_no_cv_line,
)
from cva.onboarding import cv_catalog
from cva.onboarding import flow as onboarding
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import LETTER_LANGUAGES, ProfileError, load_letter_bases
from cva.report.panel import PANEL_FILENAME
from cva.service import (
    JobService,
    OfferClosedError,
    ServiceError,
    TailorProposal,
    display_result,
)
from cva.setup import telegram_token
from cva.setup.pairing import (
    CLAIM_CODE_TTL_SECONDS,
    OneTimeCode,
    display,
    save_owner_id,
)
from cva.setup.wizard import LOCAL_TOML
from cva.setup_mode import describe_load_error, missing_profile_files, needs_setup
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
# Setup mode (onboarding-v2 T1): the unconfigured profile directory, and the
# flag set when it became complete (main() then starts the full app).
_PROFILE_DIR_KEY = "setup_profile_dir"
_SETUP_DONE_KEY = "setup_done"
# The Settings the setup app validates the full load with, the JobService that
# load built (main() reuses it), and the owner updates received from the switch
# on (main() replays them in the full app, so none is lost).
_SETUP_SETTINGS_KEY = "setup_settings"
_SETUP_SERVICE_KEY = "setup_service"
_SETUP_REPLAY_KEY = "setup_replay"
# config.local.toml the guided setup saves ui_language to (cwd-relative, as
# Settings reads it); absent in test doubles, which then never write it.
_CONFIG_PATH_KEY = "config_local_toml"
# Owner claim (beta3-ux T1): while owner_telegram_id is unset the bot runs a
# claim app first. Its one-time code, the id that claimed it, the owner updates
# that arrived after the claim (replayed in the next app) and whether the
# profile still needs /setup (the claim reply's next step).
_CLAIM_CODE_KEY = "claim_code"
_CLAIMED_KEY = "claimed_owner"
_CLAIM_REPLAY_KEY = "claim_replay"
_CLAIM_NEEDS_SETUP_KEY = "claim_needs_setup"


def is_owner(user_id: int | None, owner_id: int) -> bool:
    """Single-user whitelist gate; an unset owner (``0``) refuses everyone."""
    return bool(user_id) and bool(owner_id) and user_id == owner_id


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1 guard: strangers are ignored with no reply and no side effects."""
    user = update.effective_user
    if not is_owner(user.id if user else None, int(context.bot_data[_OWNER_KEY])):
        raise ApplicationHandlerStop


def _hidden_commands(context: ContextTypes.DEFAULT_TYPE | None) -> tuple[str, ...]:
    """Commands /help leaves out: ``/setup`` on a legacy install (an app
    built without ``profile_dir``), where it only refuses."""
    if context is None or _PROFILE_DIR_KEY not in context.bot_data:
        return ()
    return LEGACY_HIDDEN if context.bot_data[_PROFILE_DIR_KEY] is None else ()


async def reply_long(message: Any, text: str) -> None:
    """Reply ``text`` in as many messages as Telegram's 4096-character limit
    needs, cut at line boundaries (:func:`cva.commands.split_message`): text
    that grows with the profile (role families, labels) must never fail with
    "Message is too long"."""
    for piece in split_message(text):
        await message.reply_text(piece)


async def start_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_long(
        update.message,
        help_text(
            _families(context), lang=_lang(context), hidden=_hidden_commands(context)
        ),
    )


async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/connect`` (``/conectar``): a one-time code the extension's Options
    trades for the bridge secret (``POST /pair``), so the owner never opens
    ``.env``. Owner-only like every command (the group -1 gate)."""
    lang = _lang(context)
    pairing = context.bot_data.get(bridge.PAIRING_KEY)
    if not isinstance(pairing, OneTimeCode):
        await update.message.reply_text(t("bot.connect.disabled", lang))
        return
    await update.message.reply_text(
        t(
            "bot.connect.code",
            lang,
            code=display(pairing.issue()),
            connect=command_name("conectar", lang),
        )
    )


def _rows_markup(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup | None:
    """``(label, callback data)`` rows as an inline keyboard (``None`` if empty)."""
    if not rows:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in rows
        ]
    )


def _profile_dir(context: ContextTypes.DEFAULT_TYPE) -> Path | None:
    value = context.bot_data.get(_PROFILE_DIR_KEY)
    return value if isinstance(value, Path) else None


async def cvs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/cvs``: the base CV catalog (tailored-cv T1, :mod:`cva.onboarding.cv_catalog`):
    every slot with its origin, template and sections, and buttons to view
    the PDF, change the template or rebuild it (created CVs only)."""
    lang = _lang(context)
    profile_dir = _profile_dir(context)
    entries = await asyncio.to_thread(
        cv_catalog.base_cvs, profile_dir, _families(context)
    )
    setup = command_name("configurar", lang) if profile_dir is not None else None
    await update.message.reply_text(
        cv_catalog.catalog_text(entries, lang, setup),
        reply_markup=_rows_markup(cv_catalog.catalog_buttons(entries)),
    )


def _slot_name(slot: str, families: FamilyRegistry) -> str:
    role, _, lang = slot.rpartition("_")
    label = next((f.label for f in families.families if f.role == role), role)
    return f"{label} · {lang}"


async def _cb_cvs(press: "_Press", parts: list[str]) -> bool:
    """The catalog's buttons: ``cvs:pdf|tpl|re:<slot>``, ``cvs:set:<slot>:<template>``."""
    if len(parts) not in (2, 3) or (len(parts) == 3) != (parts[0] == "set"):
        return False
    action, slot = parts[0], parts[1]
    if action not in ("pdf", "tpl", "re", "set"):
        return False
    query, ui = press.query, press.ui
    profile_dir = _profile_dir(press.context)
    name = _slot_name(slot, press.families)
    try:
        if action == "pdf":
            pdf = cv_catalog.pdf_path(profile_dir, slot)
            with pdf.open("rb") as handle:
                await query.message.reply_document(handle, filename=pdf.name)
            return True
        if action == "tpl":
            source = cv_catalog.created_source(profile_dir, slot)
            await query.message.reply_text(
                t(
                    "bot.cvs.pick_template",
                    ui,
                    slot=name,
                    template=cv_catalog.template_name(source.template, ui),
                ),
                reply_markup=_rows_markup(
                    cv_catalog.template_buttons(slot, source.template, ui)
                ),
            )
            return True
        if action == "set":
            source = await asyncio.to_thread(
                cv_catalog.change_template, profile_dir, slot, parts[2]
            )
            done = t(
                "bot.cvs.done.template",
                ui,
                slot=name,
                template=cv_catalog.template_name(source.template, ui),
            )
        else:
            await asyncio.to_thread(cv_catalog.rebuild, profile_dir, slot)
            done = t("bot.cvs.done.rebuild", ui, slot=name)
    except cv_catalog.CatalogError as exc:
        await query.message.reply_text(t(exc.key, ui))
        return True
    await query.message.reply_text(done)
    return True


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
    if (outage := service.ai_outage()) is not None:
        # AI outage (llm-outage): no LLM call; the offer waits for the resume.
        service.defer_for_ai_outage(offer_id)
        await reply.reply_text(
            ai_outage_message(outage, lang=ui),
            reply_markup=ai_outage_keyboard(lang=ui),
        )
        return
    try:
        result = await service.analyze(offer_id)
        offer = service.offer(offer_id)
        lang = infer_language(offer.source_data)
        role = _families(context).role_for(offer.perfil)
    except OfferClosedError:
        await reply.reply_text(offer_closed_message(lang=ui))
        return
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        if (outage := service.note_ai_failure(exc)) is not None:
            service.defer_for_ai_outage(offer_id)
            service.mark_ai_outage_alerted()  # this reply IS the alert
            await reply.reply_text(
                ai_outage_message(outage, lang=ui),
                reply_markup=ai_outage_keyboard(lang=ui),
            )
            return
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
            offer=offer,
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
    if await onboarding.on_text(
        update.message, context.user_data, _onboarding_env(context)
    ):
        return  # a /setup step expecting text wins over paste, note and STAR
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
    cv_base = service.offer(offer_id).cv_base
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
            cv_base=cv_base,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            families=families,
            lang=ui,
            adaptable=service.can_adapt(cv_base),
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
                analysis_score=service.analysis_scores([offer_id]).get(offer_id),
            )
        )
        return
    if args:
        await update.message.reply_text(
            t("bot.usage", lang, usage=f"{command_name('estado', lang)} [id]")
        )
        return
    rows = service.recent_offers()
    families = _families(context)
    scores = service.analysis_scores([row.id for row in rows])
    listing = "\n".join(
        offer_status_line(
            row, families=families, lang=lang, analysis_score=scores.get(row.id)
        )
        for row in rows
    )
    text = listing or t("bot.no_offers", lang)
    if (outage := service.ai_outage()) is not None:
        text = ai_status_line(outage, lang=lang) + "\n\n" + text
    await update.message.reply_text(text)


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
    await update.message.reply_text(
        t("bot.exported", _lang(context), path=display_path(path))
    )


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


async def searches_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/searches``: what each watched search found and what was new (14 days)."""
    rows = _service(context).search_overview()
    for piece in searches_overview_messages(rows, lang=_lang(context)):
        await update.message.reply_text(piece)


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


_LETTER_EXTRAS_KEY = "letter_extras"


async def _write_cover_letters_and_reply(
    service: JobService,
    reply,
    offer_id: int,
    extras: dict[tuple[int, int], list[int]] | None = None,
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
    letter = await reply.reply_text(
        cover_letter_message(t("bot.letter_label", lang), result.text),
        reply_markup=cover_letter_keyboard(offer_id, "a", result.generation, lang=lang),
    )
    followers = [
        await reply.reply_text(
            "\n".join(
                filter(
                    None,
                    (
                        cover_letter_sources_message(result.facts, lang=lang),
                        letter_quality_line(result.quality, lang=lang),
                    ),
                )
            )
        )
    ]
    if result.warnings:
        followers.append(
            await reply.reply_text(
                t("bot.letter_warnings", lang)
                + "\n"
                + "\n".join(f"- {warning}" for warning in result.warnings)
            )
        )
    # "Don't use" removes the letter plus the messages sent right after it; the
    # ids live in memory only (nothing about a proposed letter is persisted).
    letter_id = getattr(letter, "message_id", None)
    if extras is not None and isinstance(letter_id, int):
        extras[(int(getattr(letter, "chat_id", 0)), letter_id)] = [
            m for f in followers if isinstance(m := getattr(f, "message_id", None), int)
        ]


async def _dismiss_letter(query, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    """ "🗑 Don't use": delete the letter message and its sources/warnings."""
    message = query.message
    extras = context.bot_data.setdefault(_LETTER_EXTRAS_KEY, {})
    follower_ids = extras.pop((message.chat_id, message.message_id), [])
    try:
        await context.bot.delete_message(message.chat_id, message.message_id)
    except Exception:  # noqa: BLE001 — too old (>48 h) or already gone
        await message.reply_text(t("bot.letter_dismiss_failed", lang))
        return
    for follower_id in follower_ids:
        with suppress(Exception):
            await context.bot.delete_message(message.chat_id, follower_id)


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


async def _close_gmail_siblings(
    service: JobService, context, offer_id: int, answered: str, estado: str | None
) -> None:
    """After an answer, close the offer's other unanswered proposals and turn
    their cards into a short "already handled" note without buttons (a failed
    edit is cosmetic: the decisions are already stored)."""
    lang = _lang(context)
    text = (
        t("bot.gm_handled", lang, id=offer_id, estado=_estado(estado, lang))
        if estado
        else t("bot.gm_handled_plain", lang, id=offer_id)
    )
    for chat_id, message_id in service.close_sibling_gmail_proposals(
        offer_id, answered
    ):
        try:
            await context.bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        except Exception as exc:  # noqa: BLE001 — cosmetic; decisions are stored
            _LOG.warning("gmail sibling card not edited: %s", type(exc).__name__)


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
            await _close_gmail_siblings(
                service, context, row.offer_id, message_id, None
            )
        return
    if action == "o":
        if service.resolve_gmail_proposal(message_id, "other", expect="pending"):
            await _reply_outcome_menu(service, query.message, row.offer_id)
            await _clear_markup(query)
            await _close_gmail_siblings(
                service, context, row.offer_id, message_id, None
            )
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
        await _close_gmail_siblings(service, context, row.offer_id, message_id, current)
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
        await _close_gmail_siblings(
            service, context, row.offer_id, message_id, row.proposed_estado
        )
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


@dataclass(frozen=True)
class _Press:
    """One inline-button press, as every callback handler needs it."""

    service: JobService
    query: Any
    context: ContextTypes.DEFAULT_TYPE
    families: FamilyRegistry
    ui: Lang
    kind: str


# A callback handler gets the press and the payload fields after the prefix;
# it returns False when it does not accept them (the "unknown action" reply).
_CallbackHandler = Callable[[_Press, list[str]], Awaitable[bool]]


async def _cb_analyze(press: _Press, parts: list[str]) -> bool:
    if len(parts) != 1:
        return False
    service, query, context = press.service, press.query, press.context
    url = f"https://linkedin.com/jobs/view/{parts[0]}"
    offer_id = await _ingest_url(service, query.message, context, url)
    if offer_id is not None:
        await _analyze_and_reply(service, query.message, context, offer_id)
    return True


async def _cb_cv(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    service, query, families, ui = press.service, press.query, press.families, press.ui
    role = normalize_role(parts[1], families=families) if len(parts) > 1 else None
    lang = parts[2] if len(parts) > 2 else None
    if (no_cv := service.no_cv_label(offer_id)) is not None:  # stale button
        await query.message.reply_text(no_cv_reply(no_cv, lang=ui))
        return True
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
    cv_base = service.offer(offer_id).cv_base
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
            cv_base=cv_base,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            families=families,
            lang=ui,
            adaptable=service.can_adapt(cv_base),
        ),
    )
    return True


# --- adapted CV (tailored-cv T3) ----------------------------------------------

# Removal buttons shown under a preview (one per change, the first ones).
_TAILOR_REMOVE_BUTTONS = 5


def _tailor_preview(proposal: TailorProposal, ui: Lang) -> str:
    """Numbered change lines, the dropped suggestions in one line, a note."""
    changes = "\n".join(f"{i}. {line}" for i, line in enumerate(proposal.changes, 1))
    lines = [t("tailor.bot.preview", ui, id=proposal.offer_id, changes=changes)]
    dropped = sum(1 for v in proposal.dropped if v.reason != "order_completed")
    if dropped:
        key = "tailor.bot.dropped.one" if dropped == 1 else "tailor.bot.dropped.other"
        lines.append(t(key, ui, n=dropped))
    if proposal.new_synonyms:
        lines.append(t("tailor.bot.new_synonym_note", ui))
    lines.append(t("tailor.bot.preview_note", ui))
    return "\n\n".join(lines)


def _tailor_keyboard(proposal: TailorProposal, ui: Lang) -> InlineKeyboardMarkup | None:
    """[✅ Use this CV] / [↩️ Remove change N] (two per row) / [Discard]."""
    oid = proposal.offer_id
    prefix = f"{CV_TAILOR_ACTION}:{oid}"
    removals = [
        (t("tailor.bot.button.remove", ui, n=i + 1), f"{prefix}:rm:{i}")
        for i in range(min(len(proposal.changes), _TAILOR_REMOVE_BUTTONS))
    ]
    rows = [[(t("tailor.bot.button.use", ui), f"{prefix}:accept")]]
    rows += [removals[i : i + 2] for i in range(0, len(removals), 2)]
    rows.append([(t("tailor.bot.button.discard", ui), f"{prefix}:discard")])
    return _rows_markup(rows)


async def _cb_tailor(press: _Press, parts: list[str]) -> bool:
    """``cvt:<id>:<ROLE>:<lang>``: propose an adapted CV (nothing written)."""
    if len(parts) != 3 or (offer_id := _parse_id(parts[0])) is None:
        return False
    service, query, ui = press.service, press.query, press.ui
    await query.message.reply_text(t("tailor.bot.preparing", ui))
    try:
        proposal = await service.tailor_cv(offer_id, parts[1], parts[2], ui=ui)
    except ServiceError as exc:
        await query.message.reply_text(exc.text(ui))
        return True
    except Exception as exc:  # noqa: BLE001 — LLM/provider: the base CV stays
        _LOG.warning("adapted CV for offer %s failed: %s", offer_id, exc)
        await query.message.reply_text(t("tailor.bot.failed", ui))
        return True
    if not proposal.changes:
        service.discard_tailored(offer_id)
        await query.message.reply_text(t("tailor.bot.no_changes", ui))
        return True
    await query.message.reply_text(
        _tailor_preview(proposal, ui), reply_markup=_tailor_keyboard(proposal, ui)
    )
    return True


async def _cb_tailor_action(press: _Press, parts: list[str]) -> bool:
    """``cva:<id>:accept|discard|rm:<i>`` on a preview."""
    if len(parts) < 2 or (offer_id := _parse_id(parts[0])) is None:
        return False
    action = parts[1:]
    valid = action in (["accept"], ["discard"]) or (
        len(action) == 2 and action[0] == "rm" and action[1].isdigit()
    )
    if not valid:
        return False
    service, query, ui = press.service, press.query, press.ui
    if action == ["discard"]:
        service.discard_tailored(offer_id)
        await query.edit_message_text(t("tailor.bot.discarded", ui))
        return True
    try:
        if action == ["accept"]:
            pdf_path = await service.accept_tailored(offer_id)
        else:
            proposal = service.tailor_without(offer_id, int(action[1]), ui=ui)
    except ServiceError as exc:
        await query.message.reply_text(exc.text(ui))
        return True
    if action == ["accept"]:
        await query.edit_message_reply_markup(reply_markup=None)
        with pdf_path.open("rb") as document:
            await query.message.reply_document(
                document,
                filename=pdf_path.name,
                caption=t("tailor.bot.adapted_caption", ui),
            )
        return True
    if not proposal.changes:
        service.discard_tailored(offer_id)
        await query.edit_message_text(t("tailor.bot.nothing_left", ui))
        return True
    await query.edit_message_text(
        _tailor_preview(proposal, ui), reply_markup=_tailor_keyboard(proposal, ui)
    )
    return True


async def _cb_toggle(press: _Press, parts: list[str]) -> bool:
    """``lang`` and ``variant``: cycle the card's CV language or role."""
    if len(parts) != 3:
        return False
    service, families = press.service, press.families
    offer_id = int(parts[0])
    role, lang = normalize_role(parts[1], families=families), parts[2]
    if press.kind == "lang":
        lang = next_lang(role, lang, families=families)
    else:
        role = next_role(role, families=families)
    try:  # the "Search elsewhere" row survives the toggle
        offer = service.offer(offer_id)
    except RepositoryError:
        offer = None
    await press.query.edit_message_reply_markup(
        reply_markup=cv_keyboard(
            offer_id,
            role=role,
            lang=lang,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            families=families,
            ui_lang=press.ui,
            offer=offer,
        )
    )
    return True


async def _cb_discard(press: _Press, parts: list[str]) -> bool:
    if len(parts) != 1:
        return False
    offer_id = _parse_id(parts[0])
    if offer_id is not None:
        press.service.discard(offer_id)
        await press.query.edit_message_text(discard_message(offer_id, lang=press.ui))
    return True


async def _cb_disc(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    try:
        press.service.discard(offer_id)
    except ServiceError as exc:
        await press.query.message.reply_text(_err(exc, press.ui))
        return True
    await press.query.edit_message_text(
        discard_no_send_message(lang=press.ui), reply_markup=None
    )
    return True


async def _cb_sent(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    service, ui = press.service, press.ui
    try:
        service.mark_sent(offer_id)
    except ServiceError as exc:
        await press.query.message.reply_text(_err(exc, ui))
        return True
    await press.query.edit_message_text(
        sent_message(offer_id, service.offer(offer_id).match or 0, lang=ui),
        reply_markup=sent_outcome_keyboard(offer_id, lang=ui),
    )
    return True


async def _cb_outcome_menu(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    await _reply_outcome_menu(press.service, press.query.message, offer_id)
    return True


async def _cb_outcome(press: _Press, parts: list[str]) -> bool:
    if (
        len(parts) != 2
        or parts[1] not in OUTCOME_ESTADOS
        or (offer_id := _parse_id(parts[0])) is None
    ):
        return False
    await _apply_outcome(press.service, press.query, press.context, offer_id, parts[1])
    return True


async def _cb_gmail(press: _Press, parts: list[str]) -> bool:
    if len(parts) != 2 or parts[0] not in ("y", "o", "i"):
        return False
    await _gmail_callback(press.service, press.query, press.context, parts[0], parts[1])
    return True


async def _cb_calibration(press: _Press, parts: list[str]) -> bool:
    if not parts:
        return False
    await _calibration_callback(press.service, press.query, parts)
    return True


async def _cb_followup(press: _Press, parts: list[str]) -> bool:
    if (
        len(parts) != 2
        or parts[1] not in ("ok", "skip", "none")
        or (offer_id := _parse_id(parts[0])) is None
    ):
        return False
    await _followup_action(press.service, press.query, offer_id, parts[1])
    return True


async def _cb_salary(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    await _research_salary_and_reply(press.service, press.query.message, offer_id)
    return True


async def _cb_cover_letter(press: _Press, parts: list[str]) -> bool:
    if not parts or (offer_id := _parse_id(parts[0])) is None:
        return False
    service = press.service
    card = await _resend_card_below(service, press.query.message, offer_id)
    await _write_cover_letters_and_reply(
        service,
        card,
        offer_id,
        press.context.bot_data.setdefault(_LETTER_EXTRAS_KEY, {}),
    )
    return True


async def _cb_dismiss_letter(press: _Press, parts: list[str]) -> bool:
    if len(parts) != 1 or _parse_id(parts[0]) is None:
        return False
    await _dismiss_letter(press.query, press.context, press.ui)
    return True


async def _cb_choose_letter(press: _Press, parts: list[str]) -> bool:
    if (
        len(parts) != 3
        or parts[2] not in ("a", "b")
        or not parts[1].isdigit()
        or (offer_id := _parse_id(parts[0])) is None
    ):
        return False
    service, query, ui = press.service, press.query, press.ui
    try:
        service.choose_cover_letter(offer_id, parts[2], generation=int(parts[1]))
    except ServiceError as exc:
        await query.message.reply_text(_err(exc, ui))
        return True
    await query.message.reply_text(t("bot.letter_saved", ui))
    await _send_letter_pdf(
        query.message, lambda: service.cover_letter_pdf(offer_id), ui
    )
    return True


async def _cb_prep(press: _Press, parts: list[str]) -> bool:
    if (
        len(parts) not in (1, 2)
        or (offer_id := _parse_id(parts[0])) is None
        or (len(parts) != 1 and parts[1] not in STAGES)
    ):
        return False
    if len(parts) == 1:
        await _prep_stage_choice(press.service, press.query.message, offer_id)
    else:
        await _prepare_and_reply(press.service, press.query.message, offer_id, parts[1])
    return True


async def _cb_star(press: _Press, parts: list[str]) -> bool:
    await _star_callback(press.service, press.query, press.context, parts)
    return True


async def _cb_beacon(press: _Press, parts: list[str]) -> bool:
    if len(parts) != 1:
        return False
    if (
        reply := beacon_callback_reply(
            press.service, press.kind, parts[0], lang=press.ui
        )
    ) is not None:
        await press.query.edit_message_text(reply)
    return True


async def _cb_onboarding(press: _Press, parts: list[str]) -> bool:
    await onboarding.on_callback(
        press.query, press.context.user_data, _onboarding_env(press.context), parts
    )
    return True


async def _cb_ai_outage(press: _Press, parts: list[str]) -> bool:
    """``[🔄 Check now]`` probes at once; ``[▶️ Resume]`` ends the pause
    without probing. Either way a recovery re-drives the parked offers."""
    if parts not in (["check"], ["resume"]):
        return False
    service, query, ui = press.service, press.query, press.ui
    if parts == ["resume"]:
        pending: int | None = service.resume_ai()
    else:
        probe = await service.probe_ai(force=True)
        if probe.status == "none":
            await query.message.reply_text(t("notifier.ai.ok", ui))
            return True
        if probe.status != "recovered":
            outage = service.ai_outage()
            text = t("notifier.ai.still_down", ui)
            if outage is not None:
                text += "\n" + ai_outage_message(outage, lang=ui)
            await query.message.reply_text(
                text, reply_markup=ai_outage_keyboard(lang=ui)
            )
            return True
        pending = probe.pending
    handle = press.context.bot_data.get(bridge.BOT_DATA_KEY)
    if handle is not None:
        scheduler.redrive_retry_pending(service, handle)
    await query.message.reply_text(ai_back_message(pending or 0, lang=ui))
    return True


# Every inline-button prefix (the first ``:`` field of the callback data, see
# cva/notifier.py) and its handler. Payloads are a contract: cards stay in the
# chat for weeks, so a prefix is never renamed or reused.
CALLBACK_HANDLERS: dict[str, _CallbackHandler] = {
    "an": _cb_analyze,
    "cv": _cb_cv,
    "lang": _cb_toggle,
    "variant": _cb_toggle,
    "discard": _cb_discard,
    "disc": _cb_disc,
    "sent": _cb_sent,
    OUT: _cb_outcome_menu,
    OE: _cb_outcome,
    GM: _cb_gmail,
    CAL: _cb_calibration,
    FU: _cb_followup,
    SAL: _cb_salary,
    CL: _cb_cover_letter,
    CLD: _cb_dismiss_letter,
    CLC: _cb_choose_letter,
    PREP: _cb_prep,
    ST: _cb_star,
    BEACON_RESUME: _cb_beacon,
    BEACON_DISABLE: _cb_beacon,
    onboarding.PREFIX: _cb_onboarding,
    cv_catalog.PREFIX: _cb_cvs,
    CV_TAILOR: _cb_tailor,
    CV_TAILOR_ACTION: _cb_tailor_action,
    AI_OUTAGE: _cb_ai_outage,
}


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stateless callback router (notifier payload scheme): the prefix picks
    the handler in :data:`CALLBACK_HANDLERS`."""
    query = update.callback_query
    await query.answer()
    service = _service(context)
    ui = _lang(context)
    kind, parts = (query.data or "").split(":", 1)[0], (query.data or "").split(":")[1:]
    if query.message is not None and kind not in ("cv", CL):
        # The handlers below rewrite this card: stop tracking it so a later
        # "También en" edit can't clobber what the owner just did. (A "cv" or
        # cover-letter press re-sends the card instead and re-points the
        # stored row.)
        service.forget_card(query.message.chat_id, query.message.message_id)
    handler = CALLBACK_HANDLERS.get(kind)
    press = _Press(service, query, context, _families(context), ui, kind)
    if handler is None or not await handler(press, parts):
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


def _set_ui_language(context: ContextTypes.DEFAULT_TYPE, lang: Lang) -> None:
    """Switch the running bot to ``lang`` (the guided setup's language step);
    in setup mode the full app ``main`` starts next follows it too."""
    context.bot_data[_LANG_KEY] = lang
    settings = context.bot_data.get(_SETUP_SETTINGS_KEY)
    if isinstance(settings, Settings):
        context.bot_data[_SETUP_SETTINGS_KEY] = settings.model_copy(
            update={"ui_language": lang}
        )


def _set_enabled_portals(
    context: ContextTypes.DEFAULT_TYPE, portals: list[str]
) -> None:
    """Serve the portals the guided setup just wrote to config.local.toml
    without a restart: the running bridge's ``/config`` and, in setup mode,
    the settings the full app ``main`` starts next is built from."""
    handle = context.bot_data.get(bridge.BOT_DATA_KEY)
    if handle is not None:
        handle.set_enabled_portals(portals)
    settings = context.bot_data.get(_SETUP_SETTINGS_KEY)
    if isinstance(settings, Settings):
        context.bot_data[_SETUP_SETTINGS_KEY] = settings.model_copy(
            update={"enabled_portals": list(portals)}
        )


def _onboarding_env(context: ContextTypes.DEFAULT_TYPE) -> onboarding.Env:
    """What the guided setup needs from this app (setup mode or full)."""
    return onboarding.Env(
        lang=_lang(context),
        profile_dir=context.bot_data.get(_PROFILE_DIR_KEY),
        service=context.bot_data.get(_SERVICE_KEY),
        config_path=context.bot_data.get(_CONFIG_PATH_KEY),
        set_lang=lambda lang: _set_ui_language(context, lang),
        set_portals=lambda portals: _set_enabled_portals(context, portals),
    )


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A file sent to the bot: a base CV PDF while ``/setup`` asks for one
    (step 9); any other document is ignored, as before."""
    await onboarding.on_document(
        update.message, context.user_data, _onboarding_env(context)
    )


async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/setup`` (``/configurar``): start the guided profile setup
    (``cva/onboarding``); a pending paste, outcome note or STAR question is
    dropped so it cannot capture the answers."""
    context.user_data[_PASTE_KEY] = False
    _take_pending_note(context)
    _clear_star(context)
    await onboarding.start(update.message, context.user_data, _onboarding_env(context))


# --- setup mode (onboarding-v2 T1) ---------------------------------------------
# While the configured profile directory lacks a required file
# (cva/setup_mode.py) the bot runs this reduced app: no JobService is built, so
# nothing reads a profile, and least of all the legacy (owner) layout.


def _setup_missing(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, ...]:
    profile_dir: Path = context.bot_data[_PROFILE_DIR_KEY]
    return missing_profile_files(profile_dir)


async def _switch_if_complete(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, replay: bool
) -> bool:
    """Leave setup mode once the profile is complete AND loads: say so once
    and stop polling so ``main`` starts the full app (no manual restart).

    ``replay`` keeps ``update`` for ``main`` to replay in the full app (the
    owner update that merely saw the profile complete); the ``/setup``
    Confirm that completed it was already handled and is not replayed. A
    complete but malformed profile keeps setup mode, tells the owner which
    file failed (so a supervised service does not crash-loop on it) and
    returns True too: the update was answered. False: still incomplete.
    """
    if _setup_missing(context):
        return False
    lang = _lang(context)
    query = update.callback_query
    message = query.message if query is not None else update.message
    settings: Settings = context.bot_data[_SETUP_SETTINGS_KEY]
    try:
        service = JobService.from_settings(settings)
    except Exception as exc:  # noqa: BLE001 — any load failure keeps setup mode
        _LOG.warning("profile complete but not loadable: %s", exc)
        if query is not None and replay:
            await query.answer()
        if message is not None:
            error = describe_load_error(context.bot_data[_PROFILE_DIR_KEY], exc)
            await message.reply_text(t("bot.setup.broken", lang, error=error))
        return True
    context.bot_data[_SETUP_DONE_KEY] = True
    context.bot_data[_SETUP_SERVICE_KEY] = service
    context.bot_data[_SETUP_REPLAY_KEY] = [update] if replay else []
    if message is not None:
        await message.reply_text(t("bot.setup.ready", lang))
    context.application.stop_running()
    return True


async def setup_reload_check(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Group 0 of the setup app: switch to the full app once the profile is
    complete (:func:`_switch_if_complete`; normally the ``/setup`` Confirm
    already did, this catches a profile completed by hand).

    From the switch on, every owner update (the rest of the switching
    ``getUpdates`` batch, which PTB still dispatches here before polling
    stops) is kept for ``main`` to replay in the full app instead of being
    answered by the setup app.
    """
    if context.bot_data.get(_SETUP_DONE_KEY):
        context.bot_data[_SETUP_REPLAY_KEY].append(update)
        raise ApplicationHandlerStop
    if await _switch_if_complete(update, context, replay=True):
        raise ApplicationHandlerStop


async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/start`` in setup mode: the profile is not set up yet, use /setup
    (a complete but broken profile never gets here: the group-0 check names
    the file that failed)."""
    lang = _lang(context)
    await update.message.reply_text(
        t("bot.setup.not_set_up", lang, setup=command_name("configurar", lang))
    )


async def setup_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/help`` in setup mode: only the commands that work right now."""
    lang = _lang(context)
    await update.message.reply_text(
        t(
            "bot.setup.help",
            lang,
            setup=command_name("configurar", lang),
            connect=command_name("conectar", lang),
        )
    )


async def setup_onboarding_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """An ``ob:`` button in setup mode (the full app routes it through
    :data:`CALLBACK_HANDLERS`)."""
    query = update.callback_query
    await query.answer()
    await onboarding.on_callback(
        query,
        context.user_data,
        _onboarding_env(context),
        (query.data or "").split(":")[1:],
    )
    # A successful Confirm completes the profile: switch right away, not on
    # the owner's next message (the group-0 check ran before this handler).
    if not context.bot_data.get(_SETUP_DONE_KEY):
        await _switch_if_complete(update, context, replay=False)


async def setup_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text in setup mode: a /setup answer, else the not-ready reply."""
    if not await onboarding.on_text(
        update.message, context.user_data, _onboarding_env(context)
    ):
        await setup_not_ready(update, context)


async def setup_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A file in setup mode: a base CV PDF for ``/setup``, else the not-ready reply."""
    if not await onboarding.on_document(
        update.message, context.user_data, _onboarding_env(context)
    ):
        await setup_not_ready(update, context)


async def setup_not_ready(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Any other command, text or button in setup mode."""
    text = t("bot.setup.not_ready", _lang(context))
    query = update.callback_query
    if query is not None:
        await query.answer()
        await query.message.reply_text(text)
    elif update.message is not None:
        await update.message.reply_text(text)


def build_setup_app(settings: Settings) -> Application:
    """The setup-mode app: owner gate → completion check → /help, /start,
    /setup and its answers (``ob:`` buttons, typed text) → a "not set up yet"
    reply for everything else."""
    if settings.profile_dir is None:  # the legacy layout is never setup mode
        raise ValueError("setup mode needs an explicit profile_dir")
    app = (
        Application.builder().token(settings.telegram_token.get_secret_value()).build()
    )
    app.bot_data[_OWNER_KEY] = settings.owner_telegram_id
    app.bot_data[_LANG_KEY] = settings.ui_language
    app.bot_data[_PROFILE_DIR_KEY] = settings.profile_dir
    app.bot_data[_SETUP_SETTINGS_KEY] = settings
    app.bot_data[_CONFIG_PATH_KEY] = Path(LOCAL_TOML)
    app.add_error_handler(on_error)  # same backstop as the full app
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(TypeHandler(Update, setup_reload_check), group=0)
    setup_names = next(c for c in COMMANDS if c.name == "configurar")
    connect_names = next(c for c in COMMANDS if c.name == "conectar")
    app.add_handler(CommandHandler("start", setup_start), group=1)
    app.add_handler(CommandHandler("help", setup_help), group=1)
    app.add_handler(CommandHandler(list(setup_names.names), setup_command), group=1)
    app.add_handler(CommandHandler(list(connect_names.names), connect_command), group=1)
    app.add_handler(
        CallbackQueryHandler(
            setup_onboarding_callback, pattern=f"^{onboarding.PREFIX}:"
        ),
        group=1,
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, setup_text), group=1
    )
    app.add_handler(MessageHandler(filters.Document.ALL, setup_document), group=1)
    app.add_handler(MessageHandler(filters.ALL, setup_not_ready), group=1)
    app.add_handler(CallbackQueryHandler(setup_not_ready), group=1)
    return app


# --- owner claim (beta3-ux T1) -----------------------------------------------
# While owner_telegram_id is unset the bot runs this app first: every update is
# ignored with no reply (exactly like the owner gate) except a private
# ``/start <code>`` with the one-time code printed in the bot's terminal; its
# sender becomes the owner, written to config.local.toml.


def _claim_candidate(update: Any) -> str | None:
    """The code of a private-chat ``/start <code>`` message, else ``None``."""
    message = update.message
    text = getattr(message, "text", None)
    chat = getattr(message, "chat", None)
    if not isinstance(text, str) or getattr(chat, "type", None) != "private":
        return None
    parts = text.split()
    if len(parts) != 2:
        return None
    command = parts[0].split("@", 1)[0]
    return parts[1] if command == "/start" else None


async def claim_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1 of the claim app; always stops the update (nothing else runs).

    A wrong, expired or locked code gets no reply (no oracle); after too many
    wrong tries the code is dropped and only a restart prints a new one. The
    right code claims the bot once: the id is saved, the gate's owner is set
    at once, the claimer is told the next step and polling stops so ``main``
    starts the setup or full app. Owner updates of the same batch are kept for
    that app."""
    user = update.effective_user
    claimed = context.bot_data.get(_CLAIMED_KEY)
    if claimed is not None:
        if user is not None and user.id == claimed:
            context.bot_data[_CLAIM_REPLAY_KEY].append(update)
        raise ApplicationHandlerStop
    candidate = _claim_candidate(update)
    if candidate is None or user is None or getattr(user, "is_bot", False):
        raise ApplicationHandlerStop
    code: OneTimeCode = context.bot_data[_CLAIM_CODE_KEY]
    was_locked = code.locked
    if not code.verify(candidate):
        if code.locked and not was_locked:
            _LOG.warning(
                "too many wrong owner pairing codes: the code is disabled; "
                "restart the bot for a new one"
            )
        raise ApplicationHandlerStop
    lang = _lang(context)
    try:
        save_owner_id(context.bot_data[_CONFIG_PATH_KEY], int(user.id))
    except (OSError, ValueError) as exc:
        _LOG.error("could not save owner_telegram_id: %s", type(exc).__name__)
        await update.message.reply_text(
            t("bot.claim.save_failed", lang, error=type(exc).__name__)
        )
        raise ApplicationHandlerStop from None
    context.bot_data[_CLAIMED_KEY] = int(user.id)
    context.bot_data[_OWNER_KEY] = int(user.id)
    context.bot_data[_CLAIM_REPLAY_KEY] = []
    _LOG.info("owner claimed with the pairing code; owner_telegram_id saved")
    next_step = (
        t("bot.claim.next_setup", lang, setup=command_name("configurar", lang))
        if context.bot_data.get(_CLAIM_NEEDS_SETUP_KEY)
        else t("bot.claim.next_help", lang)
    )
    await update.message.reply_text(t("bot.claim.done", lang, next=next_step))
    context.application.stop_running()
    raise ApplicationHandlerStop


def build_claim_app(
    settings: Settings, code: OneTimeCode, config_path: Path | None = None
) -> Application:
    """The claim app: only :func:`claim_gate` (no command, no bridge)."""
    app = (
        Application.builder().token(settings.telegram_token.get_secret_value()).build()
    )
    app.bot_data[_OWNER_KEY] = 0
    app.bot_data[_LANG_KEY] = settings.ui_language
    app.bot_data[_CLAIM_CODE_KEY] = code
    app.bot_data[_CONFIG_PATH_KEY] = config_path or Path(LOCAL_TOML)
    app.bot_data[_CLAIM_NEEDS_SETUP_KEY] = needs_setup(settings)
    app.add_error_handler(on_error)
    app.add_handler(TypeHandler(Update, claim_gate), group=-1)
    return app


def announce_claim_code(code: str, lang: Lang, stream: TextIO | None = None) -> bool:
    """Show the claim code to the person at the terminal; True when shown.

    Written to the terminal only, never to the log: when ``stream`` (stderr
    by default) is not a terminal (a service whose output goes to a log
    file), the code is not printed at all and a warning says how to get it.
    """
    out = stream if stream is not None else sys.stderr
    if not out.isatty():
        _LOG.warning(
            "owner_telegram_id is not set: start the bot once in a terminal "
            "(`uv run bot.py`) to see the one-time owner pairing code, or set "
            "owner_telegram_id in config.local.toml"
        )
        return False
    out.write("\n" + t("bot.claim.terminal", lang, code=display(code)) + "\n\n")
    out.flush()
    return True


def register_claim_link(
    app: Application, code: str, lang: Lang, stream: TextIO | None = None
) -> None:
    """Once the bot knows its username, print the ``t.me`` deep link that
    sends ``/start <code>`` with one tap (terminal only, like the code)."""
    out = stream if stream is not None else sys.stderr

    async def _post_init(application: Application) -> None:
        try:
            username = application.bot.username
        except Exception:  # noqa: BLE001 — the link is a convenience
            return
        if username:
            link = f"https://t.me/{username}?start={code}"
            out.write(t("bot.claim.link", lang, link=link) + "\n\n")
            out.flush()

    app.post_init = _post_init


def run_claim(settings: Settings) -> tuple[int, list[Update]] | None:
    """Run the claim app until someone claims the bot: ``(owner id, owner
    updates to replay)``, or ``None`` when stopped first (Ctrl-C)."""
    code = OneTimeCode(ttl_seconds=CLAIM_CODE_TTL_SECONDS)
    secret = code.issue()
    app = build_claim_app(settings, code)
    if announce_claim_code(secret, settings.ui_language):
        register_claim_link(app, secret, settings.ui_language)
    # close_loop=False: the next app runs on the same event loop.
    app.run_polling(close_loop=False)
    owner = app.bot_data.get(_CLAIMED_KEY)
    if owner is None:
        return None
    return int(owner), list(app.bot_data.get(_CLAIM_REPLAY_KEY, []))


def register_menu(
    app: Application,
    lang: Lang,
    only: tuple[str, ...] | None = None,
    hidden: tuple[str, ...] = (),
) -> None:
    """Publish the ``lang`` command menu at startup (chains any existing
    ``post_init``); ``only`` narrows it (setup mode), ``hidden`` drops those
    commands (a legacy install).

    Call AFTER ``bridge.register``, which assigns ``app.post_init`` itself.
    A Telegram failure is logged, never fatal.
    """
    previous = app.post_init

    async def _post_init(application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                [
                    BotCommand(name, text)
                    for name, text in menu_commands(lang, only, hidden)
                ]
            )
        except Exception:  # noqa: BLE001 — the menu is cosmetic
            _LOG.warning("could not set the Telegram command menu", exc_info=True)
        if previous is not None:
            await previous(application)

    app.post_init = _post_init


def register_replay(app: Application, updates: list[Update]) -> None:
    """Queue ``updates`` (kept by the setup app at the switch) for the full
    app once it is initialised, in order and before any newly polled update
    (chains any existing ``post_init``). Each is rebuilt on the full app's
    bot: the setup app's bot is shut down by then."""
    if not updates:
        return
    previous = app.post_init

    async def _post_init(application: Application) -> None:
        if previous is not None:
            await previous(application)
        for update in updates:
            replayed = Update.de_json(update.to_dict(), application.bot)
            application.update_queue.put_nowait(replayed)

    app.post_init = _post_init


# Callback per ``cva.commands.COMMANDS`` entry (by ``Command.name``); every
# Spanish and English name and alias of the table is registered to it.
_COMMAND_HANDLERS: dict[
    str, Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
] = {
    "help": start_help,
    "analyze": analyze_command,
    "cv": cv_command,
    "estado": status_command,
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
    "busquedas": searches_command,
    "cartabase": cartabase_command,
    "configurar": setup_command,
    "conectar": connect_command,
    "cvs": cvs_command,
}


def build_app(settings: Settings, service: JobService) -> Application:
    """Wire handlers: whitelist gate (group -1) → commands → paste → callbacks."""
    app = (
        Application.builder().token(settings.telegram_token.get_secret_value()).build()
    )
    app.bot_data[_SERVICE_KEY] = service
    app.bot_data[_OWNER_KEY] = settings.owner_telegram_id
    app.bot_data[_LANG_KEY] = settings.ui_language
    app.bot_data[_PROFILE_DIR_KEY] = settings.profile_dir
    app.bot_data[_CONFIG_PATH_KEY] = Path(LOCAL_TOML)
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
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
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
    """Start the bot; a token Telegram rejects ends it with one plain line.

    The token is checked (``getMe``) before anything is printed (the claim
    code included) and is redacted from every log record; an
    :class:`InvalidToken` raised later (a token revoked while running) is
    reported the same way, never as a traceback that would show the token.
    """
    configure_logging()
    settings = Settings()
    token = settings.telegram_token.get_secret_value()
    telegram_token.install_redaction(token)
    lang = settings.ui_language
    status = telegram_token.check_token(token)
    if status == "rejected":
        _token_rejected(lang)
    if status == "unreachable":
        _LOG.warning("could not reach Telegram to check the bot token; polling retries")
    try:
        _run(settings)
    except InvalidToken:
        _token_rejected(lang)
    except Exception as exc:
        text = "".join(traceback.format_exception(exc))
        if not token or token not in text:
            raise
        # Last resort: the traceback would show the token; print it scrubbed.
        sys.stderr.write(telegram_token.redact(text, token))
        sys.stderr.flush()
        raise SystemExit(1) from None


def _token_rejected(lang: Lang) -> NoReturn:
    sys.stderr.write(t("bot.token_rejected", lang) + "\n")
    sys.stderr.flush()
    raise SystemExit(1)


def _run(settings: Settings) -> None:
    _LOG.info(describe_llm_setup(settings))
    service: JobService | None = None
    replay: list[Update] = []
    if settings.owner_telegram_id == 0:
        # No owner yet (beta3-ux T1): claim first, then setup mode / full app.
        _LOG.warning("owner_telegram_id is 0 (unset): waiting for the owner claim")
        claimed = run_claim(settings)
        if claimed is None:
            return  # stopped (Ctrl-C) before anyone claimed the bot
        owner, replay = claimed
        settings = settings.model_copy(update={"owner_telegram_id": owner})
    while needs_setup(settings):
        _LOG.warning(
            "profile not set up (missing: %s): starting in setup mode",
            ", ".join(missing_profile_files(settings.profile_dir)),
        )
        setup_app = build_setup_app(settings)
        bridge.register_setup(setup_app, settings)
        register_menu(setup_app, settings.ui_language, only=SETUP_COMMANDS)
        register_replay(setup_app, replay)  # the claim batch, after the menu
        # close_loop=False: the full app below runs on the same event loop.
        setup_app.run_polling(close_loop=False)
        if not setup_app.bot_data.get(_SETUP_DONE_KEY):
            return  # stopped (Ctrl-C) while still in setup mode
        service = setup_app.bot_data.get(_SETUP_SERVICE_KEY)
        replay = setup_app.bot_data.get(_SETUP_REPLAY_KEY, [])
        # The guided setup may have switched the UI language.
        settings = setup_app.bot_data.get(_SETUP_SETTINGS_KEY, settings)
    if service is None:
        service = JobService.from_settings(settings)
    app = build_app(settings, service)
    scheduler.register(
        app,
        service,
        settings.owner_telegram_id,
        gmail=build_gmail_sync(settings, service),
    )
    bridge.register(app, settings, service)
    # After the bridge: it overwrites app.post_init.
    register_menu(
        app,
        settings.ui_language,
        hidden=LEGACY_HIDDEN if settings.profile_dir is None else (),
    )
    register_replay(app, replay)  # the updates of the switch, after the menu
    app.run_polling()  # long polling — no public endpoint, TLS, or webhook


if __name__ == "__main__":
    main()
