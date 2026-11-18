"""Notifier: PTB message + inline-keyboard builders (design D12, tasks 8.3–8.4).

Pure functions returning strings and ``telegram`` keyboard objects — no I/O,
no network, table-testable. Callback payloads are stateless strings (design
``an:`` / ``cv:`` / ``sent:`` scheme; toggles carry their current selection so
no bot-side session state is needed):

- ``an:{job_id}``                        analyze a beacon-reported job id
- ``cv:{offer_id}:{AI|BACKEND}:{es|en|ca}``  generate the CV (2-part ``cv:{id}``
  tolerated by the bot parser, defaults AI/es)
- ``lang:{offer_id}:{role}:{lang}``      flip the language toggle
- ``variant:{offer_id}:{role}:{lang}``   flip the AI/BACKEND toggle
- ``discard:{offer_id}``                 discard without generating a PDF
- ``disc:{offer_id}``                    post-generation discard (no send recorded)
- ``sent:{offer_id}``                    mark the offer as sent
- ``sal:{offer_id}``                     on-demand salary web research
- ``cl:{offer_id}``                      write the two cover-letter versions
- ``clc:{offer_id}:{gen}:{a|b}``         store the chosen cover-letter version

LANGUAGE NOTE: user-facing message strings are Spanish on purpose — the bot's
end user is the Spanish-speaking owner. Module code, comments, and identifiers
are English (artifact language contract).
"""

import html
import json
from collections.abc import Sequence
from contextlib import suppress
from datetime import date
from pathlib import Path
from re import search
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cva.beacons import BeaconPausedAlert
from cva.commands import help_text
from cva.llm import LlmResponseError
from cva.match import MatchResult
from cva.match.apply_instructions import KIND_EMOJI, ApplyInstruction
from cva.match.interview_prep import PREP_ESTADOS, STAGE_LABEL, CompanyFact
from cva.match.judge import OfferJudgment
from cva.match.salary_reference import eur_text
from cva.match.salary_research import SalaryOutcome
from cva.tracker import (
    OUTCOME_ESTADOS,
    BeaconRow,
    OfferEvent,
    OfferRow,
    RepositoryError,
)

if TYPE_CHECKING:
    from cva.gmail.sync import Proposal as GmailProposal
    from cva.service import JobService

AN = "an"
CV = "cv"
LANG = "lang"
VARIANT = "variant"
DISCARD = "discard"
DISC = "disc"
SENT = "sent"
SAL = "sal"
CL = "cl"
CLC = "clc"
OUT = "out"  # open the outcome menu of a sent offer
OE = "oe"  # record an outcome estado
GM = "gm"  # Gmail proposal actions: gm:<y|o|i>:<gmail message id>
FU = "fu"  # follow-up reminder actions: ok | skip | none
PREP = "prep"  # interview prep: prep:<id> (stage choice) | prep:<id>:<stage>
BEACON_RESUME = "beacon_resume"
BEACON_DISABLE = "beacon_disable"

# Language toggle cycle: AI offers the full ES→EN→CA loop; Backend has no
# Catalan CV, so its cycle is the ES↔EN pair only. Unknown values reset to "es".
_AI_LANG_CYCLE: dict[str, str] = {"es": "en", "en": "ca", "ca": "es"}

# Role toggle cycle (fixed-base-cvs): the bot offers AI and Backend only.
_ROLE_CYCLE: dict[str, str] = {"AI": "BACKEND", "BACKEND": "AI"}
_NO_CATALAN_ROLES: Final[frozenset[str]] = frozenset({"BACKEND"})
# Data Scientist CVs are manual-only now; cards sent earlier still carry DS in
# their callback data, so DS is mapped to AI instead of crashing.
_LEGACY_ROLES: Final[dict[str, str]] = {"DS": "AI"}
_ROLE_BUTTON_LABEL: Final[dict[str, str]] = {"BACKEND": "Backend"}

# Round-summary amendment: max discard lines listed before the "… y N más".
_ROUND_DISCARD_LINES: Final[int] = 25
# Max entries listed in each walled / closed / duplicates block of the round
# summary, and in the standalone duplicates notice, before the "… y N más".
_ROUND_BLOCK_LINES: Final[int] = 5


def next_lang(role: str, lang: str) -> str:
    """Next language of the toggle cycle for ``role`` (pure, table-tested)."""
    if normalize_role(role) in _NO_CATALAN_ROLES:
        return "en" if lang == "es" else "es"
    return _AI_LANG_CYCLE.get(lang, "es")


def normalize_role(role: str) -> str:
    """Map a role from (possibly old) callback data onto the bot's roles."""
    return _LEGACY_ROLES.get(role, role)


def next_role(role: str) -> str:
    """Next role of the variant toggle (unknown values reset to AI)."""
    return _ROLE_CYCLE.get(normalize_role(role), "AI")


def canonical_job_url(job_id: str) -> str:
    """Canonical LinkedIn job link for notifications (telegram-bot spec)."""
    return f"https://linkedin.com/jobs/view/{job_id}"


def new_job_message(
    *,
    title: str | None,
    company: str | None,
    age: str,
    job_id: str,
    threshold: int,
    url: str | None = None,
) -> str:
    """New-job card: title · company · age · canonical link + threshold context.

    ``url`` (infojobs-source, Card-content requirement): the per-source
    canonical URL line; ``None`` keeps the legacy LinkedIn-shape default
    (``canonical_job_url``) — every existing caller stays byte-identical.
    """
    return (
        f"🔔 {title or '(sin título)'} · {company or '(sin empresa)'} · {age}\n"
        f"{url if url is not None else canonical_job_url(job_id)}\n"
        f"Umbral Top-Choice: {threshold}"
    )


