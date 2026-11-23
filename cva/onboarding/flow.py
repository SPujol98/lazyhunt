"""The ``/setup`` conversation in Telegram (onboarding-v2 T4, S3/S7).

A re-run offers the families ``preferences.toml`` already declares,
preselected (built-in ones as their preset button, the others labelled by
their ``label``); a declared family the user leaves selected keeps every key.

One question per message, buttons wherever the answer is a choice, Back and
Cancel on every step (Back from the second one on), then a summary with
Confirm / Edit. Nothing is written before Confirm.

State lives in ``context.user_data`` like the STAR flow (``bot._ask_star``):
:data:`STATE_KEY` holds a JSON-able dict (current step, history for Back, the
answers) and :data:`STATE_AT_KEY` the time of the last interaction; a
conversation idle for :data:`TTL_SECONDS` is dropped, so a forgotten setup
never captures later text. While the current step expects text, the bot hands
plain messages here BEFORE the paste / STAR / outcome-note capture
(:func:`on_text` returns ``True`` when it took the message).

Callback data: ``ob:<step id>:<action>[:<argument>]`` (``ob`` is registered in
``bot.CALLBACK_HANDLERS``). A button of an earlier question is answered with
the current question instead of being applied.

After the level, T8 (S9) asks the background that becomes the ``setup-*``
claims (:mod:`cva.onboarding.profile_gen`): spoken languages (common ones or
typed, side step ``spoken_other``) and a level for each, main skills, years
of experience, last role and company (optional) and education (optional).

Step 9 (T5, S5) asks for the base CV PDFs, one per family and CV language
(:mod:`cva.onboarding.cv_upload`); the PDF itself arrives as a Telegram
document through :func:`on_document`. Uploads wait in a pending area of the
profile directory until Confirm; Cancel, expiry and a new ``/setup`` drop them.
Per slot it offers "Upload my PDF", "Create one with lazyhunt" (a starter
CV from the answers in one of three templates, :mod:`cva.onboarding.starter`,
kept in the same pending area like an upload) or "Not now"; "Skip all" warns
first, "Create another version" adds a position type or language, and a
profile without ``cover_letter_base.toml`` can get one on Confirm.
:data:`CONFIRM_REPLY_EXTRAS` adds lines/buttons to the Confirm reply.

Extension points (later units): :data:`STEP_HOOKS` are extra :class:`Step`
objects asked after the base CVs and before the summary; :data:`CONFIRM_HOOKS`
run after the files are written (T7: minimal profile from the answers).

``/setup <section>`` (T9, S10) re-runs one section (:data:`SECTION_STEPS`;
names and aliases in English and Spanish, :func:`find_section`) on a profile
that is already set up: only its questions, then a summary of that section
and Confirm, written through the same path with ``section`` set
(:func:`cva.onboarding.answers.write_answers`: the keys of other sections and
any file the section does not change keep their bytes; the ``setup-*`` claims
are replaced only by the ``background`` section). Every re-run, ``/setup``
alone included, starts prefilled with the current values
(:func:`cva.onboarding.prefill.current_answers`): text steps offer "Keep",
choices show the current one ticked.

Confirm is all-or-nothing: when writing the answers, the base CVs or a hook
fails, every file it wrote is put back as it was (:mod:`cva.onboarding.snapshot`)
and the pending uploads stay, so Confirm can be pressed again once the cause
is fixed. Step 9 shows the base CVs the profile already has (verified against
its manifest) as done, and only slots with no PDF at all are reported as
not deliverable.
"""

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

from cva.cvgen.base_pdfs import variant_key
from cva.cvgen.master import CvgenError
from cva.cvgen.starter import TEMPLATES, Template
from cva.families import BUILTIN_REGISTRY, CV_LANGUAGES
from cva.i18n import Lang, join_list, t
from cva.match.locations import country_of_city
from cva.match.salary_reference import (
    ReferenceRange,
    city_label,
    eur_text,
    load_reference_table,
    profile_city,
    profile_reference,
)
from cva.onboarding import answers as A
from cva.onboarding import cv_upload as U
from cva.onboarding import starter
from cva.onboarding.commit import Committed, ConfirmFailed, commit_all
from cva.onboarding.portals import (
    enabled_portals_line,
    portal_ids,
    portal_label,
    read_enabled_portals,
    terms_warning,
)
from cva.onboarding.prefill import current_answers
from cva.onboarding.profile_gen import confirm_hook
from cva.profile_identity import ProfileError
from cva.profile_paths import resolve_profile_paths
from cva.setup_mode import describe_load_error, missing_profile_files

PREFIX: Final[str] = "ob"
STATE_KEY: Final[str] = "onboarding"
STATE_AT_KEY: Final[str] = "onboarding_at"
TTL_SECONDS: Final[int] = 30 * 60  # same expiry as the STAR flow
_LANGUAGE_NAMES: Final[dict[str, str]] = {"en": "English", "es": "Español"}
_ON, _OFF = "✅", "▫️"

State = dict[str, Any]


@dataclass(frozen=True)
class Screen:
    """A question: its text and its choice buttons as ``(label, action)``."""

    text: str
    rows: list[list[tuple[str, str]]] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    """What an answer does: stay (re-ask, with ``error``), advance, go to a
    step (``goto``) or return to the previous one (``back``)."""

    advance: bool = False
    goto: str | None = None
    back: bool = False
    error: str | None = None  # i18n key shown above the question


@dataclass(frozen=True)
class Step:
    id: str
    render: Callable[[State, Lang], Screen]
    on_action: Callable[[State, str, str], Outcome] | None = None
    on_text: Callable[[State, str], Outcome] | None = None
    applies: Callable[[State], bool] = lambda state: True
    # Asked only right after this step when editing (currency after salary).
    follows: str | None = None


@dataclass(frozen=True)
class Env:
    """What the conversation needs from the running bot."""

    lang: Lang  # the bot's current UI language
    profile_dir: Path | None
    service: Any = None  # JobService (reload_preferences) in the full app
    config_path: Path | None = None  # config.local.toml (ui_language, portals)
    set_lang: Callable[[Lang], None] | None = None  # switch the running bot
    # Serve the new enabled_portals in the running bot (bridge, setup settings).
    set_portals: Callable[[list[str]], None] | None = None


# Extension points (see the module docstring).
STEP_HOOKS: list[Step] = []
# T7: the minimal working profile (claims, synonyms, judge files) is built
# from the confirmed answers; existing files are never overwritten.
CONFIRM_HOOKS: list[Callable[[Path, dict[str, Any]], None]] = [confirm_hook]


# --- step helpers -------------------------------------------------------------------


def _q(step: str, lang: Lang, **fmt: object) -> str:
    return t(f"onboarding.q.{step}", lang, **fmt)


def _answers(state: State) -> dict[str, Any]:
    answers: dict[str, Any] = state["a"]
    return answers


def _mark(selected: bool, label: str) -> str:
    return f"{_ON if selected else _OFF} {label}"


def _toggle(values: list[str], value: str, order: tuple[str, ...]) -> list[str]:
    chosen = set(values) ^ {value}
    return [v for v in order if v in chosen]


def _shown(value: Any) -> str:
    """A current answer as the "Keep" button shows it."""
    return ", ".join(map(str, value)) if isinstance(value, list) else str(value)


def _keep_row(state: State, sid: str, lang: Lang) -> list[tuple[str, str]]:
    """The "Keep <current value>" button of a re-run (none without a value)."""
    value = _answers(state).get(sid)
    if value is None or value == "" or value == []:
        return []
    return [(t("onboarding.btn.keep", lang, value=_shown(value)), "keep")]


def _keep(state: State, sid: str) -> Outcome:
    value = _answers(state).get(sid)
    return Outcome() if value is None or value in ("", []) else Outcome(advance=True)


def _text_step(
    sid: str,
    clean: Callable[[str], Any],
    error: str,
    *,
    skippable: bool = False,
) -> Step:
    def render(state: State, lang: Lang) -> Screen:
        rows = [_keep_row(state, sid, lang)]
        if skippable:
            rows.append([(t("onboarding.btn.skip", lang), "skip")])
        return Screen(_q(sid, lang), rows)

    def on_text(state: State, text: str) -> Outcome:
        value = clean(text)
        if value is None:
            return Outcome(error=error)
        _answers(state)[sid] = value
        return Outcome(advance=True)

    def on_action(state: State, action: str, arg: str) -> Outcome:
        if action == "keep":
            return _keep(state, sid)
        if action == "skip" and skippable:
            _answers(state)[sid] = None
            return Outcome(advance=True)
        return Outcome()

    return Step(sid, render, on_action, on_text)


