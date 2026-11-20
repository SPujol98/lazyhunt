"""The profile, viewed and edited after onboarding (onboarding-v2 T10, S9/S10).

The extension Options page has a "Profile" tab that reads and writes, through
the bridge (``GET``/``POST /profile``, :mod:`cva.bridge`), the answers of
the ``/setup`` questionnaire that live in the profile files:

==============  =======================================================  ================
section         fields (``values`` of a POST)                            written to
==============  =======================================================  ================
``positions``   ``families`` (built-in ids), ``kept_families`` (the      ``[families]``
                other declared ids that stay), ``custom_titles``
``level``       ``level``                                                ``[search]``
``background``  ``spoken_languages`` ``[{language, level}]``, ``skills``, the ``setup-*``
                ``experience`` ``{years, role, company}``, ``education``  claims
``location``    ``work_modes``, ``work_city``, ``country``               ``[search]``,
                                                                         ``[location]``
``salary``      ``min_salary`` (``null``: none), ``currency``            ``[search]``
``offer_langs`` ``offer_langs``                                          ``[gates]``
==============  =======================================================  ================

A POST is exactly a ``/setup <section>`` Confirm: one section, every field of
it, validated with the questionnaire's own ``clean_*`` helpers and limits
(:mod:`cva.onboarding.answers`), merged into the current answers
(:func:`cva.onboarding.prefill.current_answers`) and written through
:func:`cva.onboarding.commit.commit_all` (``write_answers(section=...)``, the
``setup-*`` claims merge of :data:`CONFIRM_HOOKS`, all or nothing), then the
running bot reloads ``preferences.toml``. Hand-written claims (any id without
the ``setup-`` prefix) are listed read-only and keep their exact bytes.

A refused request raises :class:`InvalidProfile` with one error code per
field (:data:`ERROR_CODES`); nothing is written then.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from cva.families import BUILTIN_REGISTRY
from cva.i18n import Lang
from cva.onboarding import answers as A
from cva.onboarding.commit import WRITE_LOCK, Hook, commit_all
from cva.onboarding.prefill import current_answers
from cva.onboarding.profile_gen import SETUP_PREFIX, confirm_hook
from cva.profile_identity import ProfileError

EDITABLE_SECTIONS: Final[tuple[str, ...]] = (
    "positions",
    "level",
    "background",
    "location",
    "salary",
    "offer_langs",
)
SECTION_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "positions": ("families", "kept_families", "custom_titles"),
    "level": ("level",),
    "background": ("spoken_languages", "skills", "experience", "education"),
    "location": ("work_modes", "work_city", "country"),
    "salary": ("min_salary", "currency"),
    "offer_langs": ("offer_langs",),
}
# Field error codes (the Options page shows each one's own text).
ERROR_CODES: Final[tuple[str, ...]] = (
    "unknown_field",  # not a field of the section (or of the request)
    "required",  # a field of the section is missing
    "type",  # wrong JSON type
    "choice",  # not one of the offered values
    "pick_one",  # an empty selection where one is needed
    "text",  # empty, too long or with control characters
    "titles",
    "skills",
    "languages",
    "salary",
    "currency",
    "company_without_role",
)
_SEPARATORS: Final[str] = ",;\n"

CONFIRM_HOOKS: list[Hook] = [confirm_hook]


class InvalidProfile(Exception):
    """A refused request: ``fields`` maps each bad field to its error code."""

    def __init__(self, fields: Mapping[str, str]) -> None:
        super().__init__(", ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = dict(fields)


class _Bad(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# --- one value ----------------------------------------------------------------------


def _text(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise _Bad("type")
    cleaned = A.clean_text(value)
    if cleaned is None:
        raise _Bad("text")
    return cleaned


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Bad("type")
    return value


def _choices(value: Any, allowed: Sequence[str], *, need_one: bool = True) -> list[str]:
    """A selection of ``allowed`` values, in ``allowed`` order."""
    chosen = _strings(value)
    if any(v not in allowed for v in chosen):
        raise _Bad("choice")
    if need_one and not chosen:
        raise _Bad("pick_one")
    return [v for v in allowed if v in chosen]


def _list_text(
    items: list[str], clean: Callable[[str], list[str] | None], code: str
) -> list[str]:
    """Items checked by the questionnaire's list cleaner (as if typed one per
    line); an item holding a separator would be split there, so it is refused."""
    if any(sep in item for item in items for sep in _SEPARATORS):
        raise _Bad(code)
    cleaned = clean("\n".join(items)) if items else None
    if cleaned is None:
        raise _Bad(code)
    return cleaned


def _titles(value: Any) -> list[str]:
    items = _strings(value)
    if not items:
        return []
    if len(items) > A.MAX_TITLES:
        raise _Bad("titles")
    titles = _list_text(items, A.clean_titles, "titles")
    if len(titles) != len({" ".join(i.split()) for i in items}):
        raise _Bad("titles")  # an item the cleaner dropped (too long)
    return titles


def _level(value: Any) -> str:
    if not isinstance(value, str):
        raise _Bad("type")
    if value not in A.LEVELS:
        raise _Bad("choice")
    return value


def _salary(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int:  # bool is an int too
        raise _Bad("type")
    amount = A.clean_salary(str(value))
    if amount is None:
        raise _Bad("salary")
    return amount


def _currency(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Bad("type")
    code = A.clean_currency(value)
    if code is None:
        raise _Bad("currency")
    return code


def _spoken(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        raise _Bad("type")
    langs: list[str] = []
    other: list[str] = []
    levels: dict[str, str] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != {"language", "level"}
            or not isinstance(item["language"], str)
            or not isinstance(item["level"], str)
        ):
            raise _Bad("type")
        if item["level"] not in A.LANGUAGE_LEVELS:
            raise _Bad("choice")
        name = item["language"]
        key = name if name in A.SPOKEN_LANGUAGES else A.common_language(name.strip())
        if key is None:
            typed = _list_text([name], A.clean_languages, "languages")
            key = typed[0]
            if key.casefold() not in {o.casefold() for o in other}:
                other.append(key)
        elif key not in langs:
            langs.append(key)
        levels.setdefault(key, item["level"])
    if not langs and not other:
        raise _Bad("pick_one")
    if len(other) > A.MAX_OTHER_LANGUAGES:
        raise _Bad("languages")
    return {
        "spoken_langs": [c for c in A.SPOKEN_LANGUAGES if c in langs],
        "spoken_other": other,
        "spoken_levels": levels,
    }


def _experience(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not set(value) <= {"years", "role", "company"}:
        raise _Bad("type")
    years = value.get("years")
    if not isinstance(years, str):
        raise _Bad("type" if years is not None else "required")
    if years not in A.EXPERIENCE_YEARS:
        raise _Bad("choice")
    role = _text(value.get("role"), optional=True)
    company = _text(value.get("company"), optional=True)
    if company is not None and role is None:
        raise _Bad("company_without_role")
    return {"exp_years": years, "last_role": role, "last_company": company}


# --- one request --------------------------------------------------------------------


def _section_answers(
    section: str, values: Mapping[str, Any], declared: Sequence[str]
) -> dict[str, Any]:
    """The answers ``values`` give (``/setup`` answer keys), or :class:`InvalidProfile`."""
    errors: dict[str, str] = {
        key: "unknown_field" for key in values if key not in SECTION_FIELDS[section]
    }
    errors.update(
        {key: "required" for key in SECTION_FIELDS[section] if key not in values}
    )
    if errors:
        raise InvalidProfile(errors)
    out: dict[str, Any] = {}

    def field(key: str, convert: Callable[[Any], Any]) -> None:
        try:
            converted = convert(values[key])
        except _Bad as bad:
            errors[key] = bad.code
            return
        if isinstance(converted, dict) and key in ("spoken_languages", "experience"):
            out.update(converted)
        else:
            out[key] = converted

    if section == "positions":
        field("families", lambda v: _choices(v, BUILTIN_REGISTRY.ids, need_one=False))
        field("kept_families", lambda v: _choices(v, declared, need_one=False))
        field("custom_titles", _titles)
        if not errors and not (
            out["families"] or out["kept_families"] or out["custom_titles"]
        ):
            errors["families"] = "pick_one"
    elif section == "level":
        field("level", _level)
    elif section == "background":
        field("spoken_languages", _spoken)
        field("skills", lambda v: _list_text(_strings(v), A.clean_skills, "skills"))
        field("experience", _experience)
        field("education", lambda v: _text(v, optional=True))
    elif section == "location":
        field("work_modes", lambda v: _choices(v, A.WORK_MODES))
        field("work_city", _text)
        field("country", _text)
    elif section == "salary":
        field("min_salary", _salary)
        field("currency", _currency)
        if not errors:
            if out["min_salary"] is None:
                out["currency"] = None
            elif out["currency"] is None:
                out["currency"] = "EUR"
    else:  # offer_langs
        field("offer_langs", lambda v: _choices(v, A.OFFER_LANGUAGES))
    if errors:
        raise InvalidProfile(errors)
    return out


def parse_request(payload: Any, declared: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """``(section, answers)`` of a ``POST /profile`` body
    ``{"section": ..., "values": {...}}``; ``declared`` are the ids of the
    non-built-in families ``preferences.toml`` declares."""
    if not isinstance(payload, dict):
        raise InvalidProfile({"body": "type"})
    errors = {
        key: "unknown_field" for key in payload if key not in ("section", "values")
    }
    section = payload.get("section")
    if section not in EDITABLE_SECTIONS:
        errors["section"] = "required" if section is None else "choice"
    values = payload.get("values")
    if not isinstance(values, dict):
        errors["values"] = "required" if values is None else "type"
    if errors or not isinstance(values, dict):
        raise InvalidProfile(errors)
    return str(section), _section_answers(str(section), values, declared)


# --- the profile ----------------------------------------------------------------------


def _declared(profile_dir: Path) -> tuple[list[str], list[tuple[str, str]]]:
    return A.families_in_effect(profile_dir / "preferences.toml")


def _claims(profile_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(setup-* claims, other claims)`` of ``claims.json`` as ``{id,
    category, text}`` (an unreadable file lists none)."""
    try:
        data = json.loads((profile_dir / "claims.json").read_text("utf-8"))
    except (OSError, ValueError):
        return [], []
    setup: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        category = item.get("category")
        text = item.get("text")
        shown = {
            "id": item["id"],
            "category": category if isinstance(category, str) else None,
            "text": text if isinstance(text, str) else json.dumps(text),
        }
        (setup if item["id"].startswith(SETUP_PREFIX) else other).append(shown)
    return setup, other