class SentMessage(NamedTuple):
    """Where a card landed in Telegram: what ``notify`` reports back so the
    service can remember it and the bridge can edit it later."""

    chat_id: int
    message_id: int


ALSO_ON_PREFIX: Final[str] = "📍 También en: "


def with_also_on(
    text: str, labels: Sequence[str], *, parse_mode: str | None = None
) -> str:
    """``text`` ending with ONE ``📍 También en: A, B`` line (none when no labels).

    Any earlier También-en line is replaced, never stacked. Labels are
    HTML-escaped when the card is sent with ``parse_mode="HTML"``.
    """
    lines = text.split("\n")
    if lines and lines[-1].startswith(ALSO_ON_PREFIX):
        lines.pop()
    if labels:
        shown = [html.escape(x) if parse_mode == "HTML" else x for x in labels]
        lines.append(ALSO_ON_PREFIX + ", ".join(shown))
    return "\n".join(lines)


def keyboard_to_json(keyboard: Any) -> str | None:
    """Serialize an inline keyboard so a card can be edited without losing it."""
    if keyboard is None or not hasattr(keyboard, "to_dict"):
        return None
    return json.dumps(keyboard.to_dict())


def keyboard_from_json(text: str | None) -> InlineKeyboardMarkup | None:
    """Inverse of :func:`keyboard_to_json`; ``None`` for absent/corrupt data."""
    if not text:
        return None
    try:
        return InlineKeyboardMarkup.de_json(json.loads(text))
    except (ValueError, TypeError, KeyError):
        return None


def new_job_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Notification keyboard: the single ``[Analizar]`` action."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔎 Analizar", callback_data=f"{AN}:{job_id}")]]
    )


_TELEGRAM_LIMIT: Final[int] = 4096
_APPLY_WARNING_HEADER: Final[str] = "⚠️ Ojo: esta oferta pide algo más al aplicar"


def apply_warning_block(findings: Sequence[ApplyInstruction]) -> str:
    """Warning block for extra application steps; ``""`` when none.

    Cards are plain text (no ``parse_mode``), so snippets are NOT escaped.
    """
    if not findings:
        return ""
    lines = [_APPLY_WARNING_HEADER]
    for item in findings:
        value = f": {item.value}" if item.value else ""
        lines.append(
            f"• {KIND_EMOJI.get(item.kind, '•')} {item.label}{value} — «{item.snippet}»"
        )
    return "\n".join(lines)


def with_apply_warnings(text: str, findings: Sequence[ApplyInstruction]) -> str:
    """Append the warning block after a blank line, within Telegram's limit.

    Returns ``text`` byte-identical when there are no findings. Whole bullet
    lines are dropped from the end until the card fits; if not even the
    header fits, the card is returned unchanged.
    """
    block = apply_warning_block(findings)
    if not block:
        return text
    lines = block.split("\n")
    while len(lines) > 1 and len(text) + 2 + len("\n".join(lines)) > _TELEGRAM_LIMIT:
        lines.pop()
    if len(lines) == 1:
        return text
    return f"{text}\n\n" + "\n".join(lines)


def with_salary_line(text: str, line: str) -> str:
    """Append the salary line after a blank line, within Telegram's limit.

    ``text`` is returned byte-identical when there is no line or no room.
    """
    if not line or len(text) + 2 + len(line) > _TELEGRAM_LIMIT:
        return text
    return f"{text}\n\n{line}"


_DEADLINE_CARD_DAYS: Final[int] = 7
_STALE_POSTING_DAYS: Final[int] = 30


def _days_word(days: int) -> str:
    return "1 día" if days == 1 else f"{days} días"


def date_card_lines(
    fecha_publicacion: str | None, fecha_limite: str | None, today: date
) -> str:
    """Urgency lines for a card (``""`` when none applies).

    Deadline 0-7 days away: "⏰ Cierra hoy" / "⏰ Cierra en N días (dd/mm)";
    posted more than 30 days ago: "🕰️ Publicada hace N días (puede estar
    cerrada)". Past deadlines produce no line (they are not an alert).
    """
    lines: list[str] = []
    if fecha_limite:
        with suppress(ValueError):
            deadline = date.fromisoformat(fecha_limite)
            left = (deadline - today).days
            if left == 0:
                lines.append("⏰ Cierra hoy")
            elif 0 < left <= _DEADLINE_CARD_DAYS:
                lines.append(f"⏰ Cierra en {_days_word(left)} ({deadline:%d/%m})")
    if fecha_publicacion:
        with suppress(ValueError):
            age = (today - date.fromisoformat(fecha_publicacion)).days
            if age > _STALE_POSTING_DAYS:
                lines.append(f"🕰️ Publicada hace {age} días (puede estar cerrada)")
    return "\n".join(lines)


_DEADLINE_ALERT_MAX_LINES: Final[int] = 15


def _deadline_entry(row: OfferRow, today: date) -> str:
    when = "hoy"
    with suppress(ValueError, TypeError):
        left = (date.fromisoformat(row.fecha_limite or "") - today).days
        when = "hoy" if left <= 0 else f"en {_days_word(left)}"
    entry = (
        f"• {row.posicion or '(sin título)'} · {row.empresa or '(sin empresa)'}"
        f" — cierra {when}"
    )
    if row.url_oferta:
        entry += f"\n  {row.url_oferta}"
    return entry


