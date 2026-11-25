"""Questionnaire answers -> profile files (onboarding-v2 T4, S7).

The answers of ``/setup`` are a plain JSON-able mapping (kept in the Telegram
``user_data`` while the conversation runs). On Confirm they are written with
the validated writers of :mod:`cva.profile_writer` (never hand-rolled TOML):

=====================  ======================================================
answer                 file / key
=====================  ======================================================
families (built-in)    ``preferences.toml [families.<id>]`` (``ai``/``backend``;
                       ``languages`` = accepted offer languages it can write)
kept_families          the families already declared in ``preferences.toml``
                       that stay: every key kept as written; one left out is
                       removed
custom_titles          ``[families.<slug>]``: ``label``, ``title_terms``,
                       ``search_roles``, ``languages``. Every built-in and
                       no own titles in an existing file that declares no
                       ``[families]``: still none declared (the built-ins
                       keep applying exactly as before)
level                  ``[search] level``
work_modes             ``[search] work_modes``
min_salary, currency   ``[search] min_salary`` / ``currency`` (removed on Skip)
work_city, country     ``[location] city`` / ``country``
offer_langs            ``[gates] allowed_languages``
name                   ``profile.toml [identity] name`` + ``file_slug``
email, phone, city,    ``onboarding_answers.json`` (``contact``): pending
linkedin               input for the claims the next setup step generates;
                       never in ``preferences.toml``
spoken_langs,          ``onboarding_answers.json`` (``background``) and the
spoken_other,          ``setup-*`` claims of ``claims.json``
spoken_levels, skills, (:mod:`cva.onboarding.profile_gen`)
exp_years, last_role,
last_company, education
ui_language            ``config.local.toml`` (the setup wizard's writer)
=====================  ======================================================

Every other key of the existing files is kept (the writers rebuild the file,
so hand-written comments are not; the previous file stays as ``<name>.bak``).

``/setup <section>`` (T9) writes through the same functions with ``section``
set: only the keys of that section are applied (``contact``: the name in
``profile.toml``; ``positions``: ``[families]``; ``level``, ``salary``,
``location``: their ``[search]`` / ``[location]`` keys; ``offer_langs``:
``[gates] allowed_languages``), a TOML
file whose document does not change is not rewritten at all (its bytes stay
as they were), and ``onboarding_answers.json`` records the ``section``.
"""

import json
import re
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cva.families import (
    BUILTIN_REGISTRY,
    CV_LANGUAGES,
    FAMILY_ID_MAX_LEN,
    FAMILY_ID_RE,
    normalize_title,
)
from cva.fsutil import atomic_write
from cva.i18n import Lang, t
from cva.onboarding.snapshot import write_target
from cva.profile_identity import identity_from_data
from cva.profile_prefs import preferences_from_data
from cva.profile_writer import (
    read_document,
    toml_document,
    write_preferences,
    write_profile,
)

# The setup wizard's line-preserving ``config.local.toml`` writer (it validates
# the result with tomllib before returning it): reused, not re-implemented.
from cva.setup.wizard import _toml_text as _config_toml_text

ANSWERS_FILE: Final[str] = "onboarding_answers.json"
ANSWERS_SCHEMA: Final[int] = 2  # 2: ``background`` (T8)
LEVELS: Final[tuple[str, ...]] = ("junior", "mid", "senior")
WORK_MODES: Final[tuple[str, ...]] = ("remote", "hybrid", "onsite")
OFFER_LANGUAGES: Final[tuple[str, ...]] = ("es", "en", "ca", "fr", "de", "it", "pt")
CURRENCIES: Final[tuple[str, ...]] = ("EUR", "USD", "GBP", "CHF")
MAX_TEXT: Final[int] = 100
MAX_TITLES: Final[int] = 10  # own job titles (positions)
# T8: the background questions (spoken languages, skills, experience).
SPOKEN_LANGUAGES: Final[tuple[str, ...]] = OFFER_LANGUAGES  # same names
LANGUAGE_LEVELS: Final[tuple[str, ...]] = ("native", "C2", "C1", "B2", "B1", "A2")
EXPERIENCE_YEARS: Final[tuple[str, ...]] = ("0", "lt1", "1-2", "3-5", "6-10", "10plus")
MAX_SKILLS: Final[int] = 30
MAX_EDUCATION: Final[int] = 5  # education entries (one ``setup-education-*`` each)
MAX_SKILL_LEN: Final[int] = 50
MAX_OTHER_LANGUAGES: Final[int] = 5
MAX_LANGUAGE_LEN: Final[int] = 40

