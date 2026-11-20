"""The current answers of a profile, to prefill ``/setup`` (onboarding-v2 T9, S10).

A re-run (``/setup`` or ``/setup <section>``) starts from what the profile
already says, so a question can simply be kept. Sources, the later one
winning for the keys it holds (hand edits of the files beat the record of
the last Confirm):

=============================  ==============================================
source                         answers
=============================  ==============================================
``onboarding_answers.json``    every answer of the last Confirm (``answers``)
``claims.json``                name, email and LinkedIn from the identity
                               claim (``profile.toml [identity]
                               identity_claim_id``) when the record has none;
                               the background answers from the ``setup-*``
                               claims when the record has none
``profile.toml``               ``[identity] name``
``preferences.toml``           ``[search] level`` / ``work_modes`` /
                               ``min_salary`` / ``currency``, ``[location]
                               city`` / ``country``, ``[gates]
                               allowed_languages``
=============================  ==============================================

The families are offered by :mod:`cva.onboarding.flow` from
``preferences.toml`` (``declared``); a previous own-titles answer is not
prefilled, its family is one of the declared ones. A file that is missing
or unreadable adds nothing (Confirm reports an unreadable one).
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from cva.i18n import t
from cva.onboarding import answers as A
from cva.onboarding.profile_gen import experience_words
from cva.profile_identity import ProfileError
from cva.profile_writer import read_document

_SETUP: Final[str] = "setup-"
# Answers kept in the record that a re-run never reuses (asked again).
_NOT_PREFILLED: Final[tuple[str, ...]] = ("custom_titles", "families", "kept_families")


def _document(path: Path) -> dict[str, Any]:
    try:
        return read_document(path)
    except ProfileError:
        return {}


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _record_answers(profile_dir: Path) -> dict[str, Any]:
    record = _json(profile_dir / A.ANSWERS_FILE)
    answers = record.get("answers") if isinstance(record, dict) else None
    if not isinstance(answers, dict):
        return {}
    return {k: v for k, v in answers.items() if k not in _NOT_PREFILLED}


def _claims(profile_dir: Path) -> list[dict[str, Any]]:
    claims = _json(profile_dir / "claims.json")
    if not isinstance(claims, list):
        return []
    return [c for c in claims if isinstance(c, dict) and isinstance(c.get("text"), str)]


def _identity(answers: dict[str, Any], claims: list[dict[str, Any]], cid: str) -> None:
    """Name, email and LinkedIn from the identity claim (``a · b · c``)."""
    claim = next((c for c in claims if c.get("id") == cid), None)
    if claim is None:
        return
    fields = [f.strip() for f in str(claim["text"]).split("·") if f.strip()]
    if fields and not answers.get("name"):
        answers["name"] = A.clean_text(fields[0])
    if not answers.get("email"):
        emails = [v for f in fields[1:] if (v := A.clean_email(f))]
        if emails:
            answers["email"] = emails[0]
    if not answers.get("linkedin"):
        urls = [
            v
            for f in fields[1:]
            if "linkedin." in f.casefold() and (v := A.clean_url(f))
        ]
        if urls:
            answers["linkedin"] = urls[0]


def _reverse(prefix: str, values: tuple[str, ...], text: str) -> str | None:
    """The value whose EN or ES catalogue label is ``text``."""
    folded = text.strip().casefold()
    for value in values:
        for lang in ("en", "es"):
            if t(f"{prefix}.{value}", lang).casefold() == folded:
                return value
    return None


def _spoken(answers: dict[str, Any], claims: list[dict[str, Any]]) -> None:
    langs: list[str] = []
    other: list[str] = []
    levels: dict[str, str] = {}
    for claim in claims:
        cid = str(claim.get("id", ""))
        if not cid.startswith(f"{_SETUP}lang-"):
            continue
        name, _, label = str(claim["text"]).rpartition(" — ")
        level = _reverse("onboarding.cefr", A.LANGUAGE_LEVELS, label)
        code = cid.removeprefix(f"{_SETUP}lang-")
        if level is None or not name:
            continue
        if code in A.SPOKEN_LANGUAGES:
            langs.append(code)
            levels[code] = level
        else:
            other.append(name)
            levels[name] = level
    if langs or other:
        answers["spoken_langs"] = [c for c in A.SPOKEN_LANGUAGES if c in langs]
        answers["spoken_other"] = other
        answers["spoken_levels"] = levels


def _experience(answers: dict[str, Any], text: str) -> None:
    years_text, _, role_text = text.partition(" · ")
    for lang in ("en", "es"):
        words = experience_words(lang)
        years = next((y for y in A.EXPERIENCE_YEARS if words[y] == years_text), None)
        if years is None:
            continue
        answers["exp_years"] = years
        role_at = re.escape(words["role_at"])
        pattern = role_at.replace(r"\$role", "(?P<role>.+)").replace(
            r"\$company", "(?P<company>.+)"
        )
        role_only = re.escape(words["role"]).replace(r"\$role", "(?P<role>.+)")
        match = re.fullmatch(pattern, role_text) or re.fullmatch(role_only, role_text)
        if match is not None:
            answers["last_role"] = match.group("role")
            answers["last_company"] = match.groupdict().get("company")
        return


def _background(answers: dict[str, Any], claims: list[dict[str, Any]]) -> None:
    """The background answers from the ``setup-*`` claims (a record without
    them: written by hand, or lost)."""
    setup = [c for c in claims if str(c.get("id", "")).startswith(_SETUP)]
    if not setup:
        return
    _spoken(answers, setup)
    skills = [
        str(c["text"]) for c in setup if str(c["id"]).startswith(f"{_SETUP}skill-")
    ]
    if skills:
        answers["skills"] = skills
    for claim in setup:
        if claim["id"] == f"{_SETUP}experience":
            _experience(answers, str(claim["text"]))
        elif claim["id"] == f"{_SETUP}education":
            answers["education"] = str(claim["text"])


def _from_preferences(answers: dict[str, Any], doc: Mapping[str, Any]) -> None:
    search = doc.get("search")
    if isinstance(search, Mapping):
        if search.get("level") in A.LEVELS:
            answers["level"] = search["level"]
        modes = search.get("work_modes")
        if isinstance(modes, list):
            answers["work_modes"] = [m for m in A.WORK_MODES if m in modes]
        if isinstance(search.get("min_salary"), int):
            answers["min_salary"] = search["min_salary"]
            currency = search.get("currency")
            answers["currency"] = currency if isinstance(currency, str) else "EUR"
        elif "min_salary" not in search:
            answers["min_salary"] = None
            answers["currency"] = None
    location = doc.get("location")
    if isinstance(location, Mapping):
        for key, answer in (("city", "work_city"), ("country", "country")):
            if isinstance(location.get(key), str) and location[key]:
                answers[answer] = location[key]
    gates = doc.get("gates")
    if isinstance(gates, Mapping) and isinstance(gates.get("allowed_languages"), list):
        allowed = gates["allowed_languages"]
        answers["offer_langs"] = [c for c in A.OFFER_LANGUAGES if c in allowed]


def current_answers(profile_dir: Path) -> dict[str, Any]:
    """What the profile at ``profile_dir`` already answers (module docstring)."""
    answers = _record_answers(profile_dir)
    profile = _document(profile_dir / "profile.toml")
    identity = profile.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    claims = _claims(profile_dir)
    if isinstance(identity.get("name"), str) and identity["name"]:
        answers["name"] = identity["name"]
    cid = identity.get("identity_claim_id")
    _identity(answers, claims, cid if isinstance(cid, str) else "identity-contact")
    if "skills" not in answers:
        _background(answers, claims)
    _from_preferences(answers, _document(profile_dir / "preferences.toml"))
    return {k: v for k, v in answers.items() if v is not None or k in _NULLABLE}


# Answers whose ``None`` means "skipped" (kept so the step shows it).
_NULLABLE: Final[tuple[str, ...]] = ("min_salary", "currency")
