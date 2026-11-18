"""Telegram text and keyboards for outcome calibration (Spanish, user-facing).

Callback payloads are stateless ``cal:<action>:<id>[:<reason>]``:
``r`` rejection reason (offer id), ``y``/``n`` approve/decline an example,
``d`` deactivate an active one.
"""

import re
from collections.abc import Sequence
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cva.calibration import CalibrationExample, CalibrationSummary
from cva.notifier import OUTCOME_LABELS

CAL: Final[str] = "cal"

REASON_LABELS: Final[dict[str, str]] = {
    "requisitos": "Requisitos",
    "stack": "Stack",
    "idioma": "Idioma",
    "otro": "Otro / no sé",
}
_LEADING_SYMBOLS: Final[re.Pattern[str]] = re.compile(r"^[^\w]+")


def outcome_text(estado: str) -> str:
    """The outcome label without its emoji, lowercased (``entrevista técnica``)."""
    return _LEADING_SYMBOLS.sub("", OUTCOME_LABELS.get(estado, estado)).lower()


def _result(example: CalibrationExample) -> str:
    text = outcome_text(example.outcome)
    if example.reason:
        text += f" ({REASON_LABELS.get(example.reason, example.reason).lower()})"
    return text


def question_message(example: CalibrationExample) -> str:
    return (
        "🎯 ¿Usar esta oferta para calibrar la nota?\n"
        f"Nota del bot: {example.score_at_send}/100 · Resultado: {_result(example)}\n"
        "Solo se usan la nota, el rol y el resultado; nunca el texto de la "
        "oferta ni tus notas. Puedes quitarla luego en /calibracion."
    )


def question_keyboard(example_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Sí", callback_data=f"{CAL}:y:{example_id}"),
                InlineKeyboardButton("No", callback_data=f"{CAL}:n:{example_id}"),
            ]
        ]
    )


def reason_message() -> str:
    return (
        "🎯 ¿Por qué te rechazaron? Solo si fue por encaje puede servir para "
        "calibrar la nota."
    )


def reason_keyboard(offer_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{CAL}:r:{offer_id}:{reason}")
        for reason, label in REASON_LABELS.items()
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


def list_message(
    examples: Sequence[CalibrationExample], summary: CalibrationSummary
) -> str:
    if not examples:
        return (
            "🎯 No hay ejemplos de calibración activos.\n"
            "Cuando anotes un resultado con /outcome, el bot te preguntará si "
            "quieres usarlo para calibrar la nota."
        )
    lines = [f"🎯 Ejemplos de calibración activos ({summary.total}):"]
    for example in examples:
        role = f" · {example.core_role}" if example.core_role else ""
        lines.append(
            f"#{example.offer_id} · nota {example.score_at_send} · "
            f"{_result(example)}{role}"
        )
    lines.extend(
        [
            "",
            (
                f"• Positivos (llegaste a entrevista): {summary.positives_below} "
                "con nota por debajo del umbral."
            ),
            (
                f"• Negativos (rechazo por encaje): {summary.negatives_above} "
                "con nota por encima del umbral."
            ),
        ]
    )
    return "\n".join(lines)


def list_keyboard(examples: Sequence[CalibrationExample]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🗑 Desactivar (#{example.offer_id})",
                    callback_data=f"{CAL}:d:{example.id}",
                )
            ]
            for example in examples
        ]
    )