# ``/setup <section>`` (T9): the sections, in questionnaire order.
SECTIONS: Final[tuple[str, ...]] = (
    "contact",
    "positions",
    "level",
    "background",
    "location",
    "salary",
    "offer_langs",
    "cvs",
)
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]{2,}")
_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"\+?[0-9][0-9 ().-]{5,24}")
_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/\S*)?", re.IGNORECASE
)
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]{3}")


# --- light validation (None = refused) ------------------------------------------


def _has_control(value: str) -> bool:
    """True when ``value`` holds a control character (``\\x00-\\x1f``, ``\\x7f``)."""
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def clean_text(text: str) -> str | None:
    """A one-line free-text answer (name, city...), 1-100 characters, no
    control characters (line breaks and tabs become spaces)."""
    value = " ".join(text.split())
    if _has_control(value):
        return None
    return value if 0 < len(value) <= MAX_TEXT else None


def clean_email(text: str) -> str | None:
    value = text.strip()
    if _has_control(value):
        return None
    return value if len(value) <= MAX_TEXT and _EMAIL_RE.fullmatch(value) else None


def clean_phone(text: str) -> str | None:
    value = " ".join(text.split())
    return value if _PHONE_RE.fullmatch(value) else None


def clean_url(text: str) -> str | None:
    """An http(s) URL; ``linkedin.com/in/x`` gets ``https://`` in front."""
    value = text.strip()
    if not re.match(r"https?://", value, re.IGNORECASE):
        value = f"https://{value}"
    if _has_control(value):
        return None
    return value if len(value) <= 200 and _URL_RE.fullmatch(value) else None


def clean_titles(text: str) -> list[str] | None:
    """Comma- or line-separated job titles (deduplicated, order kept)."""
    titles = [" ".join(p.split()) for p in re.split(r"[,\n;]", text)]
    if any(_has_control(title) for title in titles):
        return None
    kept = list(dict.fromkeys(t for t in titles if 0 < len(t) <= MAX_TEXT))
    return kept[:MAX_TITLES] or None


def _clean_list(text: str, max_items: int, max_len: int) -> list[str] | None:
    """Comma-, semicolon- or line-separated items, whitespace normalized and
    deduplicated ignoring case (first spelling kept). ``None`` when an item
    has a control character or is too long, when there are more than
    ``max_items`` or none at all."""
    items = [" ".join(p.split()) for p in re.split(r"[,\n;]", text)]
    if any(_has_control(item) or len(item) > max_len for item in items):
        return None
    kept: dict[str, str] = {}
    for item in items:
        if item:
            kept.setdefault(item.casefold(), item)
    values = list(kept.values())
    return values if 0 < len(values) <= max_items else None


def clean_skills(text: str) -> list[str] | None:
    """Main skills/stack: 1-30 items of at most 50 characters each."""
    return _clean_list(text, MAX_SKILLS, MAX_SKILL_LEN)


def clean_education(text: str) -> list[str] | None:
    """Education entries: one per line or separated by semicolons; without
    either, separated by commas. 1-5 entries of 1-100 characters each
    (deduplicated ignoring case, order kept); ``None`` otherwise."""
    separators = r"[\n;]" if re.search(r"[\n;]", text) else r","
    entries = [" ".join(p.split()) for p in re.split(separators, text)]
    if any(_has_control(entry) or len(entry) > MAX_TEXT for entry in entries):
        return None
    kept: dict[str, str] = {}
    for entry in entries:
        if entry:
            kept.setdefault(entry.casefold(), entry)
    values = list(kept.values())
    return values if 0 < len(values) <= MAX_EDUCATION else None


