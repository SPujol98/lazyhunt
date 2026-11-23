"""Notifier: PTB message + inline-keyboard builders (design D12, tasks 8.3–8.4).

Pure functions returning strings and ``telegram`` keyboard objects — no I/O,
no network, table-testable. Callback payloads are stateless strings (design
``an:`` / ``cv:`` / ``sent:`` scheme; toggles carry their current selection so
no bot-side session state is needed):

- ``an:{job_id}``                        analyze a beacon-reported job id
- ``cv:{offer_id}:{ROLE}:{es|en|ca}``     generate the CV (ROLE: a family's
  ``id.upper()``, e.g. AI|BACKEND; 2-part ``cv:{id}`` tolerated by the bot
  parser, defaults to the offer's family)
- ``lang:{offer_id}:{role}:{lang}``      flip the language toggle
- ``variant:{offer_id}:{role}:{lang}``   flip the role-family toggle
- ``discard:{offer_id}``                 discard without generating a PDF
- ``disc:{offer_id}``                    post-generation discard (no send recorded)
- ``sent:{offer_id}``                    mark the offer as sent
- ``sal:{offer_id}``                     on-demand salary web research
- ``cl:{offer_id}``                      write the two cover-letter versions
- ``clc:{offer_id}:{gen}:{a|b}``         store the chosen cover-letter version
- ``cld:{offer_id}``                     delete a proposed letter (and its sources) from the chat

LANGUAGE NOTE: every user-facing string lives in :mod:`cva.i18n.notifier`
(``notifier.*`` keys; button labels the help documents are the
``commands.button.<id>.label`` keys). Public builders take ``lang`` (the UI
language; ``cv_keyboard`` calls it ``ui_lang`` because its ``lang`` is the CV
language) and default to ``LEGACY_LANG`` until the bot wires
``Settings.ui_language``. Callback data never depends on the language and
stored values (estados, statuses) stay data; only their display is translated.
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
from cva.card_links import search_links
from cva.commands import help_text
from cva.families import BUILTIN_REGISTRY, CV_LANGUAGES, Family, FamilyRegistry
from cva.fsutil import display_path
from cva.i18n import CATALOGS, LANGS, LEGACY_LANG, Lang, t
from cva.llm import LlmResponseError
from cva.match import MatchResult
from cva.match.apply_instructions import KIND_EMOJI, ApplyInstruction
from cva.match.followup import Recipient
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
    from cva.match.letter_quality import LetterQuality
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
CLD = "cld"  # "Don't use": remove a proposed letter from the chat
OUT = "out"  # open the outcome menu of a sent offer
OE = "oe"  # record an outcome estado
GM = "gm"  # Gmail proposal actions: gm:<y|o|i>:<gmail message id>
FU = "fu"  # follow-up reminder actions: ok | skip | none
PREP = "prep"  # interview prep: prep:<id> (stage choice) | prep:<id>:<stage>
# Adapted CV (tailored-cv T3): cvt:<id>:<ROLE>:<lang> proposes one;
# cva:<id>:accept | cva:<id>:discard | cva:<id>:rm:<change index>.
CV_TAILOR = "cvt"
CV_TAILOR_ACTION = "cva"
BEACON_RESUME = "beacon_resume"
BEACON_DISABLE = "beacon_disable"

# Role and language toggles follow the profile's role families (role-families
# T5): the role cycle is the declared order, each family's language cycle its
# declared CV languages (built-ins: AI ES→EN→CA, Backend ES↔EN), the Variante
# label its ``label``. Unknown values reset to the default family / the
# family's first language; legacy ``DS`` (Data Scientist CVs, manual-only now,
# still in old cards' callback data) maps to AI.

# Round-summary amendment: max discard lines listed before the "… y N más".
_ROUND_DISCARD_LINES: Final[int] = 25
# Max entries listed in each walled / closed / duplicates block of the round
# summary, and in the standalone duplicates notice, before the "… y N más".
_ROUND_BLOCK_LINES: Final[int] = 5


def _button(button_id: str, lang: Lang, **values: str) -> str:
    """Label of a card button the help documents (shared ``commands`` catalog)."""
    return t(f"commands.button.{button_id}.label", lang, **values)


def _days_word(days: int, lang: Lang) -> str:
    return t("notifier.days.one" if days == 1 else "notifier.days.other", lang, n=days)


def _days_plain(days: int, lang: Lang) -> str:
    key = "notifier.days_plain.one" if days == 1 else "notifier.days_plain.other"
    return t(key, lang, n=days)


def _more(n: int, lang: Lang) -> str:
    return t("notifier.more", lang, n=n)


def _no_title(lang: Lang) -> str:
    return t("notifier.no_title", lang)


def _no_company(lang: Lang) -> str:
    return t("notifier.no_company", lang)


def _no_position(lang: Lang) -> str:
    return t("notifier.no_position", lang)


def outcome_label(estado: str, lang: Lang = LEGACY_LANG) -> str:
    """Button/display label of a post-send estado (``estado`` itself if unknown)."""
    key = f"notifier.outcome.{estado}"
    return t(key, lang) if key in CATALOGS[lang] else estado


def _estado_display(estado: str, lang: Lang) -> str:
    """The stored estado as the user reads it (data when it has no entry)."""
    key = f"notifier.estado.{estado}"
    return t(key, lang) if key in CATALOGS[lang] else estado


def next_lang(
    role: str, lang: str, *, families: FamilyRegistry = BUILTIN_REGISTRY
) -> str:
    """Next language of the toggle cycle for ``role`` (pure, table-tested)."""
    languages = families.resolve(role).languages
    if lang not in languages:
        return languages[0]
    return languages[(languages.index(lang) + 1) % len(languages)]


def normalize_role(role: str, *, families: FamilyRegistry = BUILTIN_REGISTRY) -> str:
    """Map a role from (possibly old) callback data onto the profile's roles:
    ``DS`` -> ``AI``, any spelling of a declared role -> its role, anything
    else -> the default family's role."""
    return families.resolve(role).role