def profile_view(profile_dir: Path) -> dict[str, Any]:
    """What ``GET /profile`` answers: the current values per section, the
    choices and limits of the questionnaire and the claims."""
    with WRITE_LOCK:
        answers = current_answers(profile_dir)
        builtin, others = _declared(profile_dir)
        setup, other = _claims(profile_dir)
    bg = A.background(answers)
    return {
        "sections": {
            "positions": {
                "families": builtin,
                "kept_families": [fid for fid, _ in others],
                "custom_titles": [],
            },
            "level": {"level": answers.get("level")},
            "background": {
                key: bg[key]
                for key in ("spoken_languages", "skills", "experience", "education")
            },
            "location": {
                "work_modes": list(answers.get("work_modes") or []),
                "work_city": answers.get("work_city"),
                "country": answers.get("country"),
            },
            "salary": {
                "min_salary": answers.get("min_salary"),
                "currency": answers.get("currency"),
            },
            "offer_langs": {"offer_langs": list(answers.get("offer_langs") or [])},
        },
        "options": {
            "families": [
                {"id": f.id, "label": f.label, "builtin": True}
                for f in BUILTIN_REGISTRY.families
            ]
            + [{"id": fid, "label": label, "builtin": False} for fid, label in others],
            "levels": list(A.LEVELS),
            "work_modes": list(A.WORK_MODES),
            "offer_languages": list(A.OFFER_LANGUAGES),
            "spoken_languages": list(A.SPOKEN_LANGUAGES),
            "language_levels": list(A.LANGUAGE_LEVELS),
            "experience_years": list(A.EXPERIENCE_YEARS),
            "currencies": list(A.CURRENCIES),
        },
        "limits": {
            "text": A.MAX_TEXT,
            "titles": A.MAX_TITLES,
            "skills": A.MAX_SKILLS,
            "skill_length": A.MAX_SKILL_LEN,
            "typed_languages": A.MAX_OTHER_LANGUAGES,
            "language_length": A.MAX_LANGUAGE_LEN,
        },
        "claims": {"setup": setup, "other": other},
    }