def education_entries(value: Any) -> list[str]:
    """The education answer as a list: the current shape (a list of entries)
    or the single line older answers files hold (kept whole, never split)."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def clean_languages(text: str) -> list[str] | None:
    """Spoken languages typed by name: 1-5 items of at most 40 characters."""
    return _clean_list(text, MAX_OTHER_LANGUAGES, MAX_LANGUAGE_LEN)


def clean_salary(text: str) -> int | None:
    """A whole gross annual amount: ``30000``, ``30.000``, ``30 000``, ``30k``."""
    value = text.strip().lower().replace(" ", "").replace(".", "").replace(",", "")
    multiplier = 1000 if value.endswith("k") else 1
    digits = value.removesuffix("k")
    if not digits.isdigit() or len(digits) > 9:
        return None
    return int(digits) * multiplier


def clean_currency(text: str) -> str | None:
    value = text.strip()
    return value.upper() if _CURRENCY_RE.fullmatch(value) else None


# Country answer (folded: lowercase, no accents) -> default currency. Anything
# else, an empty answer included, defaults to EUR (one currency by design).
_COUNTRY_CURRENCY: Final[dict[str, str]] = {
    **dict.fromkeys(
        (
            "uk",
            "u.k.",
            "gb",
            "united kingdom",
            "great britain",
            "britain",
            "england",
            "scotland",
            "wales",
            "northern ireland",
            "reino unido",
            "gran bretana",
            "inglaterra",
            "escocia",
            "gales",
        ),
        "GBP",
    ),
    **dict.fromkeys(
        (
            "us",
            "usa",
            "u.s.",
            "u.s.a.",
            "united states",
            "united states of america",
            "america",
            "estados unidos",
            "eeuu",
            "ee.uu.",
            "ee. uu.",
        ),
        "USD",
    ),
    **dict.fromkeys(
        ("ch", "switzerland", "suiza", "schweiz", "suisse", "svizzera"), "CHF"
    ),
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.strip().casefold())
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).split()
    )


def default_currency(country: Any) -> str:
    """The currency the currency step preselects for a country answer: GBP
    for the UK, USD for the US, CHF for Switzerland, EUR otherwise (Spain
    and the euro area included)."""
    if not isinstance(country, str):
        return "EUR"
    return _COUNTRY_CURRENCY.get(_fold(country), "EUR")


def common_language(name: str) -> str | None:
    """The code of a common spoken language typed by its code or its English
    or Spanish name (``English``, ``inglés``...), else ``None``."""
    folded = name.casefold()
    for code in SPOKEN_LANGUAGES:
        names = {
            code,
            *(t(f"onboarding.olang.{code}", lg).casefold() for lg in ("en", "es")),
        }
        if folded in names:
            return code
    return None


# --- derived values ---------------------------------------------------------------


def file_slug(name: str) -> str | None:
    """``profile.toml`` ``file_slug`` from a name: ``José Pérez`` -> ``Jose_Perez``."""
    decomposed = unicodedata.normalize("NFD", name)
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^0-9A-Za-z]+", "_", ascii_name).strip("_")
    return slug[:60] or None


def custom_family_id(titles: list[str]) -> str:
    """A ``[families.<id>]`` id for the user's own titles (from the first one)."""
    base = normalize_title(titles[0]).replace(" ", "_") if titles else ""
    if not base or not base[0].isalpha():
        base = f"f_{base}" if base else "custom"
    fid = base[:FAMILY_ID_MAX_LEN].rstrip("_")
    if BUILTIN_REGISTRY.get(fid) is not None:
        fid = f"{fid}_own"
    return fid if FAMILY_ID_RE.fullmatch(fid) else "custom"


def _cv_languages(offer_langs: list[str], allowed: tuple[str, ...]) -> list[str]:
    return [code for code in CV_LANGUAGES if code in offer_langs and code in allowed]


# --- documents --------------------------------------------------------------------