def _choice_step(sid: str, values: tuple[str, ...], label_key: str) -> Step:
    def render(state: State, lang: Lang) -> Screen:
        current = _answers(state).get(sid)
        row = [
            (_mark(True, label) if v == current else label, f"go:{v}")
            for v in values
            for label in [t(f"{label_key}.{v}", lang)]
        ]
        return Screen(_q(sid, lang), [row])

    def on_action(state: State, action: str, arg: str) -> Outcome:
        if action == "go" and arg in values:
            _answers(state)[sid] = arg
            return Outcome(advance=True)
        return Outcome()

    return Step(sid, render, on_action)


def _multi_step(
    sid: str, values: tuple[str, ...], label_key: str, *, per_row: int = 3
) -> Step:
    def render(state: State, lang: Lang) -> Screen:
        chosen = _answers(state).get(sid) or []
        buttons = [
            (_mark(v in chosen, t(f"{label_key}.{v}", lang)), f"t:{v}") for v in values
        ]
        rows = [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]
        return Screen(
            _q(sid, lang), [*rows, [(t("onboarding.btn.done", lang), "done")]]
        )

    def on_action(state: State, action: str, arg: str) -> Outcome:
        answers = _answers(state)
        if action == "t" and arg in values:
            answers[sid] = _toggle(answers.get(sid) or [], arg, values)
            return Outcome()
        if action == "done":
            return (
                Outcome(advance=True)
                if answers.get(sid)
                else Outcome(error="onboarding.err.pick_one")
            )
        return Outcome()

    return Step(sid, render, on_action)


# --- the steps ------------------------------------------------------------------------


def _lang_render(state: State, lang: Lang) -> Screen:
    return Screen(
        _q("lang", lang),
        [[(name, f"go:{code}") for code, name in _LANGUAGE_NAMES.items()]],
    )


def _lang_action(state: State, action: str, arg: str) -> Outcome:
    if action != "go" or arg not in _LANGUAGE_NAMES:
        return Outcome()
    state["lang"] = arg
    answers = _answers(state)
    if not answers.get("offer_langs"):
        answers["offer_langs"] = [arg]
    return Outcome(advance=True)


_BUILTIN_IDS: Final[tuple[str, ...]] = BUILTIN_REGISTRY.ids


def _declared(state: State) -> list[tuple[str, str]]:
    """``(id, label)`` of the families the profile declared that are not
    built-in presets (offered preselected; see :func:`start`)."""
    return [(str(fid), str(label)) for fid, label in state.get("declared") or []]


def _families_render(state: State, lang: Lang) -> Screen:
    answers = _answers(state)
    chosen = answers.get("families") or []
    kept = answers.get("kept_families") or []
    rows = [
        [
            (_mark(f.id in chosen, f.label), f"t:{f.id}")
            for f in BUILTIN_REGISTRY.families
        ]
    ]
    rows.extend(
        [(_mark(fid in kept, label), f"k:{fid}")] for fid, label in _declared(state)
    )
    titles = answers.get("custom_titles") or []
    other = (
        _mark(True, t("onboarding.custom", lang, titles=", ".join(titles)))
        if titles
        else t("onboarding.btn.other", lang)
    )
    rows.append([(other, "other")])
    rows.append([(t("onboarding.btn.done", lang), "done")])
    return Screen(_q("families", lang), rows)


def _families_action(state: State, action: str, arg: str) -> Outcome:
    answers = _answers(state)
    if action == "t" and arg in _BUILTIN_IDS:
        answers["families"] = _toggle(answers.get("families") or [], arg, _BUILTIN_IDS)
        return Outcome()
    declared = tuple(fid for fid, _ in _declared(state))
    if action == "k" and arg in declared:
        kept = answers.get("kept_families") or []
        answers["kept_families"] = _toggle(kept, arg, declared)
        return Outcome()
    if action == "other":
        return Outcome(goto="custom_titles")
    if action == "done":
        if (
            answers.get("families")
            or answers.get("kept_families")
            or answers.get("custom_titles")
        ):
            return Outcome(advance=True)
        return Outcome(error="onboarding.err.pick_one")
    return Outcome()


def _custom_render(state: State, lang: Lang) -> Screen:
    return Screen(
        _q("custom_titles", lang), [[(t("onboarding.btn.skip", lang), "skip")]]
    )


def _custom_text(state: State, text: str) -> Outcome:
    titles = A.clean_titles(text)
    if titles is None:
        return Outcome(error="onboarding.err.titles")
    _answers(state)["custom_titles"] = titles
    return Outcome(back=True)


def _custom_action(state: State, action: str, arg: str) -> Outcome:
    if action == "skip":  # no own titles (clears earlier ones)
        _answers(state)["custom_titles"] = []
        return Outcome(back=True)
    return Outcome()


def _work_city_value(state: State) -> str | None:
    """What "Keep" keeps: the work city already answered, else the home city."""
    answers = _answers(state)
    value = answers.get("work_city") or answers.get("city")
    return str(value) if value else None


def _work_city_render(state: State, lang: Lang) -> Screen:
    city = _work_city_value(state)
    if not city:
        return Screen(_q("work_city_empty", lang))
    return Screen(
        _q("work_city", lang), [[(t("onboarding.btn.keep", lang, value=city), "keep")]]
    )


def _work_city_action(state: State, action: str, arg: str) -> Outcome:
    city = _work_city_value(state)
    if action == "keep" and city:
        _answers(state)["work_city"] = city
        return Outcome(advance=True)
    return Outcome()


def _country_default(state: State, lang: Lang) -> tuple[str, str] | None:
    """``(country, city)`` proposed by the country step: the country of the
    work city (else the home city) from the shipped place tables; ``None``
    for an unknown city or when a country is already answered (a re-run
    keeps it)."""
    if _answers(state).get("country"):
        return None
    city = _work_city_value(state)
    country = country_of_city(city)
    if country is None or city is None:
        return None
    return t(f"onboarding.country.{country}", lang), city


def _country_render(state: State, lang: Lang) -> Screen:
    if state.get("country_other"):
        return Screen(_q("country_other", lang))
    proposed = _country_default(state, lang)
    if proposed is None:
        return Screen(_q("country", lang), [_keep_row(state, "country", lang)])
    country, city = proposed
    state["country_default"] = country
    label = t("onboarding.btn.country_from", lang, country=country, city=city)
    return Screen(
        _q("country", lang),
        [[(label, "default")], [(t("onboarding.btn.country_other", lang), "other")]],
    )


def _country_action(state: State, action: str, arg: str) -> Outcome:
    if action == "keep":
        return _keep(state, "country")
    if action == "default" and state.get("country_default"):
        _answers(state)["country"] = state.pop("country_default")
        return Outcome(advance=True)
    if action == "other":
        state["country_other"] = True
    return Outcome()


def _country_text(state: State, text: str) -> Outcome:
    country = A.clean_text(text)
    if country is None:
        return Outcome(error="onboarding.err.text")
    _answers(state)["country"] = country
    state.pop("country_other", None)
    state.pop("country_default", None)
    return Outcome(advance=True)


def _salary_text(state: State, text: str) -> Outcome:
    amount = A.clean_salary(text)
    if amount is None:
        return Outcome(error="onboarding.err.salary")
    _answers(state)["min_salary"] = amount
    return Outcome(advance=True)


def _salary_action(state: State, action: str, arg: str) -> Outcome:
    if action == "keep":
        return _keep(state, "min_salary")
    if action == "use":  # the reference's low end, recomputed (never stale)
        hint = _salary_hint(state)
        if hint is None:
            return Outcome()
        _answers(state)["min_salary"] = hint[0].low
        return Outcome(advance=True)
    if action == "skip":
        answers = _answers(state)
        answers["min_salary"] = None
        answers["currency"] = None
        return Outcome(advance=True)
    return Outcome()