def _record_lang(profile_dir: Path, default: Lang) -> Lang:
    """The language of the last Confirm (the ``setup-*`` claim texts), else ``default``."""
    try:
        record = json.loads((profile_dir / A.ANSWERS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return default
    lang = record.get("ui_language") if isinstance(record, dict) else None
    return "es" if lang == "es" else "en" if lang == "en" else default


def save(
    profile_dir: Path,
    payload: Any,
    default_lang: Lang,
    reload: Callable[[], object] | None = None,
) -> dict[str, Any]:
    """Validate and write one section (module docstring), reload the
    preferences and return ``{"saved": true, "reloaded": bool, "profile":
    <the new profile_view>}`` (``reloaded`` is false when the running bot
    refused the new ``preferences.toml``; the files stay written).

    Raises :class:`InvalidProfile` (nothing written),
    :class:`~cva.onboarding.commit.ConfirmFailed` (every file put back) or
    :class:`~cva.profile_identity.ProfileError` when ``preferences.toml``
    cannot be read (nothing written). The whole call holds
    :data:`~cva.onboarding.commit.WRITE_LOCK`."""
    with WRITE_LOCK:
        builtin, others = _declared(profile_dir)
        section, patch = parse_request(payload, [fid for fid, _ in others])
        answers = {
            **current_answers(profile_dir),
            "families": builtin,
            "kept_families": [fid for fid, _ in others],
            **patch,
        }
        lang = _record_lang(profile_dir, default_lang)
        commit_all(profile_dir, answers, [], {}, lang, section, CONFIRM_HOOKS)
        reloaded = True
        if reload is not None:
            try:
                reload()
            except ProfileError:
                reloaded = False
        return {
            "saved": True,
            "reloaded": reloaded,
            "profile": profile_view(profile_dir),
        }