def declared_families(base: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(id, label)`` of every family declared in ``base`` (the current
    ``preferences.toml`` document), in file order; the label defaults to the id."""
    families = base.get("families")
    if not isinstance(families, Mapping):
        return []
    declared: list[tuple[str, str]] = []
    for fid, table in families.items():
        label = table.get("label") if isinstance(table, Mapping) else None
        declared.append(
            (str(fid), label if isinstance(label, str) and label else str(fid))
        )
    return declared


def family_selection(
    declared: list[tuple[str, str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """``(built-in ids, other (id, label))`` of the declared families: what a
    re-run offers preselected (``families`` / ``kept_families``)."""
    builtin = BUILTIN_REGISTRY.ids
    ids = dict(declared)
    return (
        [fid for fid in builtin if fid in ids],
        [(fid, label) for fid, label in declared if fid not in builtin],
    )


def families_in_effect(
    prefs_path: Path,
) -> tuple[list[str], list[tuple[str, str]]]:
    """:func:`family_selection` of the ``preferences.toml`` at ``prefs_path``:
    the families the profile actually uses. A file that declares no
    ``[families]`` uses the built-in ones (:mod:`cva.families`, filled from
    ``[title_filters]`` / ``[search_targets]``), so they are all selected; a
    missing file (the first ``/setup``) selects none. ``ProfileError`` when
    the file is unreadable."""
    if not prefs_path.is_file():
        return [], []
    declared = declared_families(read_document(prefs_path))
    if not declared:
        return list(BUILTIN_REGISTRY.ids), []
    return family_selection(declared)


def _applies(section: str | None, *names: str) -> bool:
    return section is None or section in names


def preferences_document(
    base: Mapping[str, Any],
    answers: Mapping[str, Any],
    section: str | None = None,
) -> dict[str, Any]:
    """``base`` (the current ``preferences.toml`` document) with the answers
    (only the keys of ``section`` when given: module docstring)."""
    doc: dict[str, Any] = {k: v for k, v in base.items()}
    if _applies(section, "positions"):
        _apply_families(doc, base, answers)
    if _applies(section, "level", "location", "salary"):
        search = dict(base.get("search", {}))
        if _applies(section, "location"):
            search["work_modes"] = list(answers["work_modes"])
        if _applies(section, "level"):
            search["level"] = answers["level"]
        if _applies(section, "salary"):
            if answers.get("min_salary") is None:
                search.pop("min_salary", None)
                search.pop("currency", None)
            else:
                search["min_salary"] = int(answers["min_salary"])
                search["currency"] = answers.get("currency") or "EUR"
        doc["search"] = search
    if _applies(section, "location"):
        location = dict(base.get("location", {}))
        location["city"] = answers["work_city"]
        location["country"] = answers["country"]
        doc["location"] = location
    if _applies(section, "offer_langs"):
        gates = dict(base.get("gates", {}))
        gates["allowed_languages"] = list(answers.get("offer_langs") or [])
        doc["gates"] = gates
    return doc


def _apply_families(
    doc: dict[str, Any], base: Mapping[str, Any], answers: Mapping[str, Any]
) -> None:
    offer_langs = list(answers.get("offer_langs") or [])
    old_families: Mapping[str, Any] = base.get("families", {})
    chosen = list(answers.get("families") or [])
    builtin_ids = {f.id for f in BUILTIN_REGISTRY.families}
    # Declared families keep the file order; no ``kept_families`` answer
    # (the user was never asked) keeps every one of them.
    kept = answers.get("kept_families")
    families: dict[str, Any] = {}
    for fid, old in old_families.items():
        if fid in builtin_ids:
            if fid in chosen:
                families[fid] = dict(old)
        elif kept is None or fid in kept:
            families[fid] = old  # untouched: every key as written
    for builtin in BUILTIN_REGISTRY.families:
        if builtin.id not in chosen:
            continue
        table = dict(families.get(builtin.id, {}))
        langs = _cv_languages(offer_langs, builtin.languages)
        if langs:
            table["languages"] = langs
        families[builtin.id] = table
    titles = list(answers.get("custom_titles") or [])
    if titles:
        fid = custom_family_id(titles)
        table = dict(old_families.get(fid, {}))
        table.update(label=titles[0], title_terms=titles, search_roles=titles)
        langs = _cv_languages(offer_langs, CV_LANGUAGES)
        if langs:
            table["languages"] = langs
        families[fid] = table
    # An existing file without [families] already uses every built-in: keeping
    # them all declares nothing (the first /setup, with no file yet, still
    # declares the families it picks, their languages narrowed to the offers').
    builtins_as_they_are = (
        bool(base)
        and "families" not in base
        and set(chosen) >= builtin_ids
        and not titles
        and not families.keys() - builtin_ids
    )
    if families and not builtins_as_they_are:
        doc["families"] = families
    else:  # nothing chosen, or every built-in in a file that declares none:
        doc.pop("families", None)  # the built-in families apply (unchanged)


def profile_document(
    base: Mapping[str, Any],
    answers: Mapping[str, Any],
    section: str | None = None,
) -> dict[str, Any]:
    """``base`` (the current ``profile.toml`` document) with the name (only
    the ``contact`` section changes it)."""
    doc: dict[str, Any] = {k: v for k, v in base.items()}
    if not _applies(section, "contact"):
        return doc
    identity = dict(base.get("identity", {}))
    identity["name"] = answers["name"]
    slug = file_slug(str(answers["name"]))
    if slug is not None:
        identity["file_slug"] = slug
    doc["identity"] = identity
    return doc


def _text_answer(answers: Mapping[str, Any], key: str) -> str | None:
    value = answers.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def spoken_languages(answers: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(language, level)`` of every spoken language that has a level: the
    common ones (a code of :data:`SPOKEN_LANGUAGES`) in button order, then
    the typed ones (their name as typed)."""
    levels = answers.get("spoken_levels")
    if not isinstance(levels, Mapping):
        return []
    keys = [
        *(c for c in SPOKEN_LANGUAGES if c in (answers.get("spoken_langs") or [])),
        *(str(name) for name in answers.get("spoken_other") or []),
    ]
    return [
        (key, str(levels[key]))
        for key in dict.fromkeys(keys)
        if levels.get(key) in LANGUAGE_LEVELS
    ]


def background(answers: Mapping[str, Any]) -> dict[str, Any]:
    """The background answers (T8) as stored in ``onboarding_answers.json``
    and turned into the ``setup-*`` claims: ``spoken_languages``,
    ``skills``, ``experience`` (``None`` without a years answer; the company
    only with a role) and ``education`` (a list of entries; an older
    answers file's single line is one entry)."""
    years = answers.get("exp_years")
    role = _text_answer(answers, "last_role")
    experience = (
        {
            "years": years,
            "role": role,
            "company": _text_answer(answers, "last_company") if role else None,
        }
        if years in EXPERIENCE_YEARS
        else None
    )
    return {
        "spoken_languages": [
            {"language": key, "level": level}
            for key, level in spoken_languages(answers)
        ],
        "skills": [str(s) for s in answers.get("skills") or [] if str(s).strip()],
        "experience": experience,
        "education": education_entries(answers.get("education")),
    }


def answers_record(
    answers: Mapping[str, Any], ui_language: Lang, section: str | None = None
) -> dict[str, Any]:
    """What ``onboarding_answers.json`` holds: contact data for the claims
    step, the background answers and every answer as
    given (plus the ``section`` a ``/setup <section>`` confirmed)."""
    record: dict[str, Any] = {
        "schema": ANSWERS_SCHEMA,
        "ui_language": ui_language,
        "contact": {
            key: answers.get(key)
            for key in ("name", "email", "phone", "city", "linkedin")
        },
        "background": background(answers),
        "answers": dict(answers),
    }
    if section is not None:
        record["section"] = section
    return record


@dataclass(frozen=True)
class Written:
    preferences: Path
    profile: Path
    answers: Path


def write_answers(
    profile_dir: Path,
    answers: Mapping[str, Any],
    ui_language: Lang,
    section: str | None = None,
) -> Written:
    """Write ``preferences.toml``, ``profile.toml`` and ``onboarding_answers.json``.

    Both TOML documents are validated before either file is replaced, so an
    answer the strict loaders refuse leaves every file untouched
    (:class:`~cva.profile_identity.ProfileError` propagates). With
    ``section`` (``/setup <section>``) only its keys are applied and a TOML
    file whose document stays the same is left as it is.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = profile_dir / "preferences.toml"
    profile_path = profile_dir / "profile.toml"
    prefs_base = read_document(prefs_path)
    profile_base = read_document(profile_path)
    prefs = preferences_document(prefs_base, answers, section)
    profile = profile_document(profile_base, answers, section)
    # Validate both before writing either (each writer validates its own).
    preferences_from_data(tomllib.loads(toml_document(prefs)))
    identity_from_data(tomllib.loads(toml_document(profile)))
    if section is None or prefs != prefs_base:
        write_preferences(prefs_path, prefs)
    if section is None or profile != profile_base:
        write_profile(profile_path, profile)
    answers_path = profile_dir / ANSWERS_FILE
    record = answers_record(answers, ui_language, section)
    atomic_write(
        answers_path,
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,  # personal contact data
    )
    return Written(prefs_path, profile_path, answers_path)


def persist_ui_language(config_path: Path, lang: Lang) -> None:
    """Set ``ui_language`` in ``config.local.toml`` exactly as ``cli.py init``
    does (its line-preserving writer, then an atomic replace)."""
    base = config_path.read_text("utf-8") if config_path.is_file() else ""
    # A symlinked config (dotfiles) is written through: the link stays.
    atomic_write(
        write_target(config_path), _config_toml_text({"ui_language": lang}, base)
    )
