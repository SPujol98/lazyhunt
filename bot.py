"""Telegram bot entrypoint (design D12, tasks 8.5 + 8b.5): PTB long polling.

Thin shell over the service seam; run_polling only. Single-owner whitelist
(unset owner 0 refuses everyone); WallError → paste prompt (D3); heartbeat =
cva.scheduler (one JobQueue job / 10 min, first tick = startup catch-up).
User-facing strings are Spanish on purpose; code and comments are English.
"""

import asyncio
import io
import logging
import time
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
from cva.commands import cv_usage, menu_commands
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, FamilyRegistry
from cva.gmail.client import GoogleGmailClient
from cva.gmail.sync import GmailSync, should_propose
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
    OUTCOME_NOTE_PROMPT,
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
    help_message,
    next_lang,
    next_role,
    no_cv_reply,
    normalize_role,
    not_found_message,
    offer_closed_message,
    offer_status_line,
    outcome_keyboard,
    outcome_message,
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
from cva.service import JobService, OfferClosedError, ServiceError
from cva.star import (
    ST,
    STAR_FIELDS,
)
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
_CARTABASE_USAGE = "Uso: /cartabase [es|en|ca] (por defecto es)"
_CARTABASE_NOTE = (
    "ℹ️ Carta genérica para portales, sin oferta concreta. Para una oferta, "
    "el botón ✉️ de su tarjeta la adapta a la empresa."
)