def deadline_alert_batch(rows: Sequence[OfferRow], today: date) -> tuple[str, int]:
    """Owner alert text plus how many leading rows it actually lists.

    At most ``_DEADLINE_ALERT_MAX_LINES`` entries and always under the Telegram
    limit (a "… y N más" footer is reserved). The caller marks only the listed
    rows as alerted; the rest go out in the next message.
    """
    head = "⏰ Ofertas por cerrar que aún no has enviado:"
    footer_room = len("\n… y 9999 más")
    lines = [head]
    size = len(head)
    listed = 0
    for row in rows[:_DEADLINE_ALERT_MAX_LINES]:
        entry = _deadline_entry(row, today)
        if listed and size + 1 + len(entry) + footer_room > _TELEGRAM_LIMIT:
            break
        lines.append(entry[: _TELEGRAM_LIMIT - size - 1 - footer_room])
        size += 1 + len(lines[-1])
        listed += 1
    extra = len(rows) - listed
    if extra > 0:
        lines.append(f"… y {extra} más")
    return "\n".join(lines), listed


def deadline_alert_message(rows: Sequence[OfferRow], today: date) -> str:
    """Daily owner alert: un-sent offers closing soon (never past)."""
    return deadline_alert_batch(rows, today)[0]


def with_card_extras(
    text: str, salary_line: str, findings: Sequence[ApplyInstruction]
) -> str:
    """Salary line first (short, fixed), then the droppable apply warnings."""
    return with_apply_warnings(with_salary_line(text, salary_line), findings)


def breakdown_message(
    result: MatchResult,
    offer_id: int,
    *,
    url: str | None = None,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
) -> str:
    """Score vs threshold + per-requirement ✓/~/✗ lines with literal evidence.

    ``url`` (notification-pacing D6): the canonical offer URL line appended
    right after the header; ``None`` keeps the legacy shape (no URL line).
    """
    verdict = (
        "supera el umbral ✅"
        if result.score >= result.threshold
        else "no supera el umbral (Top-Choice) ❌"
    )
    lines = [
        (
            f"📊 Oferta #{offer_id} — puntuación {result.score}/100 "
            f"(umbral {result.threshold}): {verdict}"
        ),
    ]
    if url is not None:
        lines.append(url)
    lines.append("")
    for match in result.breakdown:
        weight = "imprescindible" if match.requirement.must_have else "deseable"
        lines.append(
            f"{match.status} {match.requirement.name} ({weight}) — "
            f"«{match.requirement.evidence}»"
        )
    return with_card_extras("\n".join(lines), salary_line, apply_findings)


def borderline_message(
    *,
    score: int,
    threshold: int,
    title: str | None,
    company: str | None,
    url: str,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
) -> str:
    """Gray-zone card: compact near-miss notice for manual review.

    Deliberately NO ✓/~/✗ breakdown — the borderline card flags
    "close to Top-Choice" (score in ``[threshold - gray_zone, threshold)``);
    the full evidence stays exclusive to the breakdown card.
    """
    text = (
        f"🟡 {title or '(sin título)'} · {company or '(sin empresa)'} — "
        f"puntuación {score}/100 (umbral {threshold})\n"
        f"{url}\n"
        "Cerca del umbral Top-Choice: revísala y genera tu CV si encaja."
    )
    return with_card_extras(text, salary_line, apply_findings)


class DiscardedOffer(NamedTuple):
    """One below-gate offer line in the round summary (design D4)."""

    score: int
    title: str | None
    company: str | None
    url: str | None


class WalledOffer(NamedTuple):
    """One hard-walled offer line in the round summary (stack wall or gate)."""

    reason: str
    title: str | None
    company: str | None
    url: str | None


class ClosedOffer(NamedTuple):
    """One closed offer line in the round summary (discarded without analysis)."""

    title: str | None
    company: str | None
    url: str | None


class DuplicateOffer(NamedTuple):
    """One cross-source duplicate line: stored discarded, linked to the original."""

    title: str | None
    company: str | None
    original_id: int
    url: str | None


def _offer_label(title: str | None, url: str | None) -> str:
    safe = html.escape(title or "(sin título)", quote=True)
    if url is None:
        return safe
    return f'<a href="{html.escape(url, quote=True)}">{safe}</a>'