def next_role(role: str, *, families: FamilyRegistry = BUILTIN_REGISTRY) -> str:
    """Next role of the variant toggle (unknown values reset to the default)."""
    return families.next_role(role)


def _family_lang(family: Family, lang: str) -> str:
    """A CV language the family has: a known language it lacks becomes its
    first one (built-in Backend: ``ca`` -> ``es``)."""
    if lang in CV_LANGUAGES and lang not in family.languages:
        return family.languages[0]
    return lang


def unknown_role_message(
    role: str, default_label: str, *, lang: Lang = LEGACY_LANG
) -> str:
    """Note on an old card button whose CV role the profile no longer has."""
    return t("notifier.unknown_role", lang, role=role, default_label=default_label)


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
    lang: Lang = LEGACY_LANG,
) -> str:
    """New-job card: title · company · age · canonical link + threshold context.

    ``url`` (infojobs-source, Card-content requirement): the per-source
    canonical URL line; ``None`` keeps the legacy LinkedIn-shape default
    (``canonical_job_url``) — every existing caller stays byte-identical.
    """
    return t(
        "notifier.new_job",
        lang,
        title=title or _no_title(lang),
        company=company or _no_company(lang),
        age=age,
        url=url if url is not None else canonical_job_url(job_id),
        threshold=threshold,
    )


class SentMessage(NamedTuple):
    """Where a card landed in Telegram: what ``notify`` reports back so the
    service can remember it and the bridge can edit it later."""

    chat_id: int
    message_id: int


ALSO_ON_PREFIX: Final[str] = t("notifier.also_on", "es")


def with_also_on(
    text: str,
    labels: Sequence[str],
    *,
    parse_mode: str | None = None,
    lang: Lang = LEGACY_LANG,
) -> str:
    """``text`` ending with ONE ``📍 También en: A, B`` line (none when no labels).

    Any earlier also-on line, in either language (the card may predate a
    language change), is replaced, never stacked. Labels are HTML-escaped when
    the card is sent with ``parse_mode="HTML"``.
    """
    lines = text.split("\n")
    prefixes = tuple(t("notifier.also_on", code) for code in LANGS)
    if lines and lines[-1].startswith(prefixes):
        lines.pop()
    if labels:
        shown = [html.escape(x) if parse_mode == "HTML" else x for x in labels]
        lines.append(t("notifier.also_on", lang) + ", ".join(shown))
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