def is_owner(user_id: int | None, owner_id: int) -> bool:
    """Single-user whitelist gate; an unset owner (``0``) refuses everyone."""
    return bool(user_id) and bool(owner_id) and user_id == owner_id


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Group -1 guard: strangers are ignored with no reply and no side effects."""
    user = update.effective_user
    if not is_owner(user.id if user else None, int(context.bot_data[_OWNER_KEY])):
        raise ApplicationHandlerStop


async def start_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(help_message(_families(context)))


def _service(context: ContextTypes.DEFAULT_TYPE) -> JobService:
    return context.bot_data[_SERVICE_KEY]


def _families(context: ContextTypes.DEFAULT_TYPE | None) -> FamilyRegistry:
    """The profile's role families (role-families T5); the built-ins when no
    service is wired (e.g. /help before start-up)."""
    service = context.bot_data.get(_SERVICE_KEY) if context is not None else None
    prefs = getattr(service, "preferences", None)
    families = getattr(prefs, "families", None)
    return families if isinstance(families, FamilyRegistry) else BUILTIN_REGISTRY


def _apply_findings(service: JobService, offer_id: int) -> list[ApplyInstruction]:
    """Extra application steps in the offer text (pure; never blocks a card)."""
    return detect_apply_instructions(service.offer(offer_id).source_data or "")


async def _analyze_and_reply(
    service: JobService, reply, context: ContextTypes.DEFAULT_TYPE, offer_id: int
) -> None:
    """Analyze a persisted offer; ANY failure replies naming the cause and
    RE-ARMS the paste state so the owner can re-paste without /analyze."""
    try:
        result = await service.analyze(offer_id)
        offer = service.offer(offer_id)
        lang = infer_language(offer.source_data)
        role = _families(context).role_for(offer.perfil)
    except OfferClosedError:
        await reply.reply_text(offer_closed_message())
        return
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        context.user_data[_PASTE_KEY] = True
        await reply.reply_text(analyze_failure_message(exc))
        return
    no_cv = service.no_cv_label(offer_id)
    cv_needed = no_cv is None
    card = breakdown_message(
        result,
        offer_id,
        url=service.offer(offer_id).url_oferta,
        apply_findings=_apply_findings(service, offer_id),
        salary_line=service.card_extras(offer_id),
    )
    await reply.reply_text(
        card if no_cv is None else with_no_cv_line(card, no_cv),
        reply_markup=cv_keyboard(
            offer_id,
            role=role,
            lang=lang,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            cv_needed=cv_needed,
            families=_families(context),
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
        await reply.reply_text(paste_prompt())
    except NotFoundError:
        await reply.reply_text(not_found_message())
    return None


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        context.user_data[_PASTE_KEY] = True  # next plain message = pasted offer
        await update.message.reply_text(paste_prompt())
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
        await update.message.reply_text("El texto está vacío; pega la oferta de nuevo.")
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
        await update.message.reply_text(f"⚠️ {exc}")
        return
    await update.message.reply_text(f"📝 Nota guardada en #{offer_id}.")


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
    await reply.reply_text(star_question_message(position, total, queue[0]))


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
    await _reply_long(
        reply,
        f"{star_story_message(story)}\n\nGuardada tal cual la escribiste.",
        star_story_keyboard(story.id),
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
        await update.message.reply_text(
            star_question_message(
                int(pending["total"]) - len(pending["queue"]) + 1,
                int(pending["total"]),
                field,
            )
            + "\n(Escribe una respuesta o usa /skip.)"
        )
        return
    except RepositoryError as exc:
        _clear_star(context)
        await update.message.reply_text(f"⚠️ {exc}")
        return
    await _star_progress(context, update.message, pending, story)


async def star_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_star(context)
    await _star_list(_service(context), update.message)


async def _star_list(service: JobService, reply) -> None:
    stories = service.star_stories()
    if not stories:
        candidates = service.star_candidates()
        await reply.reply_text(
            star_candidates_message(candidates, first=True),
            reply_markup=star_candidates_keyboard(candidates),
        )
        return
    await _reply_long(reply, star_list_message(stories), star_list_keyboard(stories))


async def _star_callback(
    service: JobService, query, context: ContextTypes.DEFAULT_TYPE, parts: list[str]
) -> None:
    """``st:`` buttons: candidates, view, per-field edit, delete."""
    reply = query.message
    try:
        action = parts[0] if parts else ""
        if action == "l" and len(parts) == 1:
            _clear_star(context)
            await _star_list(service, reply)
        elif action == "add" and len(parts) == 1:
            candidates = service.star_candidates(unused_only=True)
            await reply.reply_text(
                star_candidates_message(candidates, first=False),
                reply_markup=star_candidates_keyboard(candidates)
                if candidates
                else None,
            )
        elif action == "new" and len(parts) == 2:
            story, created = service.start_star(parts[1])
            if not created:
                await _reply_long(
                    reply,
                    "Ya tienes una historia con esto.\n" + star_story_message(story),
                    star_story_keyboard(story.id),
                )
                return
            await _begin_star(
                context,
                reply,
                story,
                [f for f, _q in STAR_FIELDS],
                intro=star_intro_message(story, service.star_source_texts(story)),
            )
        elif action in ("v", "e", "d", "dy") and len(parts) == 2 and parts[1].isdigit():
            story = service.star_story(int(parts[1]))
            if action == "v":
                await _reply_long(
                    reply, star_story_message(story), star_story_keyboard(story.id)
                )
            elif action == "e":
                await reply.reply_text(
                    f"¿Qué campo de «{story.title}» quieres rehacer?",
                    reply_markup=star_edit_keyboard(story.id),
                )
            elif action == "d":
                await reply.reply_text(
                    f"¿Borrar «{story.title}»? No se puede deshacer.",
                    reply_markup=star_delete_keyboard(story.id),
                )
            else:
                service.delete_star(story.id)
                _clear_star(context)
                await reply.reply_text(f"🗑 «{story.title}» borrada.")
        elif (
            action == "f"
            and len(parts) == 3
            and parts[1].isdigit()
            and parts[2].isdigit()
        ):
            story = service.star_story(int(parts[1]))
            index = int(parts[2])
            if index >= len(STAR_FIELDS):
                await reply.reply_text("Acción no reconocida.")
                return
            await _begin_star(context, reply, story, [STAR_FIELDS[index][0]])
        else:
            await reply.reply_text("Acción no reconocida.")
    except (ServiceError, RepositoryError) as exc:
        await reply.reply_text(f"⚠️ {exc}")


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pending = _live_star(context)
    if pending is not None:
        service = _service(context)
        try:
            field = str(pending["queue"][0])
            kept = service.star_story(int(pending["id"]))
            story = service.skip_star(int(pending["id"]), field)
        except (ServiceError, RepositoryError) as exc:
            _clear_star(context)
            await update.message.reply_text(f"⚠️ {exc}")
            return
        if getattr(kept, field) != STAR_PLACEHOLDER:
            await update.message.reply_text(
                f"Sin cambios en {star_field_label(field)}."
            )
        await _star_progress(context, update.message, pending, story)
        return
    if _take_pending_note(context) is None:
        await update.message.reply_text("No hay nada que saltar.")
        return
    await update.message.reply_text("Vale, sin nota.")


async def _reply_outcome_menu(service: JobService, reply, offer_id: int) -> None:
    """Header + timeline, with the estado buttons once the offer is sent."""
    try:
        offer = service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(f"⚠️ {exc}")
        return
    text = outcome_message(offer, service.outcome_events(offer_id))
    if offer.fecha_envio is None:
        await reply.reply_text(
            f"{text}\nTodavía no está enviada: márcala con /sent {offer_id} primero."
        )
        return
    await reply.reply_text(text, reply_markup=outcome_keyboard(offer_id, offer.estado))


async def _followup_action(
    service: JobService, query, offer_id: int, action: str
) -> None:
    """Follow-up reminder buttons: log a manual follow-up, snooze, or give up.

    Buttons are stale once the offer left ``enviada``: nothing is changed.
    """
    try:
        estado = service.offer(offer_id).estado
        if estado != "enviada":
            await query.message.reply_text(
                f"Esta candidatura ya está en {estado}; no he cambiado nada."
            )
            return
        if action == "ok":
            number = service.record_followup_sent(offer_id)
            done = f"✅ Seguimiento {number} anotado en #{offer_id}."
        elif action == "skip":
            service.snooze_followup(offer_id)
            done = f"⏭️ #{offer_id}: te lo recordaré dentro de 7 días."
        else:
            service.record_outcome(offer_id, "sin_respuesta")
            done = f"🔕 #{offer_id} marcada como sin respuesta."
    except (ServiceError, RepositoryError) as exc:
        await query.message.reply_text(f"⚠️ {exc}")
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
    try:
        if service.calibration_needs_reason(offer_id, estado):
            await reply.reply_text(
                calibration_reason_message(),
                reply_markup=calibration_reason_keyboard(offer_id),
            )
            return
        example = service.propose_calibration(offer_id, estado)
    except (ServiceError, RepositoryError) as exc:
        _LOG.warning("calibration question skipped for #%s: %s", offer_id, exc)
        return
    if example is not None:
        await _send_calibration_question(reply, example)


async def _send_calibration_question(reply, example: CalibrationExample) -> None:
    await reply.reply_text(
        calibration_question_message(example),
        reply_markup=calibration_question_keyboard(example.id),
    )


_CAL_STALE = "Esa pregunta ya está respondida; no he cambiado nada."


async def _calibration_callback(service: JobService, query, parts: list[str]) -> None:
    """``cal:`` buttons: rejection reason, Sí/No on an example, deactivate."""
    reply = query.message
    action = parts[0]
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
                    await reply.reply_text(_CAL_STALE)
                    return
                await _send_calibration_question(reply, pending)
                return
            example = service.propose_calibration(offer_id, "rechazada", parts[2])
            if example is not None:
                await query.edit_message_reply_markup(reply_markup=None)
                await _send_calibration_question(reply, example)
            else:
                await query.edit_message_reply_markup(reply_markup=None)
                await reply.reply_text("Vale, no se usará para calibrar.")
        elif action in ("y", "n", "d") and len(parts) == 2:
            example_id = _parse_id(parts[1])
            if example_id is None:
                return
            await _calibration_answer(service, query, action, example_id)
    except (ServiceError, RepositoryError) as exc:
        await reply.reply_text(f"⚠️ {exc}")


async def _calibration_answer(
    service: JobService, query, action: str, example_id: int
) -> None:
    reply = query.message
    if action == "d":
        if not service.deactivate_calibration(example_id):
            await reply.reply_text(_CAL_STALE)
            return
        await reply.reply_text("🗑 Ejemplo desactivado: ya no calibra la nota.")
        return
    example = (
        service.approve_calibration(example_id)
        if action == "y"
        else service.decline_calibration(example_id)
    )
    if example is None:
        await reply.reply_text(_CAL_STALE)
        return
    done = (
        "✅ Hecho: esta oferta calibrará la nota (puedes quitarla en /calibracion)."
        if action == "y"
        else "Vale, no se usará para calibrar."
    )
    await query.edit_message_text(done, reply_markup=None)


async def calibracion_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    service = _service(context)
    examples = service.active_calibrations()
    await _reply_long(
        update.message,
        calibration_list_message(examples, service.calibration_summary()),
        calibration_list_keyboard(examples) if examples else None,
    )


async def outcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text("Uso: /outcome <id>")
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


def _parse_id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


async def cv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    families = _families(context)
    usage = f"Uso: {cv_usage(families)}"
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
        ),
        reply_markup=sent_keyboard(
            offer_id,
            cv_base=service.offer(offer_id).cv_base,
            cover_letter_asked=_cover_letter_asked(service, offer_id),
            families=families,
        ),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    args = context.args or []
    if args and (offer_id := _parse_id(args[0])) is not None:
        await update.message.reply_text(
            offer_status_line(
                service.offer(offer_id),
                also_on=service.also_on(offer_id),
                families=_families(context),
            )
        )
        return
    if args:
        await update.message.reply_text("Uso: /status [id]")
        return
    rows = service.recent_offers()
    families = _families(context)
    listing = "\n".join(offer_status_line(row, families=families) for row in rows)
    await update.message.reply_text(listing or "Todavía no hay ofertas registradas.")


async def sent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text("Uso: /sent <id>")
        return
    try:
        _service(context).mark_sent(offer_id)
    except ServiceError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    await update.message.reply_text(
        sent_message(offer_id, _service(context).offer(offer_id).match or 0),
        reply_markup=sent_outcome_keyboard(offer_id),
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path = _service(context).export()
    await update.message.reply_text(f"📊 Exportado a {path}")


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
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text("Uso: /rescatar <id>")
        return

    async def notify(text: str, keyboard: Any) -> SentMessage:
        sent = await update.message.reply_text(text, reply_markup=keyboard)
        return SentMessage(sent.chat_id, sent.message_id)

    try:
        await _service(context).rescue_offer(offer_id, notify)
    except ServiceError as exc:
        await update.message.reply_text(f"⚠️ {exc}")


async def keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    rows = _service(context).keywords(args[0] if args else None)
    if not rows:
        await update.message.reply_text("Sin keywords registradas todavía.")
        return
    lines = [f"{row.status or '·'} {row.keyword} ×{row.frequency}" for row in rows]
    await update.message.reply_text("\n".join(lines))


async def pausa_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _service(context).set_paused(True)
    await update.message.reply_text(
        "⏸ En pausa: sin búsquedas, análisis automático ni Gmail. "
        "/analyze sigue. /reanudar para volver."
    )


async def reanudar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _service(context).set_paused(False)
    await update.message.reply_text("▶️ Reanudado: todo vuelve a funcionar.")


async def beacons_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(beacons_list_message(_service(context).beacons()))


async def cartabase_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/cartabase [es|en|ca]``: the open letter (fixed text, no LLM call).

    The letter goes alone in its own plain-text message so it copies cleanly;
    a short note follows. The base is read on every call (edits apply live).
    """
    args = [arg.lower() for arg in context.args or []]
    language = args[0] if args else "es"
    if len(args) > 1 or language not in LETTER_LANGUAGES:
        await update.message.reply_text(_CARTABASE_USAGE)
        return
    path = context.bot_data.get(_LETTER_BASE_KEY) or resolve_profile_paths().letter_base
    try:
        bases = load_letter_bases(Path(path))
    except ProfileError as exc:
        await update.message.reply_text(f"⚠️ No pude leer la carta base: {exc}")
        return
    await update.message.reply_text(assemble_open_letter(bases[language]))
    await update.message.reply_text(_CARTABASE_NOTE)


