"""A starter base CV and a base cover letter from the ``/setup`` answers
(beta3-ux T4, S1/S2 P2).

For a user without a CV, the base-CV step offers "Create one with lazyhunt":
:func:`starter_cv` turns the answers into a :class:`cva.cvgen.starter.StarterCv`
for one slot (family × CV language) and :func:`create_pending_cv` renders it
into the pending upload area, validated exactly like an uploaded PDF
(:func:`cva.onboarding.cv_upload.accept_download`), so Confirm stores it with
the same slot name and manifest sha256 and delivery works unchanged.

For a profile without ``cover_letter_base.toml``, :func:`write_cover_letter`
writes one (``es``, ``en`` and ``ca``, the languages the loader requires)
from the same answers, inside Confirm's snapshot, and never over an existing
file.

Nothing is invented: every value comes from an answer (the target position
of a built-in family is its plain English job title); sections or sentences
without an answer are left out.
"""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from cva.cvgen.starter import (
    StarterCv,
    Template,
    language_line,
    profile_line,
    render_starter_cv,
)
from cva.fsutil import atomic_write
from cva.onboarding import answers as A
from cva.onboarding import cv_upload as U
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import load_letter_bases
from cva.profile_writer import toml_document

# The target position a built-in family stands for (its CV role -> title).
BUILTIN_TITLES: Final[dict[str, str]] = {
    "AI": "AI Engineer",
    "BACKEND": "Backend Developer",
}
LETTER_FILE: Final[str] = "cover_letter_base.toml"
_MAX_LETTER_SKILLS: Final[int] = 6


def _text(answers: Mapping[str, Any], key: str) -> str | None:
    value = answers.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def slot_title(slot: U.Slot) -> str:
    """The target position of a slot: a built-in family's job title, else
    the family's label (the user's own title)."""
    role = slot.key.rsplit("_", 1)[0]
    return BUILTIN_TITLES.get(role, slot.label)


def _contact(answers: Mapping[str, Any]) -> tuple[str, ...]:
    linkedin = _text(answers, "linkedin")
    if linkedin is not None:
        linkedin = linkedin.removeprefix("https://").removeprefix("http://")
    values = (
        _text(answers, "email"),
        _text(answers, "phone"),
        _text(answers, "city"),
        linkedin,
    )
    return tuple(v for v in values if v)


def starter_cv(answers: Mapping[str, Any], slot: U.Slot) -> StarterCv:
    """The starter CV of ``slot`` from ``answers`` (module docstring)."""
    bg = A.background(answers)
    headline = slot_title(slot)
    lang = slot.lang
    experience = bg["experience"]
    years = experience["years"] if experience is not None else None
    role = experience["role"] if experience is not None else None
    company = experience["company"] if experience is not None else None
    level = answers.get("level") if answers.get("level") in A.LEVELS else None
    return StarterCv(
        name=_text(answers, "name") or "",
        headline=headline,
        lang=lang,
        contact=_contact(answers),
        profile=profile_line(headline, level, years, lang),
        experience=((role, company),) if role else (),
        education=tuple(bg["education"]),
        skills=tuple(bg["skills"]),
        languages=tuple(
            language_line(item["language"], item["level"], lang)
            for item in bg["spoken_languages"]
        ),
    )