def new_job_keyboard(job_id: str, *, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    """Notification keyboard: the single ``[Analyze]`` action."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("analyze", lang), callback_data=f"{AN}:{job_id}"
                )
            ]
        ]
    )


_TELEGRAM_LIMIT: Final[int] = 4096


def apply_warning_block(
    findings: Sequence[ApplyInstruction], *, lang: Lang = LEGACY_LANG
) -> str:
    """Warning block for extra application steps; ``""`` when none.

    Cards are plain text (no ``parse_mode``), so snippets are NOT escaped.
    """
    if not findings:
        return ""
    lines = [t("notifier.apply_warning_header", lang)]
    for item in findings:
        value = f": {item.value}" if item.value else ""
        lines.append(
            f"• {KIND_EMOJI.get(item.kind, '•')} {item.label}{value} — «{item.snippet}»"
        )
    return "\n".join(lines)


def with_apply_warnings(
    text: str, findings: Sequence[ApplyInstruction], *, lang: Lang = LEGACY_LANG
) -> str:
    """Append the warning block after a blank line, within Telegram's limit.

    Returns ``text`` byte-identical when there are no findings. Whole bullet
    lines are dropped from the end until the card fits; if not even the
    header fits, the card is returned unchanged.
    """
    block = apply_warning_block(findings, lang=lang)
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


def date_card_lines(
    fecha_publicacion: str | None,
    fecha_limite: str | None,
    today: date,
    *,
    lang: Lang = LEGACY_LANG,
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
                lines.append(t("notifier.deadline_today", lang))
            elif 0 < left <= _DEADLINE_CARD_DAYS:
                lines.append(
                    t(
                        "notifier.deadline_in",
                        lang,
                        days=_days_word(left, lang),
                        date=f"{deadline:%d/%m}",
                    )
                )
    if fecha_publicacion:
        with suppress(ValueError):
            age = (today - date.fromisoformat(fecha_publicacion)).days
            if age > _STALE_POSTING_DAYS:
                lines.append(
                    t("notifier.stale_posting", lang, days=_days_plain(age, lang))
                )
    return "\n".join(lines)


def easy_apply_line(
    origen: str | None, easy_apply: bool | None, *, lang: Lang = LEGACY_LANG
) -> str:
    """The "⚡ Easy Apply" line of a LinkedIn offer with the badge, or ``""``.

    Discreet on purpose (pending-maintenance S3): no line when the offer is
    not Easy Apply, unknown, or from another portal.
    """
    if origen == "linkedin" and easy_apply is True:
        return t("notifier.easy_apply", lang)
    return ""


_DEADLINE_ALERT_MAX_LINES: Final[int] = 15


def _deadline_entry(row: OfferRow, today: date, lang: Lang) -> str:
    when = t("notifier.deadline_when_today", lang)
    with suppress(ValueError, TypeError):
        left = (date.fromisoformat(row.fecha_limite or "") - today).days
        if left > 0:
            when = t("notifier.deadline_when_in", lang, days=_days_word(left, lang))
    entry = t(
        "notifier.deadline_entry",
        lang,
        position=row.posicion or _no_title(lang),
        company=row.empresa or _no_company(lang),
        when=when,
    )
    if row.url_oferta:
        entry += f"\n  {row.url_oferta}"
    return entry


def deadline_alert_batch(
    rows: Sequence[OfferRow], today: date, *, lang: Lang = LEGACY_LANG
) -> tuple[str, int]:
    """Owner alert text plus how many leading rows it actually lists.

    At most ``_DEADLINE_ALERT_MAX_LINES`` entries and always under the Telegram
    limit (a "… y N más" footer is reserved). The caller marks only the listed
    rows as alerted; the rest go out in the next message.
    """
    head = t("notifier.deadline_alert_head", lang)
    footer_room = len("\n" + _more(9999, lang))
    lines = [head]
    size = len(head)
    listed = 0
    for row in rows[:_DEADLINE_ALERT_MAX_LINES]:
        entry = _deadline_entry(row, today, lang)
        if listed and size + 1 + len(entry) + footer_room > _TELEGRAM_LIMIT:
            break
        lines.append(entry[: _TELEGRAM_LIMIT - size - 1 - footer_room])
        size += 1 + len(lines[-1])
        listed += 1
    extra = len(rows) - listed
    if extra > 0:
        lines.append(_more(extra, lang))
    return "\n".join(lines), listed


def deadline_alert_message(
    rows: Sequence[OfferRow], today: date, *, lang: Lang = LEGACY_LANG
) -> str:
    """Daily owner alert: un-sent offers closing soon (never past)."""
    return deadline_alert_batch(rows, today, lang=lang)[0]


def with_card_extras(
    text: str,
    salary_line: str,
    findings: Sequence[ApplyInstruction],
    *,
    lang: Lang = LEGACY_LANG,
) -> str:
    """Salary line first (short, fixed), then the droppable apply warnings."""
    return with_apply_warnings(with_salary_line(text, salary_line), findings, lang=lang)


def breakdown_message(
    result: MatchResult,
    offer_id: int,
    *,
    url: str | None = None,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
    lang: Lang = LEGACY_LANG,
) -> str:
    """Score vs threshold + per-requirement ✓/~/✗ lines with literal evidence.

    ``url`` (notification-pacing D6): the canonical offer URL line appended
    right after the header; ``None`` keeps the legacy shape (no URL line).
    """
    verdict = t(
        "notifier.breakdown_above"
        if result.score >= result.threshold
        else "notifier.breakdown_below",
        lang,
    )
    lines = [
        t(
            "notifier.breakdown_head",
            lang,
            offer_id=offer_id,
            score=result.score,
            threshold=result.threshold,
            verdict=verdict,
        ),
    ]
    if url is not None:
        lines.append(url)
    lines.append("")
    for match in result.breakdown:
        weight = t(
            "notifier.weight_must"
            if match.requirement.must_have
            else "notifier.weight_nice",
            lang,
        )
        lines.append(
            f"{match.status} {match.requirement.name} ({weight}) — "
            f"«{match.requirement.evidence}»"
        )
    return with_card_extras("\n".join(lines), salary_line, apply_findings, lang=lang)


def borderline_message(
    *,
    score: int,
    threshold: int,
    title: str | None,
    company: str | None,
    url: str,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
    lang: Lang = LEGACY_LANG,
) -> str:
    """Gray-zone card: compact near-miss notice for manual review.

    Deliberately NO ✓/~/✗ breakdown — the borderline card flags
    "close to Top-Choice" (score in ``[threshold - gray_zone, threshold)``);
    the full evidence stays exclusive to the breakdown card.
    """
    text = t(
        "notifier.borderline",
        lang,
        title=title or _no_title(lang),
        company=company or _no_company(lang),
        score=score,
        threshold=threshold,
        url=url,
    )
    return with_card_extras(text, salary_line, apply_findings, lang=lang)


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


def _offer_label(title: str | None, url: str | None, lang: Lang) -> str:
    safe = html.escape(title or _no_title(lang), quote=True)
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
    lang: Lang = LEGACY_LANG,
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
    lines = [t("notifier.round_head", lang, beacon=safe_beacon, analyzed=analyzed)]
    lines.append(t("notifier.round_counts", lang, above=above, gray=gray, below=below))
    if walled > 0:
        lines.append(t("notifier.round_walled", lang, n=walled))
        for wall in walled_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(wall.company or _no_company(lang), quote=True)
            reason = html.escape(wall.reason, quote=True)
            lines.append(
                f"· {reason} — {_offer_label(wall.title, wall.url, lang)} @ {company}"
            )
        hidden_walls = len(walled_offers) - _ROUND_BLOCK_LINES
        if hidden_walls > 0:
            lines.append(_more(hidden_walls, lang))
    if closed > 0:
        lines.append(t("notifier.round_closed", lang, n=closed))
        for gone in closed_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(gone.company or _no_company(lang), quote=True)
            lines.append(f"· {_offer_label(gone.title, gone.url, lang)} @ {company}")
        hidden_closed = len(closed_offers) - _ROUND_BLOCK_LINES
        if hidden_closed > 0:
            lines.append(_more(hidden_closed, lang))
    if duplicates > 0:
        lines.append(t("notifier.round_duplicates", lang, n=duplicates))
        for dup in duplicate_offers[:_ROUND_BLOCK_LINES]:
            company = html.escape(dup.company or _no_company(lang), quote=True)
            lines.append(
                f"· {_offer_label(dup.title, dup.url, lang)} @ {company} "
                f"→ #{dup.original_id}"
            )
        hidden_dups = duplicates - min(len(duplicate_offers), _ROUND_BLOCK_LINES)
        if hidden_dups > 0:
            lines.append(_more(hidden_dups, lang))
    if pending > 0:
        lines.append(t("notifier.round_pending", lang, n=pending))
    if below > 0:
        lines.append(t("notifier.round_discarded_head", lang))
        shown = discarded[:_ROUND_DISCARD_LINES]
        for offer in shown:
            title = html.escape(offer.title or _no_title(lang), quote=True)
            company = html.escape(offer.company or _no_company(lang), quote=True)
            label = (
                f'<a href="{html.escape(offer.url, quote=True)}">{title}</a>'
                if offer.url is not None
                else title
            )
            lines.append(f"· {offer.score}/100 — {label} @ {company}")
        hidden = len(discarded) - len(shown)
        if hidden > 0:
            lines.append(_more(hidden, lang))
    return "\n".join(lines)


_VERDICT_EMOJI: Final[dict[str, str]] = {
    "apply": "✅",
    "apply_with_caveats": "🟡",
    "discard": "⛔",
}


def _verdict_label(verdict: str, lang: Lang) -> str:
    key = f"notifier.verdict.{verdict}"
    return t(key, lang) if key in CATALOGS[lang] else verdict


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
    lang: Lang = LEGACY_LANG,
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
        text = breakdown_message(result, offer_id, url=url, lang=lang)
        if judge_fallback:
            text += "\n" + t("notifier.judge_fallback", lang)
        if result.stack_wall:
            text += "\n" + t("notifier.wall", lang, reason=result.stack_wall)
        for gate in result.gates:
            if gate.verdict == "FAIL":
                text += "\n" + t("notifier.wall", lang, reason=gate.reason)
            elif gate.verdict == "FLAG":
                text += f"\n⚠️ {gate.reason}"
        return with_card_extras(text, salary_line, apply_findings, lang=lang)
    emoji = _VERDICT_EMOJI.get(judgment.verdict, "❔")
    label = _verdict_label(judgment.verdict, lang)
    lines = [
        t(
            "notifier.judge_head",
            lang,
            emoji=emoji,
            offer_id=offer_id,
            score=judgment.score,
            label=label,
        ),
    ]
    if url is not None:
        lines.append(url)
    lines.append(t("notifier.judge_role", lang, role=judgment.core_role))
    lines.append(t("notifier.judge_cv_base", lang, cv_base=judgment.cv_base))
    if judgment.fit:
        lines.append(t("notifier.judge_fit", lang))
        for item in judgment.fit[:_JUDGE_FIT_LINES]:
            lines.append(f"· {item.requirement} ({', '.join(item.claim_ids)})")
    if judgment.walls:
        lines.append(t("notifier.judge_walls", lang))
        for wall in sorted(judgment.walls, key=lambda w: not w.hard):
            marker = "🚫" if wall.hard else "⚠️"
            lines.append(f"{marker} {wall.text}")
    lines.append("")
    lines.append(judgment.rationale)
    lines.append("")
    lines.append(t("notifier.judge_keyword_detail", lang))
    lines.append(breakdown_message(result, offer_id, lang=lang))
    return with_card_extras("\n".join(lines), salary_line, apply_findings, lang=lang)


def _salary_button_row(offer_id: int, lang: Lang) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(_button("salary", lang), callback_data=f"{SAL}:{offer_id}")
    ]


def asks_cover_letter(findings: Sequence[ApplyInstruction]) -> bool:
    """True when the offer's apply instructions include a cover letter."""
    return any(finding.kind == "cover_letter" for finding in findings)


def _cover_letter_button_row(
    offer_id: int, lang: Lang, *, asked: bool = False
) -> list[InlineKeyboardButton]:
    label = _button("cover_letter", lang) + (
        t("notifier.cover_letter_asked", lang) if asked else ""
    )
    return [InlineKeyboardButton(label, callback_data=f"{CL}:{offer_id}")]


def cover_letter_message(label: str, text: str) -> str:
    """One generated version as plain text, kept under Telegram's limit."""
    return f"{label}\n\n{text}"[:_TELEGRAM_LIMIT]


def letter_quality_line(quality: "LetterQuality | None", *, lang: Lang) -> str:
    """One short line with the letter's deterministic quality score and the
    offer must-haves it does not mention ("" when there is no score)."""
    if quality is None:
        return ""
    line = t("notifier.letter_quality", lang, score=str(quality.score))
    if quality.missing:
        line += t(
            "notifier.letter_quality_missing", lang, items=", ".join(quality.missing)
        )
    return line


def cover_letter_sources_message(
    facts: Sequence[CompanyFact], *, lang: Lang = LEGACY_LANG
) -> str:
    """The researched company facts with their URLs, or the "thin paragraph" notice."""
    if not facts:
        return t("notifier.cover_sources_none", lang)
    lines = "\n".join(f"- {item.fact}\n  {item.source_url}" for item in facts)
    return t("notifier.cover_sources", lang, lines=lines)[:_TELEGRAM_LIMIT]


def cover_letter_keyboard(
    offer_id: int, version: str, generation: int, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """[✅ Use this one] (with its generation) and [🗑 Don't use] of one generated version."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("use_this", lang),
                    callback_data=f"{CLC}:{offer_id}:{generation}:{version}",
                ),
                InlineKeyboardButton(
                    _button("dont_use", lang), callback_data=f"{CLD}:{offer_id}"
                ),
            ]
        ]
    )


def no_cv_line(label: str, *, lang: Lang = LEGACY_LANG) -> str:
    """One-line note for portals applied to with their own profile (no CV)."""
    return t("notifier.no_cv_line", lang, label=label)


def no_cv_reply(label: str, *, lang: Lang = LEGACY_LANG) -> str:
    """Answer to a stale "Generate CV" press on such a portal's card."""
    return t("notifier.no_cv_reply", lang, label=label)


def with_no_cv_line(text: str, label: str, *, lang: Lang = LEGACY_LANG) -> str:
    """Append the one-line note on cards of portals applied to without a CV."""
    return f"{text}\n{no_cv_line(label, lang=lang)}"


def cv_keyboard(
    offer_id: int,
    *,
    role: str | None = None,
    lang: str = "es",
    cover_letter_asked: bool = False,
    cv_needed: bool = True,
    families: FamilyRegistry = BUILTIN_REGISTRY,
    ui_lang: Lang = LEGACY_LANG,
    offer: OfferRow | None = None,
) -> InlineKeyboardMarkup:
    """Generate-CV keyboard: action + Lang/Variant toggles + Mark sent + Discard.

    With the tracker ``offer`` a last row of "🔎 Search elsewhere" URL buttons
    (:func:`search_elsewhere_row`) follows; the callback rows never change.

    ``lang`` is the CV language of the toggle; ``ui_lang`` the interface's.

    ``cv_needed=False`` (portal applied to with its own profile) drops the
    Generate row and the Lang/Variant toggles; the rest stays.

    ``role`` ``None`` is the default family's. A family never carries a
    language it lacks (built-in Backend: ``ca``): it falls back to the
    family's first language so the Generate button can never request a
    missing base CV.
    """
    family = families.resolve(role) if role is not None else families.default
    role = family.role
    lang = _family_lang(family, lang)
    cv_rows = (
        [
            [
                InlineKeyboardButton(
                    _button("generate_cv", ui_lang),
                    callback_data=f"{CV}:{offer_id}:{role}:{lang}",
                )
            ],
            [
                InlineKeyboardButton(
                    _button("language", ui_lang, langs=lang.upper()),
                    callback_data=f"{LANG}:{offer_id}:{role}:{lang}",
                ),
                InlineKeyboardButton(
                    _button("variant", ui_lang, labels=family.label),
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
            _salary_button_row(offer_id, ui_lang),
            _cover_letter_button_row(offer_id, ui_lang, asked=cover_letter_asked),
            [
                # Applying without a generated CV (a base CV, or a portal that
                # needs none): mark it sent straight from the card.
                InlineKeyboardButton(
                    _button("mark_sent", ui_lang), callback_data=f"{SENT}:{offer_id}"
                ),
                InlineKeyboardButton(
                    _button("discard", ui_lang), callback_data=f"{DISCARD}:{offer_id}"
                ),
            ],
            *search_elsewhere_rows(offer, ui_lang),
        ]
    )


def search_elsewhere_rows(
    offer: OfferRow | None, lang: Lang
) -> list[list[InlineKeyboardButton]]:
    """The card's "🔎 Search elsewhere" row (URL buttons, no callback data):
    the offer's title + company searched on other sites, its own portal
    skipped. No row without an offer or with neither title nor company."""
    if offer is None:
        return []
    links = search_links(
        offer.posicion, offer.empresa, source=offer.origen, url=offer.url_oferta
    )
    if not links:
        return []
    return [
        [
            InlineKeyboardButton(
                t("notifier.search_on", lang, site=link.site), url=link.url
            )
            for link in links
        ]
    ]


def sent_keyboard(
    offer_id: int,
    cv_base: str | None = None,
    *,
    cover_letter_asked: bool = False,
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
    adaptable: bool = False,
) -> InlineKeyboardMarkup:
    """Post-generation keyboard: [Mark as sent] + [🗑 Discard].

    ``adaptable`` (tailored-cv T3: the delivered base CV is one lazyhunt
    built, never the owner's own PDFs) adds a last row [✨ Adapt to this
    offer] -> ``cvt:<id>:<ROLE>:<lang>``, the same variant as [🔄 Redo CV].

    With the delivered ``cv_base`` (e.g. ``BACKEND_EN``) a second row offers
    [🔄 Redo CV]: it re-runs that same variant through the regular ``cv:``
    callback, so a CV made before a base-CV change can be rebuilt with the
    new version. Legacy ``DS_*`` rows map to AI; a role the profile no longer
    declares to the default family (in a language it has).
    """
    rows = [
        [
            InlineKeyboardButton(
                _button("mark_sent", lang), callback_data=f"{SENT}:{offer_id}"
            ),
            InlineKeyboardButton(
                _button("discard", lang), callback_data=f"{DISC}:{offer_id}"
            ),
        ]
    ]
    adapt: str | None = None
    if cv_base and "_" in cv_base:
        role, cv_lang = cv_base.rsplit("_", 1)
        family = families.resolve(role)
        cv_lang = _family_lang(family, cv_lang.lower())
        rows.append(
            [
                InlineKeyboardButton(
                    _button("redo_cv", lang),
                    callback_data=f"{CV}:{offer_id}:{family.role}:{cv_lang}",
                )
            ]
        )
        adapt = f"{CV_TAILOR}:{offer_id}:{family.role}:{cv_lang}"
    rows.append(_salary_button_row(offer_id, lang))
    rows.append(_cover_letter_button_row(offer_id, lang, asked=cover_letter_asked))
    if adaptable and adapt is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    t("tailor.bot.button.adapt", lang), callback_data=adapt
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def cv_ready_message(
    pdf_path: Path,
    *,
    pages: int | None = None,
    url: str | None = None,
    apply_findings: Sequence[ApplyInstruction] = (),
    salary_line: str = "",
    lang: Lang = LEGACY_LANG,
) -> str:
    """Delivery card once the validated PDF exists (validators passed).

    ``pages`` is included when the caller knows it; the service seam returns
    only the path, so the bot omits it — reaching this card means the
    validators passed (delivery gate lives in the service). ``url`` (the
    offer link) is appended when known so the owner can open the offer
    before marking it sent.
    """
    pages_fragment = (
        t("notifier.cv_ready_pages", lang, pages=pages) if pages is not None else ""
    )
    link_fragment = t("notifier.cv_ready_link", lang, url=url) if url else ""
    text = t(
        "notifier.cv_ready",
        lang,
        path=display_path(pdf_path),
        pages=pages_fragment,
        link=link_fragment,
    )
    return with_card_extras(text, salary_line, apply_findings, lang=lang)


SALARY_UNAVAILABLE_MESSAGE: Final[str] = t("notifier.salary_unavailable", "es")
_SALARY_SOURCES_SHOWN: Final[int] = 5
_SALARY_NOTES_MAX: Final[int] = 500


def salary_research_message(outcome: SalaryOutcome, *, lang: Lang = LEGACY_LANG) -> str:
    """Telegram reply for the "Research salary" button (plain text)."""
    if outcome.status == "unavailable":
        return t("notifier.salary_unavailable", lang)
    subject = f"{outcome.company} · {outcome.position}"
    where = f"{outcome.city} · {outcome.level}"
    cached = (
        t("notifier.salary_cached", lang, checked_at=outcome.checked_at)
        if outcome.cached
        else ""
    )
    research = outcome.research
    if outcome.status == "no_data" or research is None:
        return t(
            "notifier.salary_no_data",
            lang,
            subject=subject,
            where=where,
            cached=cached,
        )
    median = (
        t("notifier.salary_median", lang, median=eur_text(research.median))
        if research.median is not None
        else ""
    )
    lines = [
        t("notifier.salary_head", lang, subject=subject, where=where, cached=cached),
        t(
            "notifier.salary_range",
            lang,
            min=eur_text(research.min or 0),
            max=eur_text(research.max or 0),
            median=median,
            confidence=research.confidence,
        ),
        t("notifier.salary_sources", lang),
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
        lines.append(
            t("notifier.salary_notes", lang, notes=research.notes[:_SALARY_NOTES_MAX])
        )
    lines.append(t("notifier.salary_disclaimer", lang))
    return "\n".join(lines)[:_TELEGRAM_LIMIT]


# Spanish display labels (the legacy default); ``outcome_label(estado, lang)``
# serves every language.
OUTCOME_LABELS: Final[dict[str, str]] = {
    estado: outcome_label(estado, "es") for estado in OUTCOME_ESTADOS
}
OUTCOME_NOTE_PROMPT: Final[str] = t("notifier.outcome_note_prompt", "es")


def outcome_note_prompt(lang: Lang = LEGACY_LANG) -> str:
    """Ask for an optional feedback note after an outcome is recorded."""
    return t("notifier.outcome_note_prompt", lang)


def _stage_label(stage: str, lang: Lang) -> str:
    key = f"notifier.stage.{stage}"
    return t(key, lang) if key in CATALOGS[lang] else STAGE_LABEL.get(stage, stage)


def prep_button(offer_id: int, *, lang: Lang = LEGACY_LANG) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        _button("prep", lang), callback_data=f"{PREP}:{offer_id}"
    )


def prep_keyboard(offer_id: int, *, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[prep_button(offer_id, lang=lang)]])


def prep_stage_keyboard(
    offer_id: int, default: str, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """Stage choice for a prep pack; the suggested stage carries a ⭐."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⭐ {_stage_label(stage, lang)}"
                    if stage == default
                    else _stage_label(stage, lang),
                    callback_data=f"{PREP}:{offer_id}:{stage}",
                )
                for stage in STAGE_LABEL
            ]
        ]
    )


def outcome_keyboard(
    offer_id: int, estado: str | None = None, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """One button per post-send estado (two per row); interview estados add
    the "Prepare interview" button."""
    buttons = [
        InlineKeyboardButton(
            outcome_label(option, lang), callback_data=f"{OE}:{offer_id}:{option}"
        )
        for option in OUTCOME_ESTADOS
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    if estado in PREP_ESTADOS:
        rows.append([prep_button(offer_id, lang=lang)])
    return InlineKeyboardMarkup(rows)


def sent_outcome_keyboard(
    offer_id: int, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """Sent-offer card: a single [📝 Outcome] entry to the outcome menu."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("outcome", lang), callback_data=f"{OUT}:{offer_id}"
                )
            ]
        ]
    )