def _salary_hint(state: State) -> tuple[ReferenceRange, str] | None:
    """The profile's ``salary_reference.toml`` range for the chosen
    positions, level and work city, with the label of its position; ``None``
    when the table has none (or is missing or unreadable): never invented."""
    folder = state.get("dir")
    if not folder:
        return None
    answers = _answers(state)
    labels: dict[str, str] = {}
    for family in BUILTIN_REGISTRY.families:
        if family.id in (answers.get("families") or []):
            labels.setdefault(family.role, family.label)
    kept = answers.get("kept_families") or []
    for fid, label in _declared(state):
        if fid in kept:
            labels.setdefault(fid.upper(), label)
    titles = answers.get("custom_titles") or []
    if titles:
        labels.setdefault(A.custom_family_id(titles).upper(), titles[0])
    try:
        table = load_reference_table(resolve_profile_paths(Path(folder)).salary_table)
    except (OSError, ValueError):  # unreadable or invalid table: no hint
        return None
    city = profile_city(
        answers.get("work_city"),
        answers.get("country"),
        answers.get("work_modes") or [],
    )
    row = profile_reference(table, list(labels), str(answers.get("level") or ""), city)
    return None if row is None else (row, labels[row.branch])


def _amount(value: int, lang: Lang) -> str:
    return eur_text(value) if lang == "es" else f"{value:,}"


def _salary_render(state: State, lang: Lang) -> Screen:
    rows = [_keep_row(state, "min_salary", lang)]
    hint = _salary_hint(state)
    if hint is None:
        note = t("onboarding.salary.no_reference", lang)
    else:
        row, role = hint
        note = t(
            "onboarding.salary.reference",
            lang,
            role=role,
            level=t(f"onboarding.level.{row.level}", lang),
            city=city_label(row.city, lang),
            low=_amount(row.low, lang),
            high=_amount(row.high, lang),
            source=row.sources[0] if row.sources else "—",
        )
        rows.append(
            [
                (
                    t("onboarding.btn.use_salary", lang, amount=_amount(row.low, lang)),
                    "use",
                )
            ]
        )
    rows.append([(t("onboarding.btn.skip", lang), "skip")])
    return Screen(f"{_q('salary', lang)}\n\n{note}", rows)


def _currency_current(state: State) -> str:
    """The ticked currency: the answer, else the country's default."""
    answers = _answers(state)
    current = answers.get("currency")
    return str(current) if current else A.default_currency(answers.get("country"))


def _currency_render(state: State, lang: Lang) -> Screen:
    if state.get("currency_other"):
        return Screen(
            _q("currency_other", lang), [[(t("onboarding.btn.list", lang), "list")]]
        )
    current = _currency_current(state)
    rows = [[(_mark(True, c) if c == current else c, f"go:{c}") for c in A.CURRENCIES]]
    other = (
        _mark(True, current)
        if current not in A.CURRENCIES
        else t("onboarding.btn.other_currency", lang)
    )
    rows.append([(other, "other")])
    return Screen(_q("currency", lang), rows)


def _currency_action(state: State, action: str, arg: str) -> Outcome:
    if action == "go" and arg in A.CURRENCIES:
        _answers(state)["currency"] = arg
        state.pop("currency_other", None)
        return Outcome(advance=True)
    if action == "other":
        state["currency_other"] = True
        return Outcome()
    if action == "list":
        state.pop("currency_other", None)
        return Outcome()
    return Outcome()


def _currency_text(state: State, text: str) -> Outcome:
    code = A.clean_currency(text)
    if code is None:
        return Outcome(error="onboarding.err.currency")
    _answers(state)["currency"] = code
    state.pop("currency_other", None)
    return Outcome(advance=True)


def _portals_render(state: State, lang: Lang) -> Screen:
    pending = state.get("portal")
    if pending:
        return Screen(
            _q("portal_terms", lang, warning=terms_warning(pending, lang)),
            [
                [
                    (
                        t("onboarding.btn.enable", lang, portal=portal_label(pending)),
                        "yes",
                    )
                ],
                [(t("onboarding.btn.list", lang), "no")],
            ],
        )
    chosen = _answers(state).get("portals") or []
    buttons = [(_mark(p in chosen, portal_label(p)), f"t:{p}") for p in portal_ids()]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return Screen(
        _q("portals", lang), [*rows, [(t("onboarding.btn.done", lang), "done")]]
    )


def _portals_action(state: State, action: str, arg: str) -> Outcome:
    answers = _answers(state)
    chosen: list[str] = answers.get("portals") or []
    if action == "t" and arg in portal_ids():
        if arg in chosen:
            answers["portals"] = [p for p in chosen if p != arg]
        else:
            state["portal"] = arg  # show its terms warning first
        return Outcome()
    if action == "yes" and state.get("portal"):
        answers["portals"] = _toggle(chosen, str(state.pop("portal")), portal_ids())
        return Outcome()
    if action == "no":
        state.pop("portal", None)
        return Outcome()
    if action == "done":
        answers.setdefault("portals", [])
        return Outcome(advance=True)
    return Outcome()


# --- background (T8, S9): spoken languages, skills, experience, education -------------


def _language_name(key: str, lang: Lang, *, in_sentence: bool = False) -> str:
    """A common language's catalogue name; a typed one as typed. Spanish
    writes language names in lowercase mid-sentence (``in_sentence``)."""
    if key not in A.SPOKEN_LANGUAGES:
        return key
    name = t(f"onboarding.olang.{key}", lang)
    return name.lower() if in_sentence and lang == "es" else name


def _level_label(level: str, lang: Lang) -> str:
    return t(f"onboarding.cefr.{level}", lang)


def _spoken_keys(state: State) -> list[str]:
    answers = _answers(state)
    return [*(answers.get("spoken_langs") or []), *(answers.get("spoken_other") or [])]


def _spoken_render(state: State, lang: Lang) -> Screen:
    answers = _answers(state)
    chosen = answers.get("spoken_langs") or []
    buttons = [
        (_mark(code in chosen, t(f"onboarding.olang.{code}", lang)), f"t:{code}")
        for code in A.SPOKEN_LANGUAGES
    ]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    typed = answers.get("spoken_other") or []
    other = (
        _mark(True, t("onboarding.custom", lang, titles=", ".join(typed)))
        if typed
        else t("onboarding.btn.other_lang", lang)
    )
    rows.extend([[(other, "other")], [(t("onboarding.btn.done", lang), "done")]])
    return Screen(_q("spoken_langs", lang), rows)


def _spoken_action(state: State, action: str, arg: str) -> Outcome:
    answers = _answers(state)
    if action == "t" and arg in A.SPOKEN_LANGUAGES:
        chosen = answers.get("spoken_langs") or []
        answers["spoken_langs"] = _toggle(chosen, arg, A.SPOKEN_LANGUAGES)
        return Outcome()
    if action == "other":
        return Outcome(goto="spoken_other")
    if action == "done":
        if not _spoken_keys(state):
            return Outcome(error="onboarding.err.pick_one")
        # Every level is asked (again); the previous one is shown ticked.
        state["levels_before"] = answers.get("spoken_levels") or {}
        answers["spoken_levels"] = {}
        return Outcome(advance=True)
    return Outcome()


def _spoken_other_render(state: State, lang: Lang) -> Screen:
    return Screen(
        _q("spoken_other", lang), [[(t("onboarding.btn.skip", lang), "skip")]]
    )


def _spoken_other_text(state: State, text: str) -> Outcome:
    names = A.clean_languages(text)
    if names is None:
        return Outcome(error="onboarding.err.languages")
    answers = _answers(state)
    common = [code for name in names if (code := A.common_language(name))]
    chosen = [*(answers.get("spoken_langs") or []), *common]
    answers["spoken_langs"] = [c for c in A.SPOKEN_LANGUAGES if c in chosen]
    answers["spoken_other"] = [n for n in names if A.common_language(n) is None]
    return Outcome(back=True)


def _spoken_other_action(state: State, action: str, arg: str) -> Outcome:
    if action == "skip":  # no typed languages (clears earlier ones)
        _answers(state)["spoken_other"] = []
        return Outcome(back=True)
    return Outcome()


def _level_target(state: State) -> str | None:
    """The next spoken language without a level (``None``: all have one)."""
    levels = _answers(state).get("spoken_levels") or {}
    return next((key for key in _spoken_keys(state) if key not in levels), None)


