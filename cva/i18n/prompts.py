"""UI-language LLM instructions (judge rationale, interview prep, STAR): bot-i18n T5.

Keys are prefixed ``prompts.``. The ES values are byte-for-byte the text the
prompts carried before the UI language existed (owner golden prompts).
"""

from typing import Final

EN: Final[dict[str, str]] = {
    "prompts.judge_rationale_lang": "English",
    "prompts.prep_lang": "in English (the candidate reads English)",
    "prompts.prep_likely": "it is likely",
    "prompts.facts_lang": "English",
    "prompts.star.complete": "✅ complete",
    "prompts.star.incomplete": "✏️ incomplete",
    "prompts.star.missing": "Missing: {fields}",
    "prompts.star.situation.q": "Situation (context, 1-3 sentences)",
    "prompts.star.task.q": "Task (your responsibility)",
    "prompts.star.action.q": "Action (what you did, concretely)",
    "prompts.star.result.q": "Result (what happened; a figure if there is one)",
    "prompts.star.use_for.q": (
        "What kind of questions is it good for? "
        "(e.g. teamwork, a hard technical problem)"
    ),
    "prompts.star.situation.label": "Situation",
    "prompts.star.task.label": "Task",
    "prompts.star.action.label": "Action",
    "prompts.star.result.label": "Result",
    "prompts.star.use_for.label": "Good for",
    "prompts.star.situation.name": "situation",
    "prompts.star.task.name": "task",
    "prompts.star.action.name": "action",
    "prompts.star.result.name": "result",
    "prompts.star.use_for.name": "what it is good for",
    "prompts.star.situation.edit": "Situation",
    "prompts.star.task.edit": "Task",
    "prompts.star.action.edit": "Action",
    "prompts.star.result.edit": "Result",
    "prompts.star.use_for.edit": "Use",
    "prompts.star.all_started": (
        "⭐ You have already started a story with every experience and project."
    ),
    "prompts.star.first": (
        "⭐ You have no STAR stories yet. Pick an experience or project and "
        "I will ask you, one message at a time."
    ),
    "prompts.star.another": "⭐ Pick another experience or project for a new story.",
    "prompts.star.your_answers": "The answers are yours; I make nothing up.",
    "prompts.star.intro": (
        "I will ask you 5 questions, one per message. Answer in your own words; "
        "/skip leaves a field empty. You have 30 min between answers."
    ),
    "prompts.star.profile": "What your profile says:",
    "prompts.star.list_head": "⭐ Your STAR stories:",
    "prompts.star.legend": "✅ complete · ✏️ parts missing ({placeholder}).",
    "prompts.star.btn.view_n": "👁 View {n}",
    "prompts.star.btn.edit_n": "✏️ Edit {n}",
    "prompts.star.btn.delete_n": "🗑 Delete {n}",
    "prompts.star.btn.add": "➕ Add another",
    "prompts.star.btn.edit": "✏️ Edit",
    "prompts.star.btn.delete": "🗑 Delete",
    "prompts.star.btn.back": "⬅️ Back",
    "prompts.star.btn.delete_yes": "🗑 Yes, delete",
    "prompts.star.btn.cancel": "Cancel",
}
ES: Final[dict[str, str]] = {
    "prompts.judge_rationale_lang": "Spanish",
    "prompts.prep_lang": "in Spanish (the candidate reads Spanish)",
    "prompts.prep_likely": "es probable",
    "prompts.facts_lang": "Spanish",
    "prompts.star.complete": "✅ completa",
    "prompts.star.incomplete": "✏️ incompleta",
    "prompts.star.missing": "Falta: {fields}",
    "prompts.star.situation.q": "Situación (contexto, 1-3 frases)",
    "prompts.star.task.q": "Tarea (tu responsabilidad)",
    "prompts.star.action.q": "Acción (qué hiciste tú, concreto)",
    "prompts.star.result.q": "Resultado (qué pasó; cifra si la hay)",
    "prompts.star.use_for.q": (
        "¿Para qué tipo de preguntas sirve? "
        "(p. ej. trabajo en equipo, problema técnico difícil)"
    ),
    "prompts.star.situation.label": "Situación",
    "prompts.star.task.label": "Tarea",
    "prompts.star.action.label": "Acción",
    "prompts.star.result.label": "Resultado",
    "prompts.star.use_for.label": "Sirve para",
    "prompts.star.situation.name": "situación",
    "prompts.star.task.name": "tarea",
    "prompts.star.action.name": "acción",
    "prompts.star.result.name": "resultado",
    "prompts.star.use_for.name": "para qué sirve",
    "prompts.star.situation.edit": "Situación",
    "prompts.star.task.edit": "Tarea",
    "prompts.star.action.edit": "Acción",
    "prompts.star.result.edit": "Resultado",
    "prompts.star.use_for.edit": "Uso",
    "prompts.star.all_started": (
        "⭐ Ya has empezado una historia con cada experiencia y proyecto."
    ),
    "prompts.star.first": (
        "⭐ Aún no tienes historias STAR. Elige una experiencia o proyecto y "
        "te iré preguntando, un mensaje cada vez."
    ),
    "prompts.star.another": "⭐ Elige otra experiencia o proyecto para una nueva historia.",
    "prompts.star.your_answers": "Las respuestas las pones tú; yo no invento nada.",
    "prompts.star.intro": (
        "Te haré 5 preguntas, una por mensaje. Responde con tus palabras; "
        "/skip deja un campo sin rellenar. Tienes 30 min entre respuestas."
    ),
    "prompts.star.profile": "Lo que consta en tu perfil:",
    "prompts.star.list_head": "⭐ Tus historias STAR:",
    "prompts.star.legend": "✅ completa · ✏️ faltan partes ({placeholder}).",
    "prompts.star.btn.view_n": "👁 Ver {n}",
    "prompts.star.btn.edit_n": "✏️ Editar {n}",
    "prompts.star.btn.delete_n": "🗑 Borrar {n}",
    "prompts.star.btn.add": "➕ Añadir otra",
    "prompts.star.btn.edit": "✏️ Editar",
    "prompts.star.btn.delete": "🗑 Borrar",
    "prompts.star.btn.back": "⬅️ Volver",
    "prompts.star.btn.delete_yes": "🗑 Sí, borrar",
    "prompts.star.btn.cancel": "Cancelar",
}