def outcome_message(
    offer: OfferRow, events: Sequence[OfferEvent], *, lang: Lang = LEGACY_LANG
) -> str:
    """Header + timeline of an offer's outcome."""
    lines = [
        t(
            "notifier.outcome_head",
            lang,
            id=offer.id,
            company=offer.empresa or _no_company(lang),
            position=offer.posicion or _no_position(lang),
        ),
        t("notifier.outcome_state", lang, estado=_estado_display(offer.estado, lang))
        + (
            t("notifier.outcome_sent_on", lang, date=offer.fecha_envio)
            if offer.fecha_envio
            else ""
        ),
    ]
    if not events:
        lines.append(t("notifier.outcome_no_events", lang))
    for event in events:
        if event.kind == "estado":
            lines.append(
                t(
                    "notifier.event_estado",
                    lang,
                    at=event.at,
                    estado=_estado_display(str(event.estado), lang),
                )
            )
        elif event.kind == "followup":
            lines.append(t("notifier.event_followup", lang, at=event.at))
        else:
            lines.append(t("notifier.event_note", lang, at=event.at, note=event.note))
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


def followup_keyboard(
    offer_id: int, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """Follow-up reminder: sent by hand / snooze / give up."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("followup_sent", lang), callback_data=f"{FU}:{offer_id}:ok"
                ),
                InlineKeyboardButton(
                    _button("followup_skip", lang),
                    callback_data=f"{FU}:{offer_id}:skip",
                ),
                InlineKeyboardButton(
                    _button("followup_no_reply", lang),
                    callback_data=f"{FU}:{offer_id}:none",
                ),
            ]
        ]
    )


def followup_proposal_keyboard(
    offer_id: int, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """Give-up proposal: the single [🔕 No response] action."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("followup_no_reply", lang),
                    callback_data=f"{FU}:{offer_id}:none",
                )
            ]
        ]
    )