def round_summary_message(
    *,
    beacon: str,
    analyzed: int,
    above: int,
    gray: int,
    below: int,
    pending: int,
    discarded: list[DiscardedOffer],
    walled: int = 0,
    walled_offers: Sequence[WalledOffer] = (),
    closed: int = 0,
    closed_offers: Sequence[ClosedOffer] = (),
    duplicates: int = 0,
    duplicate_offers: Sequence[DuplicateOffer] = (),
) -> str:
    """Per-payload round summary (round-summary amendment; HTML-mode text).

    ONE compact recap after the paced queue finishes a payload's batch of new
    jobs — ADDITIONAL to the individual cards, never a replacement. ``pending``
    covers failed analyses (retry-pending or exhausted, both ♻️ for the
    owner); ``discarded`` lists the below-gate offers capped at
    ``_ROUND_DISCARD_LINES`` entries, then one "… y N más" line.

    Sent with ``parse_mode="HTML"`` (design D4): every dynamic value
    (beacon, title, company, url) passes through
    ``html.escape(..., quote=True)``. A discarded offer with a ``url``
    renders as a clickable ``<a href="...">title</a>`` anchor; one without a
    ``url`` renders the escaped title with no anchor.
    """
    safe_beacon = html.escape(beacon, quote=True)
    lines = [f"📊 Ronda '{safe_beacon}': {analyzed} analizadas"]
    lines.append(
        f"🟢 {above} sobre el umbral · 🟡 {gray} en zona gris · "
        f"⚪ {below} descartadas por umbral"
    )
    if walled > 0:
        lines.append(f"🧱 {walled} descartadas por muro (stack, elegibilidad o idioma)")
        for wall in walled_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(wall.company or "(sin empresa)", quote=True)
            reason = html.escape(wall.reason, quote=True)
            lines.append(
                f"· {reason} — {_offer_label(wall.title, wall.url)} @ {company}"
            )
        hidden_walls = len(walled_offers) - _ROUND_BLOCK_LINES
        if hidden_walls > 0:
            lines.append(f"… y {hidden_walls} más")
    if closed > 0:
        lines.append(f"🔒 {closed} ofertas cerradas (descartadas sin analizar)")
        for gone in closed_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(gone.company or "(sin empresa)", quote=True)
            lines.append(f"· {_offer_label(gone.title, gone.url)} @ {company}")
        hidden_closed = len(closed_offers) - _ROUND_BLOCK_LINES
        if hidden_closed > 0:
            lines.append(f"… y {hidden_closed} más")
    if duplicates > 0:
        lines.append(f"♻️ {duplicates} duplicadas (ya vistas en otra fuente)")
        for dup in duplicate_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(dup.company or "(sin empresa)", quote=True)
            lines.append(
                f"· {_offer_label(dup.title, dup.url)} @ {company} → #{dup.original_id}"
            )
        hidden_dups = duplicates - min(len(duplicate_offers), _ROUND_BLOCK_LINES)
        if hidden_dups > 0:
            lines.append(f"… y {hidden_dups} más")
    if pending > 0:
        lines.append(f"♻️ {pending} pendientes de reintento")
    if below > 0:
        lines.append("Descartadas (score — título — empresa):")
        shown = discarded[:_ROUND_DISCARD_LINES]
        for offer in shown:
            title = html.escape(offer.title or "(sin título)", quote=True)
            company = html.escape(offer.company or "(sin empresa)", quote=True)
            label = (
                f'<a href="{html.escape(offer.url, quote=True)}">{title}</a>'
                if offer.url is not None
                else title
            )
            lines.append(f"· {offer.score}/100 — {label} @ {company}")
        hidden = len(discarded) - len(shown)
        if hidden > 0:
            lines.append(f"… y {hidden} más")
    return "\n".join(lines)


_VERDICT_EMOJI: Final[dict[str, str]] = {
    "apply": "✅",
    "apply_with_caveats": "🟡",
    "discard": "⛔",
}

_VERDICT_LABEL: Final[dict[str, str]] = {
    "apply": "aplicar",
    "apply_with_caveats": "aplicar con matices",
    "discard": "descartar",
}

_JUDGE_FIT_LINES: Final[int] = 3


def judge_breakdown_message(
    result: MatchResult,
    judgment: OfferJudgment | None,
    offer_id: int,
    *,
    url: str | None = None,
    judge_fallback: bool = False,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
) -> str:
    """Judge card (``match_mode`` hybrid/judge, design llm-offer-judge).

    ``judgment=None`` renders EXACTLY :func:`breakdown_message` (keyword-only
    ``match_mode``, or a judge call that failed this analysis — ``analyze``
    already fell back to the keyword score for ``result.score`` in that
    case) — ``judge_fallback=True`` then appends one warning line so the
    owner knows the score shown is keyword-only for this offer, not a judge
    verdict. When a judgment IS present: score/verdict emoji + core role +
    top ``_JUDGE_FIT_LINES`` fit items + walls (hard first) + suggested CV
    base + rationale, followed by the existing keyword breakdown for
    transparency (the owner can still see every ✓/~/✗ line).
    """
    if judgment is None:
        text = breakdown_message(result, offer_id, url=url)
        if judge_fallback:
            text += "\n⚠️ El juicio LLM falló; se muestra el score por palabras clave."
        if result.stack_wall:
            text += f"\n🚫 Muro: {result.stack_wall}"
        for gate in result.gates:
            if gate.verdict == "FAIL":
                text += f"\n🚫 Muro: {gate.reason}"
            elif gate.verdict == "FLAG":
                text += f"\n⚠️ {gate.reason}"
        return with_card_extras(text, salary_line, apply_findings)
    emoji = _VERDICT_EMOJI.get(judgment.verdict, "❔")
    label = _VERDICT_LABEL.get(judgment.verdict, judgment.verdict)
    lines = [
        f"{emoji} Oferta #{offer_id} — puntuación {judgment.score}/100 ({label})",
    ]
    if url is not None:
        lines.append(url)
    lines.append(f"Rol principal: {judgment.core_role}")
    lines.append(f"CV base sugerido: {judgment.cv_base}")
    if judgment.fit:
        lines.append("Fit principal:")
        for item in judgment.fit[:_JUDGE_FIT_LINES]:
            lines.append(f"· {item.requirement} ({', '.join(item.claim_ids)})")
    if judgment.walls:
        lines.append("Muros:")
        for wall in sorted(judgment.walls, key=lambda w: not w.hard):
            marker = "🚫" if wall.hard else "⚠️"
            lines.append(f"{marker} {wall.text}")
    lines.append("")
    lines.append(judgment.rationale)
    lines.append("")
    lines.append("Detalle por palabra clave:")
    lines.append(breakdown_message(result, offer_id))
    return with_card_extras("\n".join(lines), salary_line, apply_findings)


def _salary_button_row(offer_id: int) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton("💶 Investigar salario", callback_data=f"{SAL}:{offer_id}")
    ]


def asks_cover_letter(findings: Sequence[ApplyInstruction]) -> bool:
    """True when the offer's apply instructions include a cover letter."""
    return any(finding.kind == "cover_letter" for finding in findings)