async def beacon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        beacon_command_reply(_service(context), list(context.args or []))
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
    [✅ Usar esta] button. Any failure replies in Spanish and stores nothing."""
    try:
        await reply.reply_text("✍️ Escribiendo carta…")
        result = await service.cover_letters(offer_id)
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(f"⚠️ No pude escribir la carta: {failure_reason(exc)}")
        return
    await reply.reply_text(
        cover_letter_message("✉️ Carta", result.text),
        reply_markup=cover_letter_keyboard(offer_id, "a", result.generation),
    )
    await reply.reply_text(cover_letter_sources_message(result.facts))
    if result.warnings:
        await reply.reply_text(
            "⚠️ Ojo, revisa antes de usarla:\n"
            + "\n".join(f"- {warning}" for warning in result.warnings)
        )


async def _research_salary_and_reply(service: JobService, reply, offer_id: int) -> None:
    """ "💶 Investigar salario" press: cited range, cached same-day result, or a
    Spanish reason (no web-search provider / LLM failure). Never fabricates."""
    try:
        await reply.reply_text("🔎 Investigando salario en la web…")
        outcome = await service.research_salary(offer_id)
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(
            f"⚠️ La investigación salarial falló: {failure_reason(exc)}"
        )
        return
    await reply.reply_text(salary_research_message(outcome))


async def _prep_stage_choice(service: JobService, reply, offer_id: int) -> None:
    """Ask which stage to prepare, suggesting one from the offer's estado."""
    try:
        offer = service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(f"⚠️ {exc}")
        return
    suggested = default_stage(offer.estado)
    await reply.reply_text(
        f"🎯 Preparar entrevista de #{offer_id} · {offer.empresa or '(sin empresa)'}\n"
        f"¿Qué fase? Sugerida: {STAGE_LABEL[suggested]} (según el estado «{offer.estado}»).",
        reply_markup=prep_stage_keyboard(offer_id, suggested),
    )