def _followup_offer_label(offer: OfferRow, lang: Lang) -> str:
    return (
        f"#{offer.id} · {offer.posicion or _no_position(lang)} · "
        f"{offer.empresa or _no_company(lang)}"
    )


def _recipient_line(recipient: Recipient, lang: Lang) -> str:
    if recipient.address is None or recipient.source is None:
        return t("notifier.followup_to_none", lang)
    return t(
        "notifier.followup_to",
        lang,
        address=recipient.address,
        source=t(f"notifier.followup_to.{recipient.source}", lang),
    )


def followup_message(
    offer: OfferRow,
    *,
    days_silent: int,
    number: int,
    draft: str | None,
    warnings: Sequence[str],
    lang: Lang = LEGACY_LANG,
    recipient: Recipient | None = None,
) -> str:
    """Follow-up reminder with the draft (and, when resolved, whom to send it
    to). Nothing is sent for the owner."""
    lines = [
        t(
            "notifier.followup_head",
            lang,
            number=number,
            label=_followup_offer_label(offer, lang),
        ),
        t("notifier.followup_silent", lang, days=_days_plain(days_silent, lang)),
    ]
    if recipient is not None:
        lines.append(_recipient_line(recipient, lang))
    lines.append("")
    if draft is None:
        lines.append(t("notifier.followup_no_draft", lang))
    else:
        lines.extend([t("notifier.followup_draft", lang), "", draft])
        if warnings:
            lines.append("")
            lines.append(t("notifier.followup_review", lang))
            lines.extend(f"• {w}" for w in warnings)
    return "\n".join(lines)