def _cover_letter_button_row(
    offer_id: int, *, asked: bool = False
) -> list[InlineKeyboardButton]:
    label = "✉️ Carta de presentación" + (" (la piden)" if asked else "")
    return [InlineKeyboardButton(label, callback_data=f"{CL}:{offer_id}")]


def cover_letter_message(label: str, text: str) -> str:
    """One generated version as plain text, kept under Telegram's limit."""
    return f"{label}\n\n{text}"[:_TELEGRAM_LIMIT]


def cover_letter_sources_message(facts: Sequence[CompanyFact]) -> str:
    """The researched company facts with their URLs, or the "thin paragraph" notice."""
    if not facts:
        return (
            "ℹ️ No encontré datos fiables de la empresa: el párrafo 2 se apoya "
            "solo en la oferta y queda más genérico. Revísalo antes de usarla."
        )
    lines = "\n".join(f"- {item.fact}\n  {item.source_url}" for item in facts)
    return f"🔎 Fuentes de la empresa (usadas en el párrafo 2):\n{lines}"[
        :_TELEGRAM_LIMIT
    ]


def cover_letter_keyboard(
    offer_id: int, version: str, generation: int
) -> InlineKeyboardMarkup:
    """The single [✅ Usar esta] action of one generated version (with its generation)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Usar esta",
                    callback_data=f"{CLC}:{offer_id}:{generation}:{version}",
                )
            ]
        ]
    )


def no_cv_line(label: str) -> str:
    """One-line note for portals applied to with their own profile (no CV)."""
    return f"📨 {label}: se aplica con tu perfil del portal (sin CV)"


def no_cv_reply(label: str) -> str:
    """Answer to a stale "Generar CV" press on such a portal's card."""
    return (
        f"ℹ️ Esta oferta es de {label}: se aplica con tu perfil del portal, "
        "no hace falta CV."
    )


def with_no_cv_line(text: str, label: str) -> str:
    """Append the one-line note on cards of portals applied to without a CV."""
    return f"{text}\n{no_cv_line(label)}"


def cv_keyboard(
    offer_id: int,
    *,
    role: str = "AI",
    lang: str = "es",
    cover_letter_asked: bool = False,
    cv_needed: bool = True,
) -> InlineKeyboardMarkup:
    """Generate-CV keyboard: action + Lang/Variant toggles + Mark sent + Discard.

    ``cv_needed=False`` (portal applied to with its own profile) drops the
    Generate row and the Lang/Variant toggles; the rest stays.

    Roles without a Catalan CV (Backend) never carry ``ca``: it falls
    back to ``es`` so the Generate button can never request a missing master.
    """
    role = normalize_role(role)
    if role in _NO_CATALAN_ROLES and lang == "ca":
        lang = "es"
    cv_rows = (
        [
            [
                InlineKeyboardButton(
                    "📄 Generar CV", callback_data=f"{CV}:{offer_id}:{role}:{lang}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"Idioma: {lang.upper()}",
                    callback_data=f"{LANG}:{offer_id}:{role}:{lang}",
                ),
                InlineKeyboardButton(
                    f"Variante: {_ROLE_BUTTON_LABEL.get(role, role)}",
                    callback_data=f"{VARIANT}:{offer_id}:{role}:{lang}",
                ),
            ],
        ]
        if cv_needed
        else []
    )
    return InlineKeyboardMarkup(
        [
            *cv_rows,
            _salary_button_row(offer_id),
            _cover_letter_button_row(offer_id, asked=cover_letter_asked),
            [
                # Applying without a generated CV (a base CV, or a portal that
                # needs none): mark it sent straight from the card.
                InlineKeyboardButton(
                    "✅ Marcar enviada", callback_data=f"{SENT}:{offer_id}"
                ),
                InlineKeyboardButton(
                    "🗑 Descartar", callback_data=f"{DISCARD}:{offer_id}"
                ),
            ],
        ]
    )


def sent_keyboard(
    offer_id: int, cv_base: str | None = None, *, cover_letter_asked: bool = False
) -> InlineKeyboardMarkup:
    """Post-generation keyboard: [Marcar enviada] + [🗑 Descartar].

    With the delivered ``cv_base`` (e.g. ``BACKEND_EN``) a second row offers
    [🔄 Rehacer CV]: it re-runs that same variant through the regular ``cv:``
    callback, so a CV made before a base-CV change can be rebuilt with the
    new version. Legacy ``DS_*`` rows map to AI (normalize_role).
    """
    rows = [
        [
            InlineKeyboardButton(
                "✅ Marcar enviada", callback_data=f"{SENT}:{offer_id}"
            ),
            InlineKeyboardButton("🗑 Descartar", callback_data=f"{DISC}:{offer_id}"),
        ]
    ]
    if cv_base and "_" in cv_base:
        role, lang = cv_base.rsplit("_", 1)
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Rehacer CV",
                    callback_data=f"{CV}:{offer_id}:{normalize_role(role)}:{lang.lower()}",
                )
            ]
        )
    rows.append(_salary_button_row(offer_id))
    rows.append(_cover_letter_button_row(offer_id, asked=cover_letter_asked))
    return InlineKeyboardMarkup(rows)