async def _prepare_and_reply(
    service: JobService, reply, offer_id: int, stage: str
) -> None:
    """Stage press: progress, then the HTML pack + a short summary. Any failure
    replies in Spanish and sends no partial pack."""
    try:
        service.offer(offer_id)
    except RepositoryError as exc:
        await reply.reply_text(f"⚠️ {exc}")
        return
    if not service.claim_prep(offer_id, stage):  # sync: atomic with the check
        await reply.reply_text("⏳ Ya estoy preparando esa entrevista…")
        return
    try:
        await reply.reply_text("⏳ Preparando…")
        delivery = await service.prepare_interview(offer_id, stage, claimed=True)
        await reply.reply_document(
            io.BytesIO(delivery.html.encode("utf-8")), filename=delivery.filename
        )
        await reply.reply_text(delivery.summary)
    except ValidationError:
        await reply.reply_text(
            "⚠️ No pude preparar la entrevista: el LLM devolvió un formato "
            "inválido. Inténtalo de nuevo."
        )
    except Exception as exc:  # noqa: BLE001 — the chat IS the error surface
        await reply.reply_text(
            f"⚠️ No pude preparar la entrevista: {failure_reason(exc)}"
        )
    finally:
        service.release_prep(offer_id, stage)


async def prep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    offer_id = _parse_id(args[0]) if args else None
    if offer_id is None:
        await update.message.reply_text("Uso: /prep <id>")
        return
    await _prep_stage_choice(_service(context), update.message, offer_id)