def followup_proposal_message(
    offer: OfferRow, *, days_silent: int, lang: Lang = LEGACY_LANG
) -> str:
    """Proposal to give up after 2 follow-ups and a long silence."""
    return t(
        "notifier.followup_proposal",
        lang,
        label=_followup_offer_label(offer, lang),
        days=_days_plain(days_silent, lang),
    )


def sent_message(offer_id: int, match: int, *, lang: Lang = LEGACY_LANG) -> str:
    """Confirmation after ``mark_sent`` (tracker row estado=enviada)."""
    return t("notifier.sent", lang, offer_id=offer_id, match=match)


def discard_message(offer_id: int, *, lang: Lang = LEGACY_LANG) -> str:
    """Confirmation after the Discard path (no PDF generated)."""
    return t("notifier.discarded", lang, offer_id=offer_id)


def discard_no_send_message(*, lang: Lang = LEGACY_LANG) -> str:
    """Confirmation after a post-generation discard (estado=descartada,
    no send recorded — ``fecha_envio`` stays NULL so xlsx skips the row)."""
    return t("notifier.discarded_no_send", lang)


def not_found_message(*, lang: Lang = LEGACY_LANG) -> str:
    """NOT_FOUND (404) on a URL fetch: the offer looks removed/closed, not walled."""
    return t("notifier.not_found", lang)


