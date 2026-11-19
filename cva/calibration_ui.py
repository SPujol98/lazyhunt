"""Telegram text and keyboards for outcome calibration (UI language ``lang``).

Callback payloads are stateless ``cal:<action>:<id>[:<reason>]``:
``r`` rejection reason (offer id), ``y``/``n`` approve/decline an example,
``d`` deactivate an active one.
"""

import re
from collections.abc import Sequence
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cva.calibration import CalibrationExample, CalibrationSummary
from cva.i18n import LEGACY_LANG, Lang, t

CAL: Final[str] = "cal"

REASON_LABELS: Final[dict[str, str]] = {
    "requisitos": "Requisitos",
    "stack": "Stack",
    "idioma": "Idioma",
    "otro": "Otro / no sé",
}
_LEADING_SYMBOLS: Final[re.Pattern[str]] = re.compile(r"^[^\w]+")


def reason_label(reason: str, lang: Lang = LEGACY_LANG) -> str:
    """The button/label text of a rejection reason id (unknown ids as is)."""
    return t(f"panel.cal.reason.{reason}", lang) if reason in REASON_LABELS else reason


def outcome_text(estado: str, lang: Lang = LEGACY_LANG) -> str:
    """The outcome label without its emoji, lowercased (``entrevista técnica``)."""
    try:
        return t(f"panel.cal.outcome.{estado}", lang)
    except KeyError:
        return _LEADING_SYMBOLS.sub("", estado).lower()


def _result(example: CalibrationExample, lang: Lang) -> str:
    text = outcome_text(example.outcome, lang)
    if example.reason:
        text += f" ({reason_label(example.reason, lang).lower()})"
    return text


def question_message(example: CalibrationExample, lang: Lang = LEGACY_LANG) -> str:
    return t(
        "panel.cal.question",
        lang,
        score=example.score_at_send,
        result=_result(example, lang),
    )


def question_keyboard(
    example_id: int, lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("panel.cal.yes", lang), callback_data=f"{CAL}:y:{example_id}"
                ),
                InlineKeyboardButton(
                    t("panel.cal.no", lang), callback_data=f"{CAL}:n:{example_id}"
                ),
            ]
        ]
    )


def reason_message(lang: Lang = LEGACY_LANG) -> str:
    return t("panel.cal.reason_question", lang)


def reason_keyboard(offer_id: int, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            reason_label(reason, lang), callback_data=f"{CAL}:r:{offer_id}:{reason}"
        )
        for reason in REASON_LABELS
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def list_message(
    examples: Sequence[CalibrationExample],
    summary: CalibrationSummary,
    lang: Lang = LEGACY_LANG,
) -> str:
    if not examples:
        return t("panel.cal.empty", lang)
    lines = [t("panel.cal.list_head", lang, total=summary.total)]
    for example in examples:
        role = f" · {example.core_role}" if example.core_role else ""
        lines.append(
            t(
                "panel.cal.list_line",
                lang,
                id=example.offer_id,
                score=example.score_at_send,
                result=_result(example, lang),
                role=role,
            )
        )
    lines.extend(
        [
            "",
            t("panel.cal.positives", lang, count=summary.positives_below),
            t("panel.cal.negatives", lang, count=summary.negatives_above),
        ]
    )
    return "\n".join(lines)


def list_keyboard(
    examples: Sequence[CalibrationExample], lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("panel.cal.deactivate", lang, id=example.offer_id),
                    callback_data=f"{CAL}:d:{example.id}",
                )
            ]
            for example in examples
        ]
    )