def _levels_render(state: State, lang: Lang) -> Screen:
    target = _level_target(state) or ""
    before = (state.get("levels_before") or {}).get(target)
    buttons = [
        (
            _mark(True, _level_label(v, lang))
            if v == before
            else _level_label(v, lang),
            f"go:{v}",
        )
        for v in A.LANGUAGE_LEVELS
    ]
    return Screen(
        _q(
            "spoken_levels",
            lang,
            language=_language_name(target, lang, in_sentence=True),
        ),
        [buttons[:3], buttons[3:]],
    )


def _levels_action(state: State, action: str, arg: str) -> Outcome:
    target = _level_target(state)
    if action != "go" or arg not in A.LANGUAGE_LEVELS or target is None:
        return Outcome()
    answers = _answers(state)
    answers["spoken_levels"] = {**(answers.get("spoken_levels") or {}), target: arg}
    return Outcome(advance=_level_target(state) is None)


def _role_action(state: State, action: str, arg: str) -> Outcome:
    if action == "keep":
        return _keep(state, "last_role")
    if action == "skip":  # no role: the company is not asked either
        answers = _answers(state)
        answers["last_role"] = None
        answers["last_company"] = None
        return Outcome(advance=True)
    return Outcome()


_ROLE_STEP: Final[Step] = _text_step(
    "last_role", A.clean_text, "onboarding.err.text", skippable=True
)

_COMPANY_STEP: Final[Step] = _text_step(
    "last_company", A.clean_text, "onboarding.err.text", skippable=True
)


# --- step 9: base CVs (T5; beta3-ux T4: upload, create from a template, later) -----

CV_STEP: Final[str] = "cv_pdfs"


def _cv_slots(state: State) -> list[U.Slot]:
    """The base CVs the answers so far call for (see :func:`cv_upload.slots`)."""
    folder = state.get("dir")
    return (
        U.slots(Path(folder), _answers(state), state.get("section")) if folder else []
    )


def _cv(state: State) -> dict[str, Any]:
    """CV step progress: ``got`` (key -> sha256 of the pending PDF), ``skip``
    (keys), the ``target`` slot the next PDF is for (``None``: the first open
    one), ``created`` (key -> template of a PDF lazyhunt made), the ``view``
    shown (``None``: the slot list; ``templates``, ``skip_warn``, ``more``),
    ``letter`` (create a base cover letter on Confirm) and a one-shot
    ``notice`` (i18n key and its values)."""
    cv: dict[str, Any] = state.setdefault("cv", {"got": {}, "skip": [], "target": None})
    cv.setdefault("created", {})
    return cv


def _cv_existing(state: State, slots: list[U.Slot]) -> set[str]:
    """Slots the profile already has a verified base PDF for (a re-run)."""
    folder = state.get("dir")
    return U.existing(Path(folder), slots) if folder else set()


def _cv_target(state: State, slots: list[U.Slot], existing: set[str]) -> U.Slot | None:
    cv = _cv(state)
    by_key = {slot.key: slot for slot in slots}
    if cv.get("target") in by_key:
        return by_key[cv["target"]]
    done = {*cv["got"], *cv["skip"], *existing}
    return next((s for s in slots if s.key not in done), None)


def _slot_name(slot: U.Slot) -> str:
    return f"{slot.label} · {slot.lang.upper()}"


def _template_name(template: str, lang: Lang) -> str:
    return t(f"onboarding.cv.tpl.{template}", lang)


def _letter_offered(state: State) -> bool:
    """A base cover letter can be created: the profile has none yet."""
    folder = state.get("dir")
    return isinstance(folder, str) and not starter.has_cover_letter(Path(folder))


def _more_options(state: State, slots: list[U.Slot]) -> list[tuple[str, str, str]]:
    """``(family id, CV language, label)`` of the base CVs "Create another
    version" can add (full questionnaire only: a section keeps the
    positions and offer languages as they are)."""
    if state.get("section") is not None:
        return []
    have = {slot.key for slot in slots}
    answers = _answers(state)
    options: list[tuple[str, str, str]] = []
    for family in BUILTIN_REGISTRY.families:
        for lang in family.languages:
            if variant_key(family.role, lang) not in have:
                options.append((family.id, lang, family.label))
    kept = answers.get("kept_families") or []
    others = [(fid, label) for fid, label in _declared(state) if fid in kept]
    titles = answers.get("custom_titles") or []
    if titles:
        others.append((A.custom_family_id(titles), str(titles[0])))
    for fid, label in others:
        for lang in CV_LANGUAGES:
            if variant_key(fid.upper(), lang) not in have:
                options.append((fid, lang, label))
    return options


def _cv_notice(state: State, lang: Lang) -> str | None:
    notice = _cv(state).pop("notice", None)
    if not isinstance(notice, dict):
        return None
    return t(str(notice["key"]), lang, **dict(notice.get("fmt") or {}))


def _cv_render(state: State, lang: Lang) -> Screen:
    view = _cv(state).get("view")
    notice = _cv_notice(state, lang)
    if view == "templates":
        screen = _cv_templates_render(state, lang)
    elif view == "skip_warn":
        screen = _cv_skip_warn_render(state, lang)
    elif view == "more":
        screen = _cv_more_render(state, lang)
    else:
        screen = _cv_list_render(state, lang)
    return Screen(f"{notice}\n\n{screen.text}", screen.rows) if notice else screen