def cv_ready_message(
    pdf_path: Path,
    *,
    pages: int | None = None,
    url: str | None = None,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
) -> str:
    """Delivery card once the validated PDF exists (validators passed).

    ``pages`` is included when the caller knows it; the service seam returns
    only the path, so the bot omits it — reaching this card means the
    validators passed (delivery gate lives in the service). ``url`` (the
    offer link) is appended when known so the owner can open the offer
    before marking it sent.
    """
    pages_fragment = f" ({pages} pág.)" if pages is not None else ""
    link_fragment = f"\n🔗 Oferta: {url}" if url else ""
    text = (
        f"📄 CV listo: {pdf_path}{pages_fragment}. "
        f"Márcalo como enviada cuando lo envíes.{link_fragment}"
    )
    return with_card_extras(text, salary_line, apply_findings)


SALARY_UNAVAILABLE_MESSAGE: Final[str] = (
    "💶 Investigación salarial no disponible con este proveedor."
)
_SALARY_SOURCES_SHOWN: Final[int] = 5
_SALARY_NOTES_MAX: Final[int] = 500


def salary_research_message(outcome: SalaryOutcome) -> str:
    """Telegram reply for the "Investigar salario" button (plain text)."""
    if outcome.status == "unavailable":
        return SALARY_UNAVAILABLE_MESSAGE
    subject = f"{outcome.company} · {outcome.position}"
    where = f"{outcome.city} · {outcome.level}"
    cached = (
        f"\n(resultado guardado de hoy, {outcome.checked_at}; no se repite la búsqueda)"
        if outcome.cached
        else ""
    )
    research = outcome.research
    if outcome.status == "no_data" or research is None:
        return (
            f"💶 Sin datos fiables de salario para {subject} ({where}). "
            f"No se encontraron fuentes publicadas suficientes.{cached}"
        )
    median = (
        f" (mediana {eur_text(research.median)} €)"
        if research.median is not None
        else ""
    )
    lines = [
        f"💶 Salario investigado — {subject} · {where}{cached}",
        (
            f"Rango: {eur_text(research.min or 0)}–{eur_text(research.max or 0)} "
            f"€ brutos/año{median} · confianza {research.confidence}"
        ),
        "Fuentes:",
    ]
    for source in research.sources[:_SALARY_SOURCES_SHOWN]:
        detail = ", ".join(
            part
            for part in (source.date, f"n={source.n}" if source.n else None)
            if part
        )
        label = f"{source.publisher} ({detail})" if detail else source.publisher
        lines.append(f"· {label} {source.url}")
    if research.notes:
        lines.append(f"Notas: {research.notes[:_SALARY_NOTES_MAX]}")
    lines.append("⚠️ Estimación a partir de fuentes web; no es un dato de la empresa.")
    return "\n".join(lines)[:_TELEGRAM_LIMIT]


OUTCOME_LABELS: Final[dict[str, str]] = {
    "screening": "📞 Screening",
    "entrevista_tecnica": "💻 Entrevista técnica",
    "entrevista_final": "🏁 Entrevista final",
    "oferta": "🎉 Oferta",
    "aceptada": "✅ Aceptada",
    "rechazada": "❌ Rechazada",
    "sin_respuesta": "🤐 Sin respuesta",
    "retirada": "↩️ Retirada",
}
OUTCOME_NOTE_PROMPT: Final[str] = (
    "¿Quieres añadir una nota (feedback)? Escríbela o /skip"
)


def prep_button(offer_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        "🎯 Preparar entrevista", callback_data=f"{PREP}:{offer_id}"
    )


def prep_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[prep_button(offer_id)]])


def prep_stage_keyboard(offer_id: int, default: str) -> InlineKeyboardMarkup:
    """Stage choice for a prep pack; the suggested stage carries a ⭐."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⭐ {label}" if stage == default else label,
                    callback_data=f"{PREP}:{offer_id}:{stage}",
                )
                for stage, label in STAGE_LABEL.items()
            ]
        ]
    )


def outcome_keyboard(offer_id: int, estado: str | None = None) -> InlineKeyboardMarkup:
    """One button per post-send estado (two per row); interview estados add
    the "Preparar entrevista" button."""
    buttons = [
        InlineKeyboardButton(
            OUTCOME_LABELS[estado], callback_data=f"{OE}:{offer_id}:{estado}"
        )
        for estado in OUTCOME_ESTADOS
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if estado in PREP_ESTADOS:
        rows.append([prep_button(offer_id)])
    return InlineKeyboardMarkup(rows)


def sent_outcome_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """Sent-offer card: a single [📝 Resultado] entry to the outcome menu."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📝 Resultado", callback_data=f"{OUT}:{offer_id}")]]
    )


def outcome_message(offer: OfferRow, events: Sequence[OfferEvent]) -> str:
    """Header + timeline of an offer's outcome (Spanish)."""
    lines = [
        (
            f"📝 Resultado de #{offer.id} · {offer.empresa or '(sin empresa)'} · "
            f"{offer.posicion or '(sin posición)'}"
        ),
        f"Estado: {offer.estado}"
        + (f" · enviada {offer.fecha_envio}" if offer.fecha_envio else ""),
    ]
    if not events:
        lines.append("Sin eventos todavía.")
    for event in events:
        if event.kind == "estado":
            lines.append(f"{event.at} · estado → {event.estado}")
        elif event.kind == "followup":
            lines.append(f"{event.at} · seguimiento enviado")
        else:
            lines.append(f"{event.at} · nota: {event.note}")
    return "\n".join(lines)


