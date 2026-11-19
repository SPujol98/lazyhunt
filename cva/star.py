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
from cva.i18n import LEGACY_LANG, Lang, t
from cva.tracker import STAR_PLACEHOLDER, StarStory

ST: Final[str] = "st"
MAX_CANDIDATES: Final[int] = 6
_TITLE_MAX: Final[int] = 120

_FIELD_KEYS: Final[tuple[str, ...]] = (
    "situation",
    "task",
    "action",
    "result",
    "use_for",
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


def candidates_message(
    candidates: Sequence[StarCandidate], *, first: bool, lang: Lang = LEGACY_LANG
) -> str:
    """Intro for the candidate picker."""
    if not candidates:
        return t("prompts.star.all_started", lang)
    intro = t("prompts.star.first" if first else "prompts.star.another", lang)
    return intro + "\n" + t("prompts.star.your_answers", lang)


def candidates_keyboard(
    candidates: Sequence[StarCandidate], lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    """One ➕ button per candidate; titles are data, so ``lang`` changes nothing."""
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


def intro_message(
    story: StarStory, source_texts: Sequence[str], lang: Lang = LEGACY_LANG
) -> str:
    """Opening of the guided flow: the real claims the story rests on."""
    lines = [f"⭐ «{story.title}»", t("prompts.star.intro", lang)]
    if source_texts:
        lines.append("\n" + t("prompts.star.profile", lang))
        lines.extend(f"• {t[:160]}" for t in source_texts[:4])
    return "\n".join(lines)


def star_fields(lang: Lang = LEGACY_LANG) -> tuple[tuple[str, str], ...]:
    """(field key, question text) for every STAR field, in ask order."""
    return tuple((f, t(f"prompts.star.{f}.q", lang)) for f in _FIELD_KEYS)


STAR_FIELDS: Final[tuple[tuple[str, str], ...]] = star_fields(LEGACY_LANG)


def question_message(
    position: int, total: int, field: str, lang: Lang = LEGACY_LANG
) -> str:
    return f"{position}/{total} · {field_question(field, lang)}"


def field_question(field: str, lang: Lang = LEGACY_LANG) -> str:
    return next(q for f, q in star_fields(lang) if f == field)


def field_label(field: str, lang: Lang = LEGACY_LANG) -> str:
    """Display name of a STAR field key (es: situation -> situación)."""
    return t(f"prompts.star.{field}.name", lang) if field in _FIELD_KEYS else field


def story_message(story: StarStory, lang: Lang = LEGACY_LANG) -> str:
    mark = t(
        "prompts.star.complete" if story.complete else "prompts.star.incomplete", lang
    )
    lines = [f"⭐ #{story.id} · {story.title} · {mark}"]
    for field in _FIELD_KEYS:
        lines.append(
            f"{t(f'prompts.star.{field}.label', lang)}: {getattr(story, field)}"
        )
    missing = [
        field_label(f, lang)
        for f in _FIELD_KEYS
        if getattr(story, f) == STAR_PLACEHOLDER
    ]
    if missing:
        lines.append(t("prompts.star.missing", lang, fields=", ".join(missing)))
    return "\n".join(lines)


def list_message(stories: Sequence[StarStory], lang: Lang = LEGACY_LANG) -> str:
    lines = [t("prompts.star.list_head", lang)]
    for number, story in enumerate(stories, start=1):
        lines.append(f"{number}. {'✅' if story.complete else '✏️'} {story.title}")
    lines.append("\n" + t("prompts.star.legend", lang, placeholder=STAR_PLACEHOLDER))
    return "\n".join(lines)


def list_keyboard(
    stories: Sequence[StarStory], lang: Lang = LEGACY_LANG
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                t("prompts.star.btn.view_n", lang, n=n), callback_data=f"{ST}:v:{s.id}"
            ),
            InlineKeyboardButton(
                t("prompts.star.btn.edit_n", lang, n=n), callback_data=f"{ST}:e:{s.id}"
            ),
            InlineKeyboardButton(
                t("prompts.star.btn.delete_n", lang, n=n),
                callback_data=f"{ST}:d:{s.id}",
            ),
        ]
        for n, s in enumerate(stories, start=1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                t("prompts.star.btn.add", lang), callback_data=f"{ST}:add"
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def story_keyboard(story_id: int, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("prompts.star.btn.edit", lang), callback_data=f"{ST}:e:{story_id}"
                ),
                InlineKeyboardButton(
                    t("prompts.star.btn.delete", lang),
                    callback_data=f"{ST}:d:{story_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    t("prompts.star.btn.back", lang), callback_data=f"{ST}:l"
                )
            ],
        ]
    )


def edit_keyboard(story_id: int, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=f"{ST}:f:{story_id}:{i}")
        for i, label in enumerate(
            t(f"prompts.star.{f}.edit", lang) for f in _FIELD_KEYS
        )
    ]
    return InlineKeyboardMarkup(
        [
            buttons[:3],
            buttons[3:],
            [
                InlineKeyboardButton(
                    t("prompts.star.btn.back", lang), callback_data=f"{ST}:l"
                )
            ],
        ]
    )


def delete_keyboard(story_id: int, lang: Lang = LEGACY_LANG) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t("prompts.star.btn.delete_yes", lang),
                    callback_data=f"{ST}:dy:{story_id}",
                ),
                InlineKeyboardButton(
                    t("prompts.star.btn.cancel", lang), callback_data=f"{ST}:l"
                ),
            ]
        ]
    )