def _cv_list_render(state: State, lang: Lang) -> Screen:
    slots = _cv_slots(state)
    cv = _cv(state)
    existing = _cv_existing(state, slots)
    target = _cv_target(state, slots, existing)
    kept = t("onboarding.cv.existing", lang)
    lines = []
    for slot in slots:
        note = ""
        if slot.key in cv["got"]:
            mark = "✅"
            if slot.key in cv["created"]:
                tpl = _template_name(str(cv["created"][slot.key]), lang)
                note = " " + t("onboarding.cv.created_mark", lang, template=tpl)
        elif slot.key in existing:  # can still be replaced (tap its line)
            mark, note = "✅", f" {kept}"
        elif slot.key in cv["skip"]:
            mark = "⏭"
        else:
            mark = "⏳"
        pointer = " 👈" if target is not None and slot.key == target.key else ""
        lines.append(f"{mark} {_slot_name(slot)}{note}{pointer}")
    ask = (
        t("onboarding.cv.send", lang, slot=_slot_name(target))
        if target is not None
        else t("onboarding.cv.all_set", lang)
    )
    text = _q(CV_STEP, lang, mb=U.MAX_BYTES // (1024 * 1024), pages=U.MAX_PAGES)
    rows: list[list[tuple[str, str]]] = []
    if target is not None:
        rows.extend(
            [
                [(t("onboarding.btn.cv_upload", lang), "upload")],
                [(t("onboarding.btn.cv_create", lang), "create")],
                [(t("onboarding.btn.cv_later", lang), "skip")],
            ]
        )
    buttons = [(f"📄 {_slot_name(s)}", f"pick:{s.key}") for s in slots]
    rows.extend(buttons[i : i + 2] for i in range(0, len(buttons), 2))
    if _more_options(state, slots):
        rows.append([(t("onboarding.btn.cv_more", lang), "more")])
    if _letter_offered(state):
        label = (
            _mark(True, t("onboarding.btn.letter_on", lang))
            if cv.get("letter")
            else t("onboarding.btn.letter", lang)
        )
        rows.append([(label, "letter")])
    rows.append(
        [
            (t("onboarding.btn.skip_all", lang), "skip_all"),
            (t("onboarding.btn.done", lang), "done"),
        ]
    )
    return Screen(f"{text}\n\n" + "\n".join(lines) + f"\n\n{ask}", rows)


def _cv_templates_render(state: State, lang: Lang) -> Screen:
    slots = _cv_slots(state)
    target = _cv_target(state, slots, _cv_existing(state, slots))
    slot = _slot_name(target) if target is not None else ""
    lines = [
        f"• {_template_name(tpl, lang)}: {t(f'onboarding.cv.tpl_about.{tpl}', lang)}"
        for tpl in TEMPLATES
    ]
    text = t("onboarding.cv.templates", lang, slot=slot, templates="\n".join(lines))
    return Screen(
        text,
        [
            [(_template_name(tpl, lang), f"tpl:{tpl}") for tpl in TEMPLATES],
            [(t("onboarding.btn.list", lang), "list")],
        ],
    )


def _cv_skip_warn_render(state: State, lang: Lang) -> Screen:
    missing = ", ".join(_slot_name(s) for s in _cv_missing(state, _cv_slots(state)))
    return Screen(
        t("onboarding.cv.skip_warning", lang, slots=missing),
        [
            [(t("onboarding.btn.cv_create_now", lang), "create")],
            [(t("onboarding.btn.cv_skip_anyway", lang), "skip_all_yes")],
            [(t("onboarding.btn.list", lang), "list")],
        ],
    )


def _cv_more_render(state: State, lang: Lang) -> Screen:
    options = _more_options(state, _cv_slots(state))
    buttons = [
        (f"➕ {label} · {code.upper()}", f"add:{fid}:{code}")
        for fid, code, label in options
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([(t("onboarding.btn.list", lang), "list")])
    return Screen(t("onboarding.cv.more", lang), rows)


def _cv_create(state: State, slot: U.Slot, template: str) -> None:
    """Render the starter CV of ``slot`` as its pending upload (a notice
    says what happened)."""
    cv = _cv(state)
    folder = Path(str(state["dir"]))
    try:
        sha, pages = starter.create_pending_cv(
            folder, _answers(state), slot, cast(Template, template)
        )
    except (U.UploadError, CvgenError, OSError):
        cv["notice"] = {"key": "onboarding.err.cv_create", "fmt": {}}
        return
    cv["got"][slot.key] = sha
    cv["created"][slot.key] = template
    cv["skip"] = [k for k in cv["skip"] if k != slot.key]
    cv["target"] = None
    cv["notice"] = {
        "key": "onboarding.cv.created",
        "fmt": {"slot": _slot_name(slot), "pages": pages},
    }


def _cv_action(state: State, action: str, arg: str) -> Outcome:
    slots = _cv_slots(state)
    cv = _cv(state)
    keys = [slot.key for slot in slots]
    existing = _cv_existing(state, slots)
    if action == "list":
        cv["view"] = None
        return Outcome()
    if action == "pick" and arg in keys:
        cv["target"] = arg
        cv["skip"] = [k for k in cv["skip"] if k != arg]
        cv["view"] = None
        return Outcome()
    if action == "upload":
        cv["view"] = None
        cv["notice"] = {"key": "onboarding.cv.upload_hint", "fmt": {}}
        return Outcome()
    if action == "create":
        if cv.get("view") == "skip_warn":  # the first slot still without a CV
            missing = _cv_missing(state, slots)
            cv["target"] = missing[0].key if missing else None
        if _cv_target(state, slots, existing) is not None:
            cv["view"] = "templates"
        return Outcome()
    if action == "tpl" and arg in TEMPLATES:
        target = _cv_target(state, slots, existing)
        cv["view"] = None
        if target is not None:
            _cv_create(state, target, arg)
        return Outcome()
    if action == "skip":
        target = _cv_target(state, slots, existing)
        if target is not None:
            cv["got"].pop(target.key, None)
            cv["created"].pop(target.key, None)
            cv["skip"] = [*cv["skip"], target.key]
            cv["target"] = None
        return Outcome()
    if action == "more":
        cv["view"] = "more" if _more_options(state, slots) else None
        return Outcome()
    if action == "add":
        return _cv_add(state, arg)
    if action == "letter" and _letter_offered(state):
        cv["letter"] = not cv.get("letter")
        return Outcome()
    if action == "skip_all":
        if _cv_missing(state, slots) and cv.get("view") != "skip_warn":
            cv["view"] = "skip_warn"  # warn first: no CV, nothing to send
            return Outcome()
        action = "skip_all_yes"
    if action == "skip_all_yes":
        cv["skip"] = [k for k in keys if k not in cv["got"]]
        cv["target"] = None
        cv["view"] = None
        return Outcome(advance=True)
    if action == "done":
        cv["target"] = None
        cv["view"] = None
        return Outcome(advance=True)
    return Outcome()


def _cv_add(state: State, arg: str) -> Outcome:
    """ "Create another version": add the family and/or the offer language
    the new base CV needs, then point the step at it."""
    fid, _, code = arg.partition(":")
    options = {(f, c) for f, c, _ in _more_options(state, _cv_slots(state))}
    cv = _cv(state)
    cv["view"] = None
    if (fid, code) not in options:
        return Outcome()
    answers = _answers(state)
    if fid in _BUILTIN_IDS:
        answers["families"] = _toggle(
            [f for f in answers.get("families") or [] if f != fid], fid, _BUILTIN_IDS
        )
    if code not in (answers.get("offer_langs") or []):
        answers["offer_langs"] = _toggle(
            answers.get("offer_langs") or [], code, A.OFFER_LANGUAGES
        )
    key = variant_key(fid.upper(), code)
    added = next((s for s in _cv_slots(state) if s.key == key), None)
    if added is not None:
        cv["target"] = key
        cv["skip"] = [k for k in cv["skip"] if k != key]
        cv["notice"] = {
            "key": "onboarding.cv.added",
            "fmt": {"slot": _slot_name(added)},
        }
    return Outcome()


def _cv_missing(state: State, slots: list[U.Slot]) -> list[U.Slot]:
    """Slots with neither an upload nor a verified PDF in the profile."""
    have = {*_cv(state)["got"], *_cv_existing(state, slots)}
    return [slot for slot in slots if slot.key not in have]


def _cv_summary(state: State, lang: Lang) -> str:
    slots = _cv_slots(state)
    cv = _cv(state)
    got = cv["got"]
    existing = _cv_existing(state, slots) - set(got)
    uploaded = [
        _slot_name(s)
        + (
            " "
            + t(
                "onboarding.cv.created_mark",
                lang,
                template=_template_name(str(cv["created"][s.key]), lang),
            )
            if s.key in cv["created"]
            else ""
        )
        for s in slots
        if s.key in got
    ]
    kept = [_slot_name(s) for s in slots if s.key in existing]
    missing = [_slot_name(s) for s in _cv_missing(state, slots)]
    parts = [", ".join(uploaded)] if uploaded else []
    if kept:
        parts.append(t("onboarding.cv.summary_kept", lang, slots=", ".join(kept)))
    if missing:
        parts.append(t("onboarding.cv.summary_missing", lang, slots=", ".join(missing)))
    return "; ".join(parts) or t("onboarding.none", lang)


def _summary_render(state: State, lang: Lang) -> Screen:
    return Screen(
        _q("summary", lang, summary=summary_text(state, lang)),
        [
            [
                (t("onboarding.btn.confirm", lang), "confirm"),
                (t("onboarding.btn.edit", lang), "edit"),
            ]
        ],
    )


def _summary_action(state: State, action: str, arg: str) -> Outcome:
    return Outcome(goto="edit") if action == "edit" else Outcome()


def _edit_render(state: State, lang: Lang) -> Screen:
    buttons = [
        (t(f"onboarding.edit.{sid}", lang), f"go:{sid}") for sid in _editable(state)
    ]
    return Screen(
        _q("edit", lang), [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    )


def _edit_action(state: State, action: str, arg: str) -> Outcome:
    if action == "go" and arg in _editable(state):
        state["edit"] = True
        return Outcome(goto=arg)
    return Outcome()


CORE_STEPS: Final[tuple[Step, ...]] = (
    Step("lang", _lang_render, _lang_action),
    _text_step("name", A.clean_text, "onboarding.err.name"),
    _text_step("email", A.clean_email, "onboarding.err.email"),
    _text_step("phone", A.clean_phone, "onboarding.err.phone", skippable=True),
    _text_step("city", A.clean_text, "onboarding.err.text"),
    _text_step("linkedin", A.clean_url, "onboarding.err.linkedin"),
    Step("families", _families_render, _families_action),
    _choice_step("level", A.LEVELS, "onboarding.level"),
    Step("spoken_langs", _spoken_render, _spoken_action),
    Step(
        "spoken_levels",
        _levels_render,
        _levels_action,
        applies=lambda state: bool(_spoken_keys(state)),
        follows="spoken_langs",
    ),
    _text_step("skills", A.clean_skills, "onboarding.err.skills"),
    _choice_step("exp_years", A.EXPERIENCE_YEARS, "onboarding.years"),
    Step("last_role", _ROLE_STEP.render, _role_action, _ROLE_STEP.on_text),
    Step(
        "last_company",
        _COMPANY_STEP.render,
        _COMPANY_STEP.on_action,
        _COMPANY_STEP.on_text,
        applies=lambda state: bool(_answers(state).get("last_role")),
        follows="last_role",
    ),
    _text_step(
        "education", A.clean_education, "onboarding.err.education", skippable=True
    ),
    _multi_step("work_modes", A.WORK_MODES, "onboarding.mode"),
    Step(
        "work_city",
        _work_city_render,
        _work_city_action,
        _text_step("work_city", A.clean_text, "onboarding.err.text").on_text,
    ),
    Step("country", _country_render, _country_action, _country_text),
    Step("salary", _salary_render, _salary_action, _salary_text),
    Step(
        "currency",
        _currency_render,
        _currency_action,
        _currency_text,
        applies=lambda state: _answers(state).get("min_salary") is not None,
        follows="salary",
    ),
    _multi_step("offer_langs", A.OFFER_LANGUAGES, "onboarding.olang", per_row=4),
    Step("portals", _portals_render, _portals_action),
    Step(
        CV_STEP,
        _cv_render,
        _cv_action,
        applies=lambda state: bool(_cv_slots(state)),
    ),
)
SUMMARY: Final[Step] = Step("summary", _summary_render, _summary_action)
# Reached from another step, never in the linear order.
SIDE_STEPS: Final[tuple[Step, ...]] = (
    Step("custom_titles", _custom_render, _custom_action, _custom_text),
    Step(
        "spoken_other", _spoken_other_render, _spoken_other_action, _spoken_other_text
    ),
    Step("edit", _edit_render, _edit_action),
)
EDITABLE: Final[tuple[str, ...]] = tuple(s.id for s in CORE_STEPS if s.follows is None)


# ``/setup <section>`` (T9): the steps of each section (ids of answers.SECTIONS).
SECTION_STEPS: Final[dict[str, tuple[str, ...]]] = {
    "contact": ("name", "email", "phone", "city", "linkedin"),
    "positions": ("families",),
    "level": ("level",),
    "background": (
        "spoken_langs",
        "spoken_levels",
        "skills",
        "exp_years",
        "last_role",
        "last_company",
        "education",
    ),
    "location": ("work_modes", "work_city", "country"),
    "salary": ("salary", "currency"),
    "offer_langs": ("offer_langs",),
    "portals": ("portals",),
    "cvs": (CV_STEP,),
}
# Accepted besides the section id and its EN/ES names (``onboarding.section.*``),
# compared after :func:`_normalize`.
_SECTION_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "contact": ("contacto", "datos"),
    "positions": ("posiciones", "roles", "families", "familias"),
    "level": ("nivel",),
    "background": (
        "perfil",
        "experience",
        "experiencia",
        "skills",
        "habilidades",
        "education",
        "formacion",
        "spoken-languages",
        "idiomas-hablados",
    ),
    "location": ("ubicacion", "work-mode", "modalidad", "city", "ciudad"),
    "salary": ("sueldo",),
    "offer_langs": ("offer-langs", "idiomas-de-las-ofertas"),
    "portals": ("portales",),
    "cvs": ("cv", "curriculum", "curriculums", "base-cvs", "cv-base"),
}


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", "-", plain.strip().casefold())


def section_names(lang: Lang) -> list[str]:
    """The section names ``/setup <section>`` shows in ``lang``."""
    return [t(f"onboarding.section.{sid}", lang) for sid in A.SECTIONS]


def find_section(text: str) -> str | None:
    """The section id ``text`` names (an id, an EN/ES name or an alias)."""
    wanted = _normalize(text)
    for sid in A.SECTIONS:
        names = {
            sid,
            *(t(f"onboarding.section.{sid}", lg) for lg in ("en", "es")),
            *_SECTION_ALIASES.get(sid, ()),
        }
        if wanted in {_normalize(name) for name in names}:
            return sid
    return None


def _order(state: State | None = None) -> list[Step]:
    """The steps asked, in order: every one, or a section's (``/setup <section>``)."""
    section = state.get("section") if state is not None else None
    if section in SECTION_STEPS:
        ids = SECTION_STEPS[section]
        return [*(s for s in CORE_STEPS if s.id in ids), SUMMARY]
    return [*CORE_STEPS, *STEP_HOOKS, SUMMARY]


def _editable(state: State) -> tuple[str, ...]:
    """The steps the summary's Edit offers (a section's own, in a section)."""
    ids = {s.id for s in _order(state)}
    return tuple(sid for sid in EDITABLE if sid in ids)


def _steps() -> dict[str, Step]:
    return {s.id: s for s in [*_order(), *SIDE_STEPS]}


# --- summary ---------------------------------------------------------------------------


def _labels(values: list[str], key: str, lang: Lang) -> str:
    return join_list([t(f"{key}.{v}", lang) for v in values], lang) if values else "—"


def summary_text(state: State, lang: Lang) -> str:
    """Every answer, one per line, in ``lang``."""
    a = _answers(state)
    none = t("onboarding.none", lang)
    families = [
        f.label for f in BUILTIN_REGISTRY.families if f.id in (a.get("families") or [])
    ]
    kept = a.get("kept_families") or []
    families.extend(label for fid, label in _declared(state) if fid in kept)
    if a.get("custom_titles"):
        families.append(
            t("onboarding.custom", lang, titles=", ".join(a["custom_titles"]))
        )
    salary = (
        t(
            "onboarding.summary.salary",
            lang,
            amount=a["min_salary"],
            currency=a.get("currency") or "EUR",
        )
        if a.get("min_salary") is not None
        else none
    )
    level = a.get("level")
    years = a.get("exp_years")
    values = {
        "lang": _LANGUAGE_NAMES.get(str(state.get("lang")), none),
        "name": a.get("name") or none,
        "email": a.get("email") or none,
        "phone": a.get("phone") or none,
        "city": a.get("city") or none,
        "linkedin": a.get("linkedin") or none,
        "families": ", ".join(families) or none,
        "level": t(f"onboarding.level.{level}", lang) if level else none,
        "spoken_langs": ", ".join(
            f"{_language_name(key, lang)} ({_level_label(lvl, lang)})"
            for key, lvl in A.spoken_languages(a)
        )
        or none,
        "skills": ", ".join(a.get("skills") or []) or none,
        "exp_years": t(f"onboarding.summary.years.{years}", lang) if years else none,
        "last_role": " · ".join(
            str(v) for v in (a.get("last_role"), a.get("last_company")) if v
        )
        or none,
        "education": "; ".join(A.education_entries(a.get("education"))) or none,
        "work_modes": _labels(a.get("work_modes") or [], "onboarding.mode", lang),
        "work_city": a.get("work_city") or none,
        "country": a.get("country") or none,
        "salary": salary,
        "offer_langs": _labels(a.get("offer_langs") or [], "onboarding.olang", lang),
        "portals": ", ".join(portal_label(p) for p in a.get("portals") or []) or none,
    }
    if _cv_slots(state):
        values[CV_STEP] = _cv_summary(state, lang)
    if state.get("section") in SECTION_STEPS:  # a section: its lines only
        shown = _editable(state)
        values = {k: v for k, v in values.items() if k in shown}
    if _cv(state).get("letter") and _letter_offered(state):
        values["cover_letter"] = t("onboarding.letter.summary", lang)
    return "\n".join(
        f"{t(f'onboarding.edit.{k}', lang)}: {v}" for k, v in values.items()
    )


# --- engine ----------------------------------------------------------------------------


def new_state(lang: Lang, section: str | None = None) -> State:
    state: State = {"history": [], "lang": lang, "a": {}, "edit": False}
    if section is not None:
        state["section"] = section
    state["step"] = _order(state)[0].id
    return state


def clear(user_data: dict[Any, Any]) -> None:
    """Drop the conversation and every base CV it uploaded but did not confirm."""
    state = user_data.pop(STATE_KEY, None)
    user_data.pop(STATE_AT_KEY, None)
    if isinstance(state, dict) and isinstance(state.get("dir"), str):
        U.discard_pending(Path(state["dir"]))


def live_state(user_data: dict[Any, Any]) -> State | None:
    """The running conversation, or ``None`` (an expired one is dropped)."""
    state = user_data.get(STATE_KEY)
    asked_at = user_data.get(STATE_AT_KEY)
    if not isinstance(state, dict) or state.get("step") not in _steps():
        clear(user_data)
        return None
    if not isinstance(asked_at, int | float) or time.time() - asked_at > TTL_SECONDS:
        clear(user_data)
        return None
    return state


def expects_text(user_data: dict[Any, Any]) -> bool:
    """True while the current step takes a typed answer."""
    state = live_state(user_data)
    return state is not None and _steps()[state["step"]].on_text is not None


def _store(user_data: dict[Any, Any], state: State) -> None:
    user_data[STATE_KEY] = state
    user_data[STATE_AT_KEY] = time.time()


def _lang_of(state: State, env: Env) -> Lang:
    lang = state.get("lang")
    return lang if lang in _LANGUAGE_NAMES else env.lang


def _next_id(state: State, current: str) -> str:
    order = _order(state)
    ids = [s.id for s in order]
    after = order[ids.index(current) + 1 :] if current in ids else order[-1:]
    for step in after:
        if not step.applies(state):
            continue
        if state.get("edit") and step.follows != current:
            return SUMMARY.id
        return step.id
    return SUMMARY.id


def _go(state: State, target: str) -> None:
    state["history"] = [*state["history"], state["step"]][-50:]
    state["step"] = target
    state.pop("portal", None)
    if target == SUMMARY.id:
        state["edit"] = False


def _apply(state: State, outcome: Outcome) -> None:
    if outcome.back:
        _back(state)
    elif outcome.goto is not None:
        _go(state, outcome.goto)
    elif outcome.advance:
        _go(state, _next_id(state, state["step"]))


def _back(state: State) -> None:
    history: list[str] = state["history"]
    if history:
        state["step"] = history[-1]
        state["history"] = history[:-1]
        state.pop("portal", None)


def _progress(state: State, lang: Lang) -> str | None:
    """``Setup n/total``: the questions of the run (a section's own in a
    section), counted so the total never changes while answering: a
    follow-up (each language's level, the company, the currency) shows the
    number of the question it follows, and the base CVs step counts in the
    full run even when the answers end up skipping it (the summary then
    follows the portals; the summary itself has no number)."""
    order = [s for s in _order(state) if s.follows is None and s is not SUMMARY]
    ids = [s.id for s in order]
    steps = _steps()
    anchor = state["step"]
    while anchor in steps and steps[anchor].follows is not None:
        anchor = str(steps[anchor].follows)
    if anchor not in ids:
        return None
    return t("onboarding.progress", lang, n=ids.index(anchor) + 1, total=len(ids))


def render(state: State, lang: Lang, error: str | None = None) -> tuple[str, Any]:
    """The current question as ``(text, InlineKeyboardMarkup)``."""
    sid = state["step"]
    screen = _steps()[sid].render(state, lang)
    parts = [p for p in (_progress(state, lang), screen.text) if p]
    if error is not None:
        parts.insert(0, t(error, lang))
    nav = [(t("onboarding.btn.cancel", lang), "cancel")]
    if state["history"]:
        nav.insert(0, (t("onboarding.btn.back", lang), "back"))
    rows = [
        [
            InlineKeyboardButton(label, callback_data=f"{PREFIX}:{sid}:{action}")
            for label, action in row
        ]
        for row in [*screen.rows, nav]
        if row
    ]
    return "\n\n".join(parts), InlineKeyboardMarkup(rows)


def _offer_declared_families(state: State, profile_dir: Path) -> None:
    """Preselect the families ``preferences.toml`` already declares, so a
    re-run keeps them unless the user deselects them (an unreadable file
    offers none; Confirm then reports it)."""
    try:
        builtin, others = A.families_in_effect(profile_dir / "preferences.toml")
    except ProfileError:
        return
    answers = _answers(state)
    answers["families"] = builtin
    state["declared"] = [[fid, lbl] for fid, lbl in others]
    answers["kept_families"] = [fid for fid, _ in others]


def _section_arg(text: str | None) -> str:
    """What follows ``/setup`` (``/setup@bot``) in the message, or ``""``."""
    parts = (text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def start(reply: Any, user_data: dict[Any, Any], env: Env) -> None:
    """``/setup [section]``: ask the first question (a running setup starts
    over), prefilled with the profile's current values."""
    if env.profile_dir is None:
        await reply.reply_text(t("onboarding.no_profile_dir", env.lang))
        return
    arg = _section_arg(getattr(reply, "text", None))
    section = find_section(arg) if arg else None
    if arg and section is None:
        await reply.reply_text(
            t(
                "onboarding.section.unknown",
                env.lang,
                section=arg,
                sections=", ".join(section_names(env.lang)),
            )
        )
        return
    if section is not None and missing_profile_files(env.profile_dir):
        await reply.reply_text(t("onboarding.section.needs_setup", env.lang))
        return
    clear(user_data)
    U.discard_pending(env.profile_dir)  # left over by a restart
    state = new_state(env.lang, section)
    state["dir"] = str(env.profile_dir)
    state["a"] = current_answers(env.profile_dir)
    if env.config_path is not None:  # the file the bot runs with beats the record
        enabled = read_enabled_portals(env.config_path)
        if enabled is not None:
            state["a"]["portals"] = enabled
    _offer_declared_families(state, env.profile_dir)
    _store(user_data, state)
    text, markup = render(state, env.lang)
    await reply.reply_text(text, reply_markup=markup)


async def on_text(message: Any, user_data: dict[Any, Any], env: Env) -> bool:
    """A plain message: the answer of the current step when it expects text
    (returns ``True``); ``False`` leaves the message to the other captures."""
    state = live_state(user_data)
    if state is None:
        return False
    step = _steps()[state["step"]]
    if step.on_text is None:
        return False
    outcome = step.on_text(state, message.text or "")
    _apply(state, outcome)
    _store(user_data, state)
    text, markup = render(state, _lang_of(state, env), outcome.error)
    await message.reply_text(text, reply_markup=markup)
    return True


async def on_document(message: Any, user_data: dict[Any, Any], env: Env) -> bool:
    """A Telegram document: the PDF of the current base CV slot while step 9
    runs (returns ``True``); ``False`` leaves it to the bot's other handlers.

    The size is checked before downloading; the file goes to a temporary name
    in the pending area and is kept only once it is a readable PDF.
    """
    state = live_state(user_data)
    document = getattr(message, "document", None)
    if state is None or state["step"] != CV_STEP or document is None:
        return False
    profile_dir = env.profile_dir
    if profile_dir is None:  # cannot happen: start() refuses without one
        return False
    lang = _lang_of(state, env)
    slots = _cv_slots(state)
    target = _cv_target(state, slots, _cv_existing(state, slots))
    fmt = {"mb": U.MAX_BYTES // (1024 * 1024), "pages": U.MAX_PAGES}
    try:
        if target is None:
            raise U.UploadError("onboarding.err.cv_pick_slot")
        U.check_document(document.file_name, document.mime_type, document.file_size)
        tmp = U.new_temp(profile_dir)
        try:
            tg_file = await document.get_file()
            await tg_file.download_to_drive(custom_path=tmp)
        except (TelegramError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            raise U.UploadError("onboarding.err.cv_download") from exc
        sha, pages = await asyncio.to_thread(
            U.accept_download, tmp, profile_dir, target.key
        )
    except U.UploadError as exc:
        notice = t(exc.key, lang, **fmt)
    else:
        cv = _cv(state)
        cv["got"][target.key] = sha
        cv["skip"] = [k for k in cv["skip"] if k != target.key]
        cv["target"] = None
        count = t(
            "onboarding.cv.pages.one" if pages == 1 else "onboarding.cv.pages.other",
            lang,
            n=pages,
        )
        notice = t("onboarding.cv.received", lang, slot=_slot_name(target), pages=count)
    _store(user_data, state)
    text, markup = render(state, lang)
    await message.reply_text(f"{notice}\n\n{text}", reply_markup=markup)
    return True


async def _edit(query: Any, text: str, reply_markup: Any = None) -> None:
    """Edit the button's message; Telegram's "message is not modified"
    (the same button pressed again: nothing to change) is not an error.
    Any other :class:`BadRequest` propagates."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def on_callback(
    query: Any, user_data: dict[Any, Any], env: Env, parts: list[str]
) -> None:
    """An ``ob:<step>:<action>[:<arg>]`` button."""
    state = live_state(user_data)
    if state is None:
        await query.message.reply_text(t("onboarding.expired", env.lang))
        return
    lang = _lang_of(state, env)
    sid = parts[0] if parts else ""
    action = parts[1] if len(parts) > 1 else ""
    arg = ":".join(parts[2:])
    if action == "cancel":
        clear(user_data)
        await _edit(query, t("onboarding.cancelled", lang), reply_markup=None)
        return
    if sid != state["step"]:
        _store(user_data, state)
        text, markup = render(state, lang)
        await query.message.reply_text(t("onboarding.stale", lang))
        await query.message.reply_text(text, reply_markup=markup)
        return
    if action == "back":
        _back(state)
        outcome = Outcome()
    elif sid == SUMMARY.id and action == "confirm":
        await _confirm(query, user_data, state, env)
        return
    else:
        handler = _steps()[sid].on_action
        outcome = handler(state, action, arg) if handler is not None else Outcome()
        _apply(state, outcome)
    _store(user_data, state)
    lang = _lang_of(state, env)
    text, markup = render(state, lang, outcome.error)
    await _edit(query, text, reply_markup=markup)


# The write itself lives in :mod:`cva.onboarding.commit`, shared with the
# extension's Profile tab (``POST /profile``); :data:`CONFIRM_HOOKS` stays here.
_ConfirmFailed = ConfirmFailed


def _commit_all(
    profile_dir: Path,
    answers: dict[str, Any],
    slots: list[U.Slot],
    got: dict[str, str],
    lang: Lang,
    section: str | None = None,
    config_path: Path | None = None,
    *,
    cover_letter: bool = False,
    declared: list[tuple[str, str]] | None = None,
) -> Committed:
    """:func:`cva.onboarding.commit.commit_all` with :data:`CONFIRM_HOOKS`."""
    return commit_all(
        profile_dir,
        answers,
        slots,
        got,
        lang,
        section,
        CONFIRM_HOOKS,
        config_path,
        cover_letter=cover_letter,
        declared=declared or [],
    )


def _refresh_created(state: State, profile_dir: Path, slots: list[U.Slot]) -> None:
    """Re-render the CVs lazyhunt created from the final answers (an answer
    edited after creating one shows in it); a failure keeps the earlier PDF."""
    cv = _cv(state)
    by_key = {slot.key: slot for slot in slots}
    for key, template in list(cv["created"].items()):
        if key not in by_key or key not in cv["got"] or template not in TEMPLATES:
            continue
        try:
            sha, _ = starter.create_pending_cv(
                profile_dir, _answers(state), by_key[key], cast(Template, template)
            )
        except (U.UploadError, CvgenError, OSError):
            continue
        cv["got"][key] = sha


@dataclass(frozen=True)
class ConfirmExtra:
    """One more line (and optional buttons) in the Confirm reply."""

    line: str
    markup: InlineKeyboardMarkup | None = None


# Extension point of the Confirm reply: each callable may add one line and a
# keyboard after the summary of what was saved (e.g. "pair the extension now",
# beta3-ux T1 wires it from bot.py). ``None`` adds nothing.
def _offer_extension_pairing(_env: Env, lang: Lang) -> ConfirmExtra:
    """After Confirm: point to the one-time code that pairs the extension."""
    from cva.commands import command_name  # local: commands imports i18n only

    return ConfirmExtra(
        line=t("bot.connect.offer", lang, connect=command_name("conectar", lang))
    )


CONFIRM_REPLY_EXTRAS: list[Callable[[Env, Lang], ConfirmExtra | None]] = [
    _offer_extension_pairing
]


async def _confirm(
    query: Any, user_data: dict[Any, Any], state: State, env: Env
) -> None:
    lang = _lang_of(state, env)
    profile_dir = env.profile_dir
    if profile_dir is None:  # cannot happen: start() refuses without one
        await query.message.reply_text(t("onboarding.no_profile_dir", lang))
        return
    answers = _answers(state)
    slots = _cv_slots(state)  # before the new preferences.toml replaces the old
    no_cv = [_slot_name(s) for s in _cv_missing(state, slots)]
    cv = _cv(state)
    _refresh_created(state, profile_dir, slots)
    try:
        committed = _commit_all(
            profile_dir,
            answers,
            slots,
            cv["got"],
            lang,
            state.get("section"),
            env.config_path,
            cover_letter=bool(cv.get("letter")),
            declared=_declared(state),
        )
    except _ConfirmFailed as failed:
        _store(user_data, state)  # uploads kept: Confirm can simply be retried
        error = describe_load_error(profile_dir, failed.cause)
        text = t("onboarding.write_failed", lang, error=error)
        if failed.undo is None:
            text += "\n" + t("onboarding.write_undone", lang)
        else:
            undo = describe_load_error(profile_dir, failed.undo)
            text += "\n\n" + t("onboarding.rollback_failed", lang, error=undo)
        await query.message.reply_text(text)
        return
    clear(user_data)
    lines = [t("onboarding.saved", lang)]
    if committed.stored:
        lines.append(t("onboarding.cv.saved", lang, count=len(committed.stored)))
    created = [s for s in slots if s.key in cv["created"] and s.key in committed.stored]
    if created:
        lines.append(
            t(
                "onboarding.cv.created_saved",
                lang,
                slots=", ".join(_slot_name(s) for s in created),
            )
        )
    if committed.cover_letter:
        lines.append(t("onboarding.letter.saved", lang))
    if committed.identity in ("kept", "kept_unrecorded"):
        key = (
            "onboarding.identity.kept"
            if committed.identity == "kept"
            else "onboarding.identity.kept_unrecorded"
        )
        lines.append(t(key, lang, claim=committed.identity_claim))
    if no_cv and state.get("section") in (None, "cvs"):
        lines.append(t("onboarding.cv.not_deliverable", lang, slots=", ".join(no_cv)))
    if lang != env.lang:
        if env.config_path is not None:
            try:
                A.persist_ui_language(env.config_path, lang)
            except (OSError, ValueError) as exc:
                lines.append(
                    t("onboarding.write_failed", lang, error=type(exc).__name__)
                )
            else:
                lines.append(t("onboarding.saved_language", lang))
        if env.set_lang is not None:
            env.set_lang(lang)
    if env.service is not None:
        try:
            env.service.reload_preferences()
        except ProfileError as exc:
            error = describe_load_error(profile_dir, exc)
            lines.append(t("onboarding.reload_failed", lang, error=error))
    if committed.portals is not None:
        if env.set_portals is not None:
            env.set_portals(committed.portals)
        labels = ", ".join(portal_label(p) for p in committed.portals)
        lines.append(
            t(
                "onboarding.portals.enabled",
                lang,
                portals=labels or t("onboarding.none", lang),
            )
        )
    elif state.get("section") in (None, "portals"):
        lines.append(
            t(
                "onboarding.portals.line",
                lang,
                line=enabled_portals_line(list(answers.get("portals") or [])),
            )
        )
    missing = missing_profile_files(profile_dir)
    if missing:
        lines.append(t("onboarding.missing", lang, missing=", ".join(missing)))
    markup: InlineKeyboardMarkup | None = None
    full_run = state.get("section") is None
    # Extras (e.g. "connect the extension") come before the next step: the
    # extension must be paired before it can suggest searches. Section
    # re-runs keep the reply short.
    for extra in CONFIRM_REPLY_EXTRAS if full_run else ():
        added = extra(env, lang)
        if added is not None:
            lines.append(added.line)
            markup = added.markup or markup
    if full_run:
        lines.append(t("onboarding.next_step", lang))
    await _edit(
        query, _q("summary", lang, summary=summary_text(state, lang)), reply_markup=None
    )
    if markup is None:
        await query.message.reply_text("\n\n".join(lines))
    else:
        await query.message.reply_text("\n\n".join(lines), reply_markup=markup)