def chunk_message(text: str, limit: int | None = None) -> list[str]:
    """Split ``text`` into parts of at most ``limit`` chars, on line boundaries.

    A single line longer than the limit is hard-split. Joining the parts with
    ``"\n"`` restores the text (hard-split pieces excepted).
    """
    size = limit if limit is not None else _TELEGRAM_LIMIT
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:size])
            line = line[size:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= size:
            current = candidate
        else:
            parts.append(current)
            current = line
    parts.append(current)
    return parts


def followup_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """Follow-up reminder: sent by hand / snooze / give up."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Enviado", callback_data=f"{FU}:{offer_id}:ok"),
                InlineKeyboardButton("⏭️ Saltar", callback_data=f"{FU}:{offer_id}:skip"),
                InlineKeyboardButton(
                    "🔕 Sin respuesta", callback_data=f"{FU}:{offer_id}:none"
                ),
            ]
        ]
    )


def followup_proposal_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    """Give-up proposal: the single [🔕 Sin respuesta] action."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔕 Sin respuesta", callback_data=f"{FU}:{offer_id}:none"
                )
            ]
        ]
    )


def _followup_offer_label(offer: OfferRow) -> str:
    return (
        f"#{offer.id} · {offer.posicion or '(sin posición)'} · "
        f"{offer.empresa or '(sin empresa)'}"
    )


def followup_message(
    offer: OfferRow,
    *,
    days_silent: int,
    number: int,
    draft: str | None,
    warnings: Sequence[str],
) -> str:
    """Follow-up reminder with the draft (Spanish). Nothing is sent for the owner."""
    lines = [
        f"📬 Seguimiento {number} de 2 · {_followup_offer_label(offer)}",
        f"Llevas {days_silent} días sin noticias desde tu última actividad.",
        "",
    ]
    if draft is None:
        lines.append("No he podido preparar un borrador esta vez; escríbelo tú.")
    else:
        lines.extend(["Borrador (envíalo tú si te encaja):", "", draft])
        if warnings:
            lines.append("")
            lines.append("⚠️ Revisa el borrador:")
            lines.extend(f"• {w}" for w in warnings)
    return "\n".join(lines)


def followup_proposal_message(offer: OfferRow, *, days_silent: int) -> str:
    """Proposal to give up after 2 follow-ups and a long silence (Spanish)."""
    return (
        f"🔕 {_followup_offer_label(offer)}\n"
        f"{days_silent} días sin respuesta y ya hiciste 2 seguimientos. "
        "¿La marco como sin respuesta?"
    )


def sent_message(offer_id: int, match: int) -> str:
    """Confirmation after ``mark_sent`` (tracker row estado=enviada)."""
    return f"✅ Oferta #{offer_id} marcada como enviada (match {match})."


def discard_message(offer_id: int) -> str:
    """Confirmation after the Discard path (no PDF generated)."""
    return f"🗑 Oferta #{offer_id} descartada. No se generará ningún PDF."


def discard_no_send_message() -> str:
    """Confirmation after a post-generation discard (estado=descartada,
    no send recorded — ``fecha_envio`` stays NULL so xlsx skips the row)."""
    return "🗑 Oferta descartada — no se registra envío."


def not_found_message() -> str:
    """NOT_FOUND (404) on a URL fetch: the offer looks removed/closed, not walled."""
    return (
        "🔒 Esa oferta parece haber sido retirada o cerrada (404); "
        "no hay nada que analizar. Si crees que sigue activa, pega el texto "
        "con /analyze."
    )


def offer_closed_message() -> str:
    """The offer text says it is closed: discarded without analysis."""
    return (
        "🔒 La oferta está cerrada (lo dice el propio texto): descartada, sin analizar."
    )


def paste_prompt() -> str:
    """WallError degradation (design D3): ask the user to paste the offer."""
    return (
        "⚠️ No pude acceder a la oferta (muro/bloqueo). "
        "Pega el texto de la oferta como mensaje y la analizo."
    )


def failure_reason(exc: BaseException | None) -> str:
    """Short chat-facing cause: LLM HTTP failures get a tailored .env hint."""
    if exc is None:
        return "error desconocido"
    text = str(exc).strip() or type(exc).__name__
    if isinstance(exc, LlmResponseError):
        match = search(r"HTTP (\d+)", text)
        cause = (
            f"el LLM devolvió HTTP {match.group(1)}"
            if match
            else f"el LLM falló: {text}"
        )
        return f"{cause} — revisa CVA_LLM_* en .env"
    return text


def analyze_error_message(reason: str) -> str:
    """Failed-analysis card; the paste flow stays armed for a corrected re-paste."""
    return (
        f"❌ El análisis falló: {reason} — pega el texto de nuevo cuando lo corrijas."
    )


def analyze_failure_message(exc: BaseException) -> str:
    """Failed-analysis card from the exception; HTTP 429 gets a tailored
    wait-and-repaste hint (the retry already ran once and failed)."""
    if isinstance(exc, LlmResponseError) and exc.status == 429:
        return (
            "❌ El análisis falló: límite de peticiones del LLM (429) — "
            "espera unos minutos y vuelve a pegar la oferta."
        )
    return analyze_error_message(failure_reason(exc))


def bridge_auto_failure_message(
    exc: BaseException,
    *,
    title: str | None,
    company: str | None,
    job_id: str,
    threshold: int,
    url: str | None = None,
) -> str:
    """Fallback card for a failed AUTO-analysis (auto-triage D4, Spanish).

    The legacy new-job card (so ``[Analizar]`` still completes manually) plus
    one brief cause line via :func:`failure_reason` — NOT
    :func:`analyze_failure_message`, whose "pega el texto de nuevo" hint is
    paste-flow context and wrong here. The offer row is already upserted, so
    a later ``[Analizar]`` press always completes. ``url`` is the per-source
    canonical offer URL (Card-content requirement); ``None`` keeps the
    LinkedIn legacy shape.
    """
    cause = failure_reason(exc)
    return (
        new_job_message(
            title=title,
            company=company,
            age="reciente",
            job_id=job_id,
            threshold=threshold,
            url=url,
        )
        + "\n"
        + f"⚠️ El análisis automático falló ({cause}). Pulsa Analizar para reintentar."
    )