async def _apply_outcome(
    service: JobService, query, context, offer_id: int, estado: str
) -> bool:
    """Record an outcome estado and run the follow-up flow (confirmation,
    calibration question, note prompt). Shared by the outcome menu buttons and
    the Gmail "Sí" button. True when the estado was recorded."""
    try:
        service.record_outcome(offer_id, estado)
    except (ServiceError, RepositoryError) as exc:
        await query.message.reply_text(f"⚠️ {exc}")
        return False
    context.user_data[_PASTE_KEY] = False
    _clear_star(context)
    context.user_data[_NOTE_KEY] = offer_id
    context.user_data[_NOTE_AT_KEY] = time.time()
    await query.message.reply_text(
        f"✅ #{offer_id} → {estado}.",
        reply_markup=prep_keyboard(offer_id) if estado in PREP_ESTADOS else None,
    )
    await _ask_calibration(service, query.message, offer_id, estado)
    await query.message.reply_text(OUTCOME_NOTE_PROMPT)
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
    row = service.gmail_proposal(message_id)
    if row is None:
        await query.message.reply_text("Esta propuesta ya no existe.")
        return
    if row.decision != "pending":
        await query.message.reply_text("Esta propuesta ya está resuelta.")
        return
    if action == "i":
        if service.resolve_gmail_proposal(message_id, "ignored", expect="pending"):
            await _clear_markup(query)
            await query.message.reply_text("Vale, la ignoro.")
        return
    if action == "o":
        if service.resolve_gmail_proposal(message_id, "other", expect="pending"):
            await _reply_outcome_menu(service, query.message, row.offer_id)
            await _clear_markup(query)
        return
    try:
        current = service.offer(row.offer_id).estado
    except RepositoryError as exc:
        await query.message.reply_text(f"⚠️ {exc}")
        return
    if not should_propose(current, row.proposed_estado):
        service.resolve_gmail_proposal(message_id, "stale", expect="pending")
        await _clear_markup(query)
        await query.message.reply_text(
            f"Esta candidatura ya está en {current}; no he cambiado nada."
        )
        return
    if not service.resolve_gmail_proposal(message_id, "accepted", expect="pending"):
        await query.message.reply_text("Esta propuesta ya está resuelta.")
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
            await query.message.reply_text(no_cv_reply(no_cv))
            return
        if len(parts) > 1 and families.get(parts[1]) is None:
            # An old button of a family the profile no longer declares.
            await query.message.reply_text(
                unknown_role_message(parts[1], families.default.label)
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
            ),
            reply_markup=sent_keyboard(
                offer_id,
                cv_base=service.offer(offer_id).cv_base,
                cover_letter_asked=_cover_letter_asked(service, offer_id),
                families=families,
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
            )
        )
    elif kind == "discard" and len(parts) == 1:
        offer_id = _parse_id(parts[0])
        if offer_id is not None:
            service.discard(offer_id)
            await query.edit_message_text(discard_message(offer_id))
    elif kind == "disc" and parts and (offer_id := _parse_id(parts[0])) is not None:
        try:
            service.discard(offer_id)
        except ServiceError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
            return
        await query.edit_message_text(discard_no_send_message(), reply_markup=None)
    elif kind == "sent" and parts and (offer_id := _parse_id(parts[0])) is not None:
        try:
            service.mark_sent(offer_id)
        except ServiceError as exc:
            await query.message.reply_text(f"⚠️ {exc}")
            return
        await query.edit_message_text(
            sent_message(offer_id, service.offer(offer_id).match or 0),
            reply_markup=sent_outcome_keyboard(offer_id),
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
            await query.message.reply_text(f"⚠️ {exc}")
            return
        await query.message.reply_text("Guardada la carta en la oferta")
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
        if (reply := beacon_callback_reply(service, kind, parts[0])) is not None:
            await query.edit_message_text(reply)
    else:
        await query.message.reply_text("Acción no reconocida.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PTB backstop: log + best-effort owner notification; never crashes."""
    error = context.error
    _LOG.error("unhandled handler error: %s", error, exc_info=error)
    try:
        await context.bot.send_message(
            int(context.bot_data[_OWNER_KEY]),
            backstop_error_message(failure_reason(error)),
        )
    except Exception:  # noqa: BLE001 — a failing backstop must not crash either
        _LOG.exception("could not notify the owner about the failure")


def register_menu(app: Application) -> None:
    """Publish the command menu at startup (chains any existing ``post_init``).

    Call AFTER ``bridge.register``, which assigns ``app.post_init`` itself.
    A Telegram failure is logged, never fatal.
    """
    previous = app.post_init

    async def _post_init(application: Application) -> None:
        try:
            await application.bot.set_my_commands(
                [BotCommand(name, text) for name, text in menu_commands()]
            )
        except Exception:  # noqa: BLE001 — the menu is cosmetic
            _LOG.warning("could not set the Telegram command menu", exc_info=True)
        if previous is not None:
            await previous(application)

    app.post_init = _post_init


def build_app(settings: Settings, service: JobService) -> Application:
    """Wire handlers: whitelist gate (group -1) → commands → paste → callbacks."""
    app = (
        Application.builder().token(settings.telegram_token.get_secret_value()).build()
    )
    app.bot_data[_SERVICE_KEY] = service
    app.bot_data[_OWNER_KEY] = settings.owner_telegram_id
    app.bot_data[_LETTER_BASE_KEY] = resolve_profile_paths(
        settings.profile_dir
    ).letter_base
    app.add_error_handler(on_error)
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CommandHandler(["start", "help"], start_help))
    app.add_handler(CommandHandler("analyze", analyze_command))
    app.add_handler(CommandHandler("cv", cv_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sent", sent_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CommandHandler("rescatar", rescatar_command))
    app.add_handler(CommandHandler("outcome", outcome_command))
    app.add_handler(CommandHandler("calibracion", calibracion_command))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CommandHandler("star", star_command))
    app.add_handler(CommandHandler("prep", prep_command))
    app.add_handler(CommandHandler("keywords", keywords_command))
    app.add_handler(CommandHandler("pausa", pausa_command))
    app.add_handler(CommandHandler("reanudar", reanudar_command))
    app.add_handler(CommandHandler("beacons", beacons_command))
    app.add_handler(CommandHandler("beacon", beacon_command))
    app.add_handler(CommandHandler(["cartabase", "base"], cartabase_command))
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
        ),
        llm=build_llm_client(settings),
        poll_minutes=settings.gmail_poll_minutes,
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
    register_menu(app)  # after the bridge: it overwrites app.post_init
    app.run_polling()  # long polling — no public endpoint, TLS, or webhook


if __name__ == "__main__":
    main()