def offer_closed_message(*, lang: Lang = LEGACY_LANG) -> str:
    """The offer text says it is closed: discarded without analysis."""
    return t("notifier.offer_closed", lang)


def paste_prompt(*, lang: Lang = LEGACY_LANG) -> str:
    """WallError degradation (design D3): ask the user to paste the offer."""
    return t("notifier.paste_prompt", lang)


def failure_reason(exc: BaseException | None, *, lang: Lang = LEGACY_LANG) -> str:
    """Short chat-facing cause: LLM HTTP failures get a tailored .env hint."""
    if exc is None:
        return t("notifier.failure_unknown", lang)
    text = str(exc).strip() or type(exc).__name__
    if isinstance(exc, LlmResponseError):
        match = search(r"HTTP (\d+)", text)
        cause = (
            t("notifier.failure_http", lang, code=match.group(1))
            if match
            else t("notifier.failure_llm", lang, text=text)
        )
        return t("notifier.failure_hint", lang, cause=cause)
    return text


def analyze_error_message(reason: str, *, lang: Lang = LEGACY_LANG) -> str:
    """Failed-analysis card; the paste flow stays armed for a corrected re-paste."""
    return t("notifier.analyze_error", lang, reason=reason)


def analyze_failure_message(exc: BaseException, *, lang: Lang = LEGACY_LANG) -> str:
    """Failed-analysis card from the exception; HTTP 429 gets a tailored
    wait-and-repaste hint (the retry already ran once and failed)."""
    if isinstance(exc, LlmResponseError) and exc.status == 429:
        return t("notifier.analyze_rate_limit", lang)
    return analyze_error_message(failure_reason(exc, lang=lang), lang=lang)


def bridge_auto_failure_message(
    exc: BaseException,
    *,
    title: str | None,
    company: str | None,
    job_id: str,
    threshold: int,
    url: str | None = None,
    lang: Lang = LEGACY_LANG,
) -> str:
    """Fallback card for a failed AUTO-analysis (auto-triage D4).

    The legacy new-job card (so ``[Analizar]`` still completes manually) plus
    one brief cause line via :func:`failure_reason` — NOT
    :func:`analyze_failure_message`, whose "paste the text again" hint is
    paste-flow context and wrong here. The offer row is already upserted, so
    a later ``[Analizar]`` press always completes. ``url`` is the per-source
    canonical offer URL (Card-content requirement); ``None`` keeps the
    LinkedIn legacy shape.
    """
    cause = failure_reason(exc, lang=lang)
    return (
        new_job_message(
            title=title,
            company=company,
            age=t("notifier.age_recent", lang),
            job_id=job_id,
            threshold=threshold,
            url=url,
            lang=lang,
        )
        + "\n"
        + t("notifier.auto_failure", lang, cause=cause)
    )