def backstop_error_message(reason: str) -> str:
    """PTB error-handler card: short note naming what failed."""
    return f"⚠️ Ha fallado el procesamiento de tu mensaje: {reason}"


def help_message() -> str:
    """``/help`` and ``/start`` text: grouped commands and card buttons."""
    return help_text()


def offer_status_line(row: object, *, also_on: Sequence[str] = ()) -> str:
    """One /status line: id · estado · match · posición (Spanish)."""
    match = row.match  # type: ignore[attr-defined]
    perfil = getattr(row, "perfil", None)
    tag = " [backend]" if perfil == "backend" else ""
    line = (
        f"#{row.id} · {row.estado} · "  # type: ignore[attr-defined]
        f"match {match if match is not None else '—'} · "
        f"{row.posicion or '(sin posición)'}{tag}"  # type: ignore[attr-defined]
    )
    if also_on:
        line += f" · También en: {', '.join(also_on)}"
    return line


def beacons_list_message(rows: list[BeaconRow]) -> str:
    """/beacons listing: one status line per beacon (Spanish)."""
    if not rows:
        return "🔭 No hay beacons configurados."
    lines = ["🔭 Beacons:"]
    for row in rows:
        lines.append(
            f"#{row.id} {row.name} · {row.status} · "
            f"muros: {row.consecutive_walls} · "
            f"última comprobación: {row.last_checked_at or 'nunca'}"
        )
    return "\n".join(lines)


def beacon_action_message(action: str, beacon_id: int) -> str:
    """Confirmation for resume/disable (command or inline action)."""
    if action == "resume":
        return f"✅ Beacon #{beacon_id} reactivado (contador de muros a cero)."
    return f"⏹ Beacon #{beacon_id} desactivado (no se volverá a comprobar)."


def beacon_command_reply(service: "JobService", args: list[str]) -> str:
    """/beacon resume|disable <id>: parse + dispatch + Spanish reply text."""
    usage = "Uso: /beacon resume|disable <id>"
    if len(args) != 2 or args[0] not in ("resume", "disable"):
        return usage
    try:
        beacon_id = int(args[1])
    except ValueError:
        return usage
    try:
        if args[0] == "resume":
            service.resume_beacon(beacon_id)
        else:
            service.disable_beacon(beacon_id)
    except RepositoryError:
        return f"⚠️ No existe el beacon #{beacon_id}."
    return beacon_action_message(args[0], beacon_id)


def beacon_callback_reply(service: "JobService", kind: str, raw_id: str) -> str | None:
    """Pause-alert inline actions (``beacon_resume:``/``beacon_disable:``).

    Returns the confirmation text, or ``None`` for malformed payloads (the
    router stays silent on those, mirroring the other callback branches).
    """
    if kind not in (BEACON_RESUME, BEACON_DISABLE):
        return None
    try:
        beacon_id = int(raw_id)
    except ValueError:
        return None
    try:
        if kind == BEACON_RESUME:
            service.resume_beacon(beacon_id)
        else:
            service.disable_beacon(beacon_id)
    except RepositoryError:
        return f"⚠️ No existe el beacon #{beacon_id}."
    return beacon_action_message(kind.removeprefix("beacon_"), beacon_id)


def beacon_paused_message(alert: BeaconPausedAlert) -> str:
    """One-shot pause alert text (D3): name, wall count, last wall kind."""
    return (
        f"🚨 Beacon «{alert.beacon_name}» pausado tras "
        f"{alert.consecutive_walls} muros consecutivos "
        f"(último: {alert.wall_kind})."
    )


def beacon_alert_keyboard(beacon_id: int) -> InlineKeyboardMarkup:
    """Pause-alert actions: inline resume / disable (D3)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "▶️ Reactivar", callback_data=f"{BEACON_RESUME}:{beacon_id}"
                ),
                InlineKeyboardButton(
                    "⏹ Desactivar", callback_data=f"{BEACON_DISABLE}:{beacon_id}"
                ),
            ]
        ]
    )


_GMAIL_SUBJECT_MAX: Final[int] = 120


def _estado_text(estado: str) -> str:
    """Human name of an estado without its emoji ("entrevista técnica")."""
    label = OUTCOME_LABELS.get(estado, estado)
    return (label.split(" ", 1)[1] if " " in label else label).lower()


def gmail_proposal_message(proposal: "GmailProposal") -> str:
    """Spanish proposal text for a detected reply (plain text, no parse mode)."""
    subject = proposal.subject.strip()
    if len(subject) > _GMAIL_SUBJECT_MAX:
        subject = subject[: _GMAIL_SUBJECT_MAX - 1] + "…"
    estado = _estado_text(proposal.estado)
    return (
        f"📧 {proposal.company} · «{subject}»: parece {estado}.\n"
        f"¿Marcar la oferta #{proposal.offer_id} como {estado}?"
    )


def gmail_proposal_keyboard(message_id: str) -> InlineKeyboardMarkup:
    """[Sí] [Otro estado] [Ignorar] for one Gmail proposal."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Sí", callback_data=f"{GM}:y:{message_id}"),
                InlineKeyboardButton(
                    "Otro estado", callback_data=f"{GM}:o:{message_id}"
                ),
                InlineKeyboardButton("Ignorar", callback_data=f"{GM}:i:{message_id}"),
            ]
        ]
    )
