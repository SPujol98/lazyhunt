"""STAR story bank: candidate selection from the claims DB, copy and keyboards.

No LLM anywhere: candidates are picked deterministically from the verified
claims, and the story fields are the owner's own words. User-facing text is
Spanish (the owner's language); identifiers and comments are English.

Callback payloads (``st:``):

- ``st:new:{key}``      start a story from a candidate
- ``st:v:{id}``         view a story
- ``st:e:{id}``         pick a field to edit
- ``st:f:{id}:{n}``     re-ask field ``n`` (index into :data:`STAR_FIELDS`)
- ``st:d:{id}``         ask to confirm the deletion
- ``st:dy:{id}``        delete (confirmed)
- ``st:add``            candidates not used yet
- ``st:l``              back to the list
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from cva.claimsdb import Claim, ClaimsDB
from cva.tracker import STAR_PLACEHOLDER, StarStory

ST: Final[str] = "st"
MAX_CANDIDATES: Final[int] = 6
_TITLE_MAX: Final[int] = 120

STAR_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("situation", "Situación (contexto, 1-3 frases)"),
    ("task", "Tarea (tu responsabilidad)"),
    ("action", "Acción (qué hiciste tú, concreto)"),
    ("result", "Resultado (qué pasó; cifra si la hay)"),
    (
        "use_for",
        (
            "¿Para qué tipo de preguntas sirve? "
            "(p. ej. trabajo en equipo, problema técnico difícil)"
        ),
    ),
)
_FIELD_LABELS: Final[dict[str, str]] = {
    "situation": "Situación",
    "task": "Tarea",
    "action": "Acción",
    "result": "Resultado",
    "use_for": "Sirve para",
}
_FIELD_NAMES_ES: Final[dict[str, str]] = {
    "situation": "situación",
    "task": "tarea",
    "action": "acción",
    "result": "resultado",
    "use_for": "para qué sirve",
}
_EDIT_LABELS: Final[tuple[str, ...]] = (
    "Situación",
    "Tarea",
    "Acción",
    "Resultado",
    "Uso",
)
_PROJECT_OVERVIEW: Final = re.compile(r"^proj-(.+)-overview$")


@dataclass(frozen=True)
class StarCandidate:
    """A story the owner could tell, grounded on real claim ids."""

    key: str
    title: str
    source_claim_ids: tuple[str, ...]


def star_candidates(db: ClaimsDB) -> list[StarCandidate]:
    """Up to six candidates: experience roles first, then the richest projects.

    Deterministic (claims order + claim counts), Spanish claims only, never
    invented: each title is cut from a real claim text.
    """
    groups: dict[str, list[Claim]] = {}
    overview_slugs = {
        m.group(1)
        for c in db.claims
        if c.category == "project" and (m := _PROJECT_OVERVIEW.match(c.id))
    }
    for claim in db.claims:
        if claim.id.endswith(("_en", "-ds")):
            continue
        key = _group_key(claim, overview_slugs)
        if key is not None:
            groups.setdefault(key, []).append(claim)
    experience = [k for k in groups if k.startswith("exp-")]
    projects = sorted(
        (k for k in groups if not k.startswith("exp-")),
        key=lambda k: -len(groups[k]),  # stable: ties keep claims order
    )
    return [
        _candidate(key, groups[key])
        for key in [*experience, *projects][:MAX_CANDIDATES]
    ]


def _group_key(claim: Claim, overview_slugs: set[str]) -> str | None:
    if claim.category == "experience":
        parts = claim.id.split("-")
        return f"exp-{parts[1]}" if len(parts) > 2 else claim.id
    if claim.category == "project":
        for slug in overview_slugs:
            if claim.id.startswith(f"proj-{slug}-"):
                return f"proj-{slug}"
        return claim.id
    return None


def _candidate(key: str, claims: list[Claim]) -> StarCandidate:
    head = next((c for c in claims if c.id.endswith(("-role", "-overview"))), claims[0])
    if key.startswith("exp-"):
        title = head.text.rsplit(", ", 1)[0]
    else:
        title = head.text.split(" (")[0].split(":")[0]
    return StarCandidate(
        key=key,
        title=title.strip()[:_TITLE_MAX],
        source_claim_ids=tuple(c.id for c in claims),
    )


def candidates_message(candidates: Sequence[StarCandidate], *, first: bool) -> str:
    """Intro for the candidate picker."""
    if not candidates:
        return "⭐ Ya has empezado una historia con cada experiencia y proyecto."
    intro = (
        "⭐ Aún no tienes historias STAR. Elige una experiencia o proyecto y "
        "te iré preguntando, un mensaje cada vez."
        if first
        else "⭐ Elige otra experiencia o proyecto para una nueva historia."
    )
    return intro + "\nLas respuestas las pones tú; yo no invento nada."


def candidates_keyboard(candidates: Sequence[StarCandidate]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"➕ {c.title}"[:60], callback_data=f"{ST}:new:{c.key}"
                )
            ]
            for c in candidates
        ]
    )


def intro_message(story: StarStory, source_texts: Sequence[str]) -> str:
    """Opening of the guided flow: the real claims the story rests on."""
    lines = [
        f"⭐ «{story.title}»",
        (
            "Te haré 5 preguntas, una por mensaje. Responde con tus palabras; "
            "/skip deja un campo sin rellenar. Tienes 30 min entre respuestas."
        ),
    ]
    if source_texts:
        lines.append("\nLo que consta en tu perfil:")
        lines.extend(f"• {t[:160]}" for t in source_texts[:4])
    return "\n".join(lines)


def question_message(position: int, total: int, field: str) -> str:
    return f"{position}/{total} · {field_question(field)}"


def field_question(field: str) -> str:
    return next(q for f, q in STAR_FIELDS if f == field)


def field_label(field: str) -> str:
    """Spanish display name of a STAR field key (situation -> situación)."""
    return _FIELD_NAMES_ES.get(field, field)


def story_message(story: StarStory) -> str:
    mark = "✅ completa" if story.complete else "✏️ incompleta"
    lines = [f"⭐ #{story.id} · {story.title} · {mark}"]
    for field, _question in STAR_FIELDS:
        lines.append(f"{_FIELD_LABELS[field]}: {getattr(story, field)}")
    missing = [
        field_label(f) for f, _q in STAR_FIELDS if getattr(story, f) == STAR_PLACEHOLDER
    ]
    if missing:
        lines.append(f"Falta: {', '.join(missing)}")
    return "\n".join(lines)


def list_message(stories: Sequence[StarStory]) -> str:
    lines = ["⭐ Tus historias STAR:"]
    for number, story in enumerate(stories, start=1):
        lines.append(f"{number}. {'✅' if story.complete else '✏️'} {story.title}")
    lines.append(f"\n✅ completa · ✏️ faltan partes ({STAR_PLACEHOLDER}).")
    return "\n".join(lines)


def list_keyboard(stories: Sequence[StarStory]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(f"👁 Ver {n}", callback_data=f"{ST}:v:{s.id}"),
            InlineKeyboardButton(f"✏️ Editar {n}", callback_data=f"{ST}:e:{s.id}"),
            InlineKeyboardButton(f"🗑 Borrar {n}", callback_data=f"{ST}:d:{s.id}"),
        ]
        for n, s in enumerate(stories, start=1)
    ]
    rows.append([InlineKeyboardButton("➕ Añadir otra", callback_data=f"{ST}:add")])
    return InlineKeyboardMarkup(rows)


def story_keyboard(story_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Editar", callback_data=f"{ST}:e:{story_id}"),
                InlineKeyboardButton("🗑 Borrar", callback_data=f"{ST}:d:{story_id}"),
            ],
            [InlineKeyboardButton("⬅️ Volver", callback_data=f"{ST}:l")],
        ]
    )


def edit_keyboard(story_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{ST}:f:{story_id}:{i}")
        for i, label in enumerate(_EDIT_LABELS)
    ]
    return InlineKeyboardMarkup(
        [
            buttons[:3],
            buttons[3:],
            [InlineKeyboardButton("⬅️ Volver", callback_data=f"{ST}:l")],
        ]
    )


def delete_keyboard(story_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Sí, borrar", callback_data=f"{ST}:dy:{story_id}"
                ),
                InlineKeyboardButton("Cancelar", callback_data=f"{ST}:l"),
            ]
        ]
    )