def backstop_error_message(reason: str, *, lang: Lang = LEGACY_LANG) -> str:
    """PTB error-handler card: short note naming what failed."""
    return t("notifier.backstop", lang, reason=reason)


def help_message(
    families: FamilyRegistry = BUILTIN_REGISTRY, *, lang: Lang = LEGACY_LANG
) -> str:
    """``/help`` and ``/start`` text: grouped commands and card buttons."""
    return help_text(families, lang)


def offer_status_line(
    row: object,
    *,
    also_on: Sequence[str] = (),
    families: FamilyRegistry = BUILTIN_REGISTRY,
    lang: Lang = LEGACY_LANG,
) -> str:
    """One /status line: id · estado · match · position; offers of
    a non-default family end with its label, e.g. ``[backend]``."""
    match = row.match  # type: ignore[attr-defined]
    family = families.resolve(families.normalize(getattr(row, "perfil", None)))
    tag = "" if family == families.default else f" [{family.label.lower()}]"
    line = t(
        "notifier.status_line",
        lang,
        id=row.id,  # type: ignore[attr-defined]
        estado=_estado_display(row.estado, lang),  # type: ignore[attr-defined]
        match=match if match is not None else "—",
        position=row.posicion or _no_position(lang),  # type: ignore[attr-defined]
        tag=tag,
    )
    if also_on:
        line += t("notifier.also_on_status", lang) + ", ".join(also_on)
    return line


def beacons_list_message(rows: list[BeaconRow], *, lang: Lang = LEGACY_LANG) -> str:
    """/beacons listing: one status line per beacon."""
    if not rows:
        return t("notifier.beacons_none", lang)
    lines = [t("notifier.beacons_head", lang)]
    for row in rows:
        lines.append(
            t(
                "notifier.beacons_line",
                lang,
                id=row.id,
                name=row.name,
                status=row.status,
                walls=row.consecutive_walls,
                last=row.last_checked_at or t("notifier.beacons_never", lang),
            )
        )
    return "\n".join(lines)


def beacon_action_message(
    action: str, beacon_id: int, *, lang: Lang = LEGACY_LANG
) -> str:
    """Confirmation for resume/disable (command or inline action)."""
    if action == "resume":
        return t("notifier.beacon_resumed", lang, beacon_id=beacon_id)
    return t("notifier.beacon_disabled", lang, beacon_id=beacon_id)


def beacon_command_reply(
    service: "JobService", args: list[str], *, lang: Lang = LEGACY_LANG
) -> str:
    """/beacon resume|disable <id>: parse + dispatch + reply text."""
    usage = t("notifier.beacon_usage", lang)
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
        return t("notifier.beacon_missing", lang, beacon_id=beacon_id)
    return beacon_action_message(args[0], beacon_id, lang=lang)


def beacon_callback_reply(
    service: "JobService", kind: str, raw_id: str, *, lang: Lang = LEGACY_LANG
) -> str | None:
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
        return t("notifier.beacon_missing", lang, beacon_id=beacon_id)
    return beacon_action_message(kind.removeprefix("beacon_"), beacon_id, lang=lang)


def beacon_paused_message(alert: BeaconPausedAlert, *, lang: Lang = LEGACY_LANG) -> str:
    """One-shot pause alert text (D3): name, wall count, last wall kind."""
    return t(
        "notifier.beacon_paused",
        lang,
        name=alert.beacon_name,
        walls=alert.consecutive_walls,
        kind=alert.wall_kind,
    )


def beacon_alert_keyboard(
    beacon_id: int, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """Pause-alert actions: inline resume / disable (D3)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _button("beacon_resume", lang),
                    callback_data=f"{BEACON_RESUME}:{beacon_id}",
                ),
                InlineKeyboardButton(
                    _button("beacon_disable", lang),
                    callback_data=f"{BEACON_DISABLE}:{beacon_id}",
                ),
            ]
        ]
    )


_GMAIL_SUBJECT_MAX: Final[int] = 120


def _estado_text(estado: str, lang: Lang) -> str:
    """Human name of an estado without its emoji ("entrevista técnica")."""
    label = outcome_label(estado, lang)
    return (label.split(" ", 1)[1] if " " in label else label).lower()


def gmail_proposal_message(
    proposal: "GmailProposal", *, lang: Lang = LEGACY_LANG
) -> str:
    """Proposal text for a detected reply (plain text, no parse mode)."""
    subject = proposal.subject.strip()
    if len(subject) > _GMAIL_SUBJECT_MAX:
        subject = subject[: _GMAIL_SUBJECT_MAX - 1] + "…"
    estado = _estado_text(proposal.estado, lang)
    return t(
        "notifier.gmail_proposal",
        lang,
        company=proposal.company,
        subject=subject,
        estado=estado,
        offer_id=proposal.offer_id,
    )


def gmail_proposal_keyboard(
    message_id: str, *, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """[Yes] [Other status] [Ignore] for one Gmail proposal."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("notifier.gmail_yes", lang), callback_data=f"{GM}:y:{message_id}"
                ),
                InlineKeyboardButton(
                    t("notifier.gmail_other", lang),
                    callback_data=f"{GM}:o:{message_id}",
                ),
                InlineKeyboardButton(
                    t("notifier.gmail_ignore", lang),
                    callback_data=f"{GM}:i:{message_id}",
                ),
            ]
        ]
    )