def create_pending_cv(
    profile_dir: Path, answers: Mapping[str, Any], slot: U.Slot, template: Template
) -> tuple[str, int]:
    """Render the starter CV of ``slot`` as its pending upload; returns
    ``(sha256, pages)`` like an upload. :class:`cva.onboarding.cv_upload.UploadError`
    when the result is not an acceptable PDF; ``CvgenError`` / ``OSError``
    when it cannot be rendered."""
    tmp = U.new_temp(profile_dir)
    try:
        render_starter_cv(starter_cv(answers, slot), template, tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return U.accept_download(tmp, profile_dir, slot.key)


# --- the base cover letter ----------------------------------------------------------

_JOIN: Final[dict[str, str]] = {"en": " or ", "es": " o ", "ca": " o "}
_MODES: Final[dict[str, dict[str, str]]] = {
    "en": {"remote": "remote", "hybrid": "hybrid", "onsite": "on-site"},
    "es": {"remote": "en remoto", "hybrid": "en híbrido", "onsite": "presencial"},
    "ca": {"remote": "en remot", "hybrid": "en híbrid", "onsite": "presencial"},
}


def _positions(
    answers: Mapping[str, Any], declared: list[tuple[str, str]]
) -> list[str]:
    titles = [
        BUILTIN_TITLES.get(fid.upper(), fid) for fid in answers.get("families") or []
    ]
    kept = answers.get("kept_families") or []
    titles += [label for fid, label in declared if fid in kept]
    titles += [str(v) for v in answers.get("custom_titles") or []]
    return list(dict.fromkeys(titles))


def _either(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + _JOIN[lang] + items[-1]


def letter_texts(
    answers: Mapping[str, Any], declared: list[tuple[str, str]] | None = None
) -> dict[str, dict[str, str]]:
    """The ``[es]``/``[en]``/``[ca]`` tables of ``cover_letter_base.toml``
    from ``answers``: short, plain sentences with only what was answered."""
    name = _text(answers, "name") or ""
    positions = _positions(answers, declared or [])
    bg = A.background(answers)
    skills = bg["skills"][:_MAX_LETTER_SKILLS]
    exp = bg["experience"]
    role = exp["role"] if exp is not None else None
    company = exp["company"] if exp is not None else None
    years = exp["years"] if exp is not None else None
    level = answers.get("level") if answers.get("level") in A.LEVELS else None
    modes = [m for m in A.WORK_MODES if m in (answers.get("work_modes") or [])]
    tables: dict[str, dict[str, str]] = {}
    for lang in ("es", "en", "ca"):
        tables[lang] = _letter(
            lang,
            name,
            _either(positions, lang),
            level,
            years,
            role,
            company,
            skills,
            modes,
        )
    return tables


def _letter(
    lang: str,
    name: str,
    headline: str,
    level: str | None,
    years: str | None,
    role: str | None,
    company: str | None,
    skills: list[str],
    modes: list[str],
) -> dict[str, str]:
    """One language's table (:func:`letter_texts`)."""
    profile = profile_line(headline, level, years, lang) if headline else ""
    words = _LETTER_WORDS[lang]
    intro = ""
    if profile:
        intro = words["intro"].format(name=name, profile=profile)
    facts = []
    if role:
        facts.append(
            (words["role_at"] if company else words["role"]).format(
                role=role, company=company
            )
        )
    if skills:
        facts.append(words["skills"].format(skills=", ".join(skills)))
    looking = ""
    if headline:
        mode_words = _either([_MODES[lang][m] for m in modes], lang)
        looking = words["looking"].format(position=headline)
        looking += f", {mode_words}." if mode_words else "."
    paragraph1 = " ".join(p for p in (intro, *facts) if p) or name
    open_paragraph = " ".join(p for p in (looking, *facts) if p) or paragraph1
    return {
        "salutation": words["salutation"],
        "salutation_generic": words["salutation_generic"],
        "paragraph1": paragraph1,
        "closing": words["closing"],
        "signoff": f"{words['signoff']}\n{name}",
        "open_salutation": words["open_salutation"],
        "open_paragraph": open_paragraph,
    }


_LETTER_WORDS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "intro": "I am {name}: {profile}",
        "role": "My last role was {role}.",
        "role_at": "My last role was {role} at {company}.",
        "skills": "I work mainly with {skills}.",
        "looking": "I am looking for a position as {position}",
        "salutation": "Dear {company} team,",
        "salutation_generic": "Dear hiring team,",
        "closing": "I would be glad to tell you more in an interview.",
        "signoff": "Best regards,",
        "open_salutation": "Hello,",
    },
    "es": {
        "intro": "Soy {name}: {profile}",
        "role": "Mi último puesto fue {role}.",
        "role_at": "Mi último puesto fue {role} en {company}.",
        "skills": "Trabajo sobre todo con {skills}.",
        "looking": "Busco un puesto de {position}",
        "salutation": "Hola equipo de {company},",
        "salutation_generic": "Hola equipo,",
        "closing": "Me encantaría contároslo en una entrevista.",
        "signoff": "Un saludo,",
        "open_salutation": "Hola,",
    },
    "ca": {
        "intro": "Sóc {name}: {profile}",
        "role": "La meva última feina va ser {role}.",
        "role_at": "La meva última feina va ser {role} a {company}.",
        "skills": "Treballo sobretot amb {skills}.",
        "looking": "Busco una feina de {position}",
        "salutation": "Hola equip {company_de},",
        "salutation_generic": "Hola equip,",
        "closing": "M'encantaria explicar-vos-ho en una entrevista.",
        "signoff": "Salutacions,",
        "open_salutation": "Hola,",
    },
}


def letter_path(profile_dir: Path) -> Path:
    return resolve_profile_paths(profile_dir).letter_base


def has_cover_letter(profile_dir: Path) -> bool:
    return letter_path(profile_dir).exists()


def write_cover_letter(
    profile_dir: Path,
    answers: Mapping[str, Any],
    declared: list[tuple[str, str]] | None = None,
) -> bool:
    """Write ``cover_letter_base.toml`` from ``answers`` when the profile has
    none (``True``); an existing file is never touched (``False``). The text
    is checked with the letters' own loader before it is kept
    (:class:`~cva.profile_identity.ProfileError` otherwise, nothing left)."""
    path = letter_path(profile_dir)
    if path.exists():
        return False
    text = toml_document(letter_texts(answers, declared))
    tomllib.loads(text)
    atomic_write(path, text)
    try:
        load_letter_bases(path)
    except BaseException:
        os.unlink(path)
        raise
    return True
