"""``/setup <section>`` (onboarding-v2 T9, S9/S10): re-run one section.

Driven through the bot's public handlers with the fakes of ``tests/test_bot.py``
(the :class:`Driver` of ``tests/test_onboarding.py``) on a copy of the demo
profile: only the section's questions are asked, prefilled with the current
values, then a short summary and Confirm through the same validated,
all-or-nothing path as the full questionnaire.
"""

import asyncio
import hashlib
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import bot
from cva.claimsdb import load_claims
from cva.config import Settings
from cva.i18n import Lang, t
from cva.onboarding import flow
from cva.profile_prefs import load_preferences
from cva.service import JobService
from tests.test_bot import FakeMessage, FakeUpdate
from tests.test_onboarding import (
    Driver,
    _answer_everything,
    _full_app,
    _setup_mode,
)


def _start(d: Driver, text: str) -> FakeMessage:
    message = FakeMessage(text)
    asyncio.run(bot.setup_command(FakeUpdate(message=message), d.context))
    d._seen(message.replies, message.markups)
    return message


def _hashes(profile: Path) -> dict[str, str]:
    """sha256 of every file of the profile, by relative path."""
    return {
        str(p.relative_to(profile)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(profile.rglob("*"))
        if p.is_file()
    }


def _prefs(profile: Path) -> dict[str, Any]:
    return tomllib.loads((profile / "preferences.toml").read_text("utf-8"))


def _labels(d: Driver) -> list[str]:
    return [label for label, _ in d.buttons()]


Full = tuple[Driver, Path, Path, JobService]


@pytest.fixture
def full(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> Full:
    return _full_app(tmp_path, clean_settings, monkeypatch, "en")


# --- rule: a section asks only its own questions, then a short summary ------------


@pytest.mark.parametrize(
    ("section", "first"),
    [
        ("contact", "name"),
        ("positions", "families"),
        ("level", "level"),
        ("background", "spoken_langs"),
        ("location", "work_modes"),
        ("salary", "salary"),
        ("offer-languages", "offer_langs"),
        ("cvs", "cv_pdfs"),
    ],
)
def test_each_section_starts_at_its_first_question(
    full: Full, section: str, first: str
) -> None:
    d = full[0]
    _start(d, f"/setup {section}")
    assert d.step == first


def test_setup_salary_writes_only_the_salary_and_keeps_every_other_byte(
    full: Full,
) -> None:
    d, profile, _, service = full
    before = _hashes(profile)
    prefs_before = _prefs(profile)
    _start(d, "/setup salary")
    assert d.step == "salary"
    d.text("40k")
    assert d.step == "currency"
    d.press("go:EUR")
    assert d.step == "summary"
    # The short summary: only the section's lines.
    assert t("onboarding.edit.salary", "en") in d.last
    assert t("onboarding.edit.name", "en") not in d.last
    assert t("onboarding.edit.level", "en") not in d.last
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))

    after = _hashes(profile)
    changed = {rel for rel in after if before.get(rel) != after[rel]}
    assert changed <= {
        "preferences.toml",
        "preferences.toml.bak",
        "onboarding_answers.json",
    }
    assert "preferences.toml" in changed
    prefs_after = _prefs(profile)
    search = prefs_after.pop("search")
    assert search == {
        **prefs_before.pop("search", {}),
        "min_salary": 40000,
        "currency": "EUR",
    }
    assert prefs_after == prefs_before  # every other key identical
    assert service.loaded_preferences.search.min_salary == 40000  # reloaded


# --- rule: prefilled with the current values ---------------------------------------


_SEARCH = (
    '[search]\nlevel = "mid"\nmin_salary = 35000\ncurrency = "GBP"\n'
    'work_modes = ["remote"]\n\n[stack_wall]'
)


def _with_search(profile: Path) -> None:
    prefs = profile / "preferences.toml"
    text = prefs.read_text("utf-8").replace("[stack_wall]", _SEARCH)
    prefs.write_text(text, encoding="utf-8")


def test_section_steps_are_prefilled_from_the_profile(full: Full) -> None:
    d, profile, _, _ = full
    _with_search(profile)
    _start(d, "/setup salary")
    assert t("onboarding.btn.keep", "en", value="35000") in _labels(d)
    d.press("keep")
    assert "✅ GBP" in _labels(d)
    _start(d, "/setup level")
    assert "✅ " + t("onboarding.level.mid", "en") in _labels(d)
    _start(d, "/setup location")
    assert "✅ " + t("onboarding.mode.remote", "en") in _labels(d)
    _start(d, "/setup offer-languages")
    on = {label for label in _labels(d) if label.startswith("✅")}
    assert on == {"✅ " + t(f"onboarding.olang.{c}", "en") for c in ("es", "ca", "en")}
    # The demo profile has no name in profile.toml: the identity claim's.
    _start(d, "/setup contact")
    assert t("onboarding.btn.keep", "en", value="Alex Rivera") in _labels(d)


def test_keeping_every_value_changes_no_profile_file(full: Full) -> None:
    d, profile, _, _ = full
    _with_search(profile)
    before = _hashes(profile)
    _start(d, "/setup salary")
    d.press("keep")
    d.press("go:GBP")
    assert d.step == "summary"
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    after = _hashes(profile)
    assert {r for r in after if before.get(r) != after[r]} == {
        "onboarding_answers.json"
    }


# --- rule: background re-run replaces only the setup-* claims ---------------------


def test_setup_background_is_prefilled_and_replaces_only_setup_claims(
    full: Full,
) -> None:
    d, profile, _, _ = full
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    claims_before = (profile / "claims.json").read_bytes()
    prefs_before = (profile / "preferences.toml").read_bytes()

    _start(d, "/setup background")
    assert "✅ " + t("onboarding.olang.es", "en") in _labels(d)
    d.press("done")
    assert "✅ " + t("onboarding.cefr.native", "en") in _labels(d)
    d.press("go:native")
    d.press("go:C1")
    assert d.step == "skills"
    assert t("onboarding.btn.keep", "en", value="Python, SQL") in _labels(d)
    d.press("keep")
    d.press("go:3-5")
    d.press("keep")  # last role
    d.press("keep")  # company
    d.press("keep")  # education
    assert d.step == "summary"
    d.press("confirm")
    # Same answers: same claims, preferences untouched.
    assert (profile / "claims.json").read_bytes() == claims_before
    assert (profile / "preferences.toml").read_bytes() == prefs_before

    _start(d, "/setup background")
    d.press("done")
    d.press("go:native")
    d.press("go:C1")
    d.text("Go, Rust")
    d.press("go:3-5")
    d.press("keep")
    d.press("keep")
    d.press("keep")
    d.press("confirm")
    claims = json.loads((profile / "claims.json").read_text("utf-8"))
    ids = [c["id"] for c in claims if c["id"].startswith("setup-")]
    assert "setup-skill-go" in ids and "setup-skill-python" not in ids
    assert (profile / "preferences.toml").read_bytes() == prefs_before


def test_other_sections_never_rewrite_hand_edited_setup_claims(full: Full) -> None:
    d, profile, _, _ = full
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    path = profile / "claims.json"
    edited = path.read_text("utf-8").replace('"SQL"', '"PostgreSQL"')
    path.write_text(edited, encoding="utf-8")
    _start(d, "/setup level")
    d.press("go:junior")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert path.read_text("utf-8") == edited
    assert load_preferences(profile / "preferences.toml").search.level == "junior"


# --- rule: /setup alone is the full questionnaire, now prefilled ------------------


def test_full_setup_still_starts_with_the_language_and_is_prefilled(
    full: Full,
) -> None:
    d = full[0]
    d.start()
    assert d.step == "lang"
    d.press("go:en")
    assert d.step == "name"
    assert t("onboarding.btn.keep", "en", value="Alex Rivera") in _labels(d)


# --- rule: unknown section -> the list ---------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_unknown_section_replies_with_the_list(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    lang: Lang,
) -> None:
    d = _full_app(tmp_path, clean_settings, monkeypatch, lang)[0]
    message = _start(d, "/setup nonsense")
    assert d.step is None
    reply = message.replies[-1]
    assert "nonsense" in reply
    for name in flow.section_names(lang):
        assert name in reply


# --- rule: Spanish names and aliases ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "first"),
    [
        ("/configurar sueldo", "salary"),
        ("/configurar Ubicación", "work_modes"),
        ("/configurar idiomas-ofertas", "offer_langs"),
        ("/setup offer languages", "offer_langs"),
        ("/configurar trayectoria", "spoken_langs"),
        ("/setup@lazyhunt_bot puestos", "families"),
    ],
)
def test_section_names_and_aliases_in_both_languages(
    full: Full, text: str, first: str
) -> None:
    d = full[0]
    _start(d, text)
    assert d.step == first


# --- rule: all-or-nothing -----------------------------------------------------------


def test_a_failed_section_confirm_restores_every_file(
    full: Full, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, profile, _, _ = full
    before = _hashes(profile)

    def broken_hook(profile_dir: Path, answers: dict[str, object]) -> None:
        raise flow.ProfileError("hook failed")

    monkeypatch.setattr(flow, "CONFIRM_HOOKS", [flow.confirm_hook, broken_hook])
    _start(d, "/setup salary")
    d.text("40000")
    d.press("go:EUR")
    d.press("confirm")
    assert d.step == "summary" and "hook failed" in d.replies[-1]
    assert _hashes(profile) == before


# --- rule: a section needs a set-up profile -----------------------------------------


def test_a_section_before_the_first_setup_asks_for_the_full_one(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    message = _start(d, "/setup salary")
    assert d.step is None
    assert message.replies[-1] == t("onboarding.section.needs_setup", "en")


def test_background_is_prefilled_from_the_setup_claims_without_the_record(
    full: Full,
) -> None:
    d, profile, _, _ = full
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    (profile / "onboarding_answers.json").unlink()
    claims_before = (profile / "claims.json").read_bytes()

    _start(d, "/setup background")
    on = {label for label in _labels(d) if label.startswith("✅")}
    assert on == {"✅ " + t(f"onboarding.olang.{c}", "en") for c in ("es", "en")}
    d.press("done")
    d.press("go:native")
    d.press("go:C1")
    assert t("onboarding.btn.keep", "en", value="Python, SQL") in _labels(d)
    d.press("keep")
    assert "✅ " + t("onboarding.years.3-5", "en") in _labels(d)
    d.press("go:3-5")
    assert t("onboarding.btn.keep", "en", value="Backend Developer") in _labels(d)
    d.press("keep")
    assert t("onboarding.btn.keep", "en", value="Acme") in _labels(d)
    d.press("keep")
    assert t("onboarding.btn.keep", "en", value="BSc Computer Science") in _labels(d)
    d.press("keep")
    d.press("confirm")
    assert (profile / "claims.json").read_bytes() == claims_before


# --- rule: /setup contact updates the identity claim it generated (fix) ------------


def _identity_claims(profile: Path) -> dict[str, dict[str, Any]]:
    claims = json.loads((profile / "claims.json").read_text("utf-8"))
    return {c["id"]: c for c in claims if c["category"] == "identity"}


def _contact_new_email(d: Driver, phone: str | None = "+34 600 111 222") -> None:
    _start(d, "/setup contact")
    assert d.step == "name"
    d.press("keep")
    d.text("new.mail@example.com")
    if phone is None:
        d.press("skip")
    else:
        d.text(phone)
    d.press("keep")  # city
    d.press("keep")  # LinkedIn
    assert d.step == "summary"
    d.press("confirm")


def _set_up(tmp_path: Path, lang: Lang = "en") -> tuple[Driver, Path]:
    d, profile, _ = _setup_mode(tmp_path, lang)
    d.start()
    _answer_everything(d, lang)
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", lang))
    return d, profile


def test_setup_contact_regenerates_the_identity_claim_setup_wrote(
    tmp_path: Path,
) -> None:
    d, profile = _set_up(tmp_path)
    before = _identity_claims(profile)
    assert "jose@example.com" in before["identity-contact"]["text"]
    others = [
        c
        for c in json.loads((profile / "claims.json").read_text("utf-8"))
        if c["category"] != "identity"
    ]
    _contact_new_email(d)
    reply = d.replies[-1]
    assert reply.startswith(t("onboarding.saved", "en"))
    assert t("onboarding.identity.kept", "en", claim="identity-contact") not in reply
    after = _identity_claims(profile)
    text = after["identity-contact"]["text"]
    assert "new.mail@example.com" in text and "jose@example.com" not in text
    assert text.startswith("José Pérez · Valencia")
    assert after["identity-phone"]["text"] == "+34 600 111 222"
    claims = json.loads((profile / "claims.json").read_text("utf-8"))
    assert [c for c in claims if c["category"] != "identity"] == others
    load_claims(profile / "claims.json")  # the bot can load what was written


def test_full_rerun_regenerates_the_identity_claim_setup_wrote(
    tmp_path: Path,
) -> None:
    d, profile = _set_up(tmp_path)
    d.start()
    d.press("go:en")
    d.press("keep")  # name
    d.text("other@example.com")
    for _ in range(40):  # keep every later answer up to the summary
        if d.step == "summary":
            break
        buttons = [(label, a.split(":", 2)[2]) for label, a in d.buttons()]
        ticked = [a for label, a in buttons if label.startswith("✅") and "go:" in a]
        actions = [a for _, a in buttons]
        keep = ("keep", "skip", "done", "skip_all")
        d.press(ticked[0] if ticked else next(a for a in keep if a in actions))
    assert d.step == "summary"
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    text = _identity_claims(profile)["identity-contact"]["text"]
    assert "other@example.com" in text and "jose@example.com" not in text


@pytest.mark.parametrize("lang", ["en", "es"])
def test_setup_contact_keeps_a_hand_edited_identity_claim_and_says_so(
    tmp_path: Path, lang: Lang
) -> None:
    d, profile = _set_up(tmp_path, lang)
    path = profile / "claims.json"
    edited = path.read_text("utf-8").replace(
        "linkedin.com/in/jose", "linkedin.com/in/jose · github.com/jose"
    )
    path.write_text(edited, encoding="utf-8")
    raw = path.read_bytes()
    _contact_new_email(d)
    reply = d.replies[-1]
    assert reply.startswith(t("onboarding.saved", lang))
    assert t("onboarding.identity.kept", lang, claim="identity-contact") in reply
    assert path.read_bytes() == raw  # byte-identical


def test_full_rerun_on_the_demo_keeps_its_hand_written_identity_claim(
    full: Full,
) -> None:
    d, profile, _, _ = full
    raw = (profile / "claims.json").read_bytes()
    span = raw[: raw.index(b"identity-contact_en")]  # its identity claim
    before = _identity_claims(profile)
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    reply = d.replies[-1]
    # No onboarding_answers.json: kept because /setup did not create it.
    kept = t("onboarding.identity.kept_unrecorded", "en", claim="identity-contact")
    assert kept in reply
    assert t("onboarding.identity.kept", "en", claim="identity-contact") not in reply
    assert _identity_claims(profile) == before
    assert (profile / "claims.json").read_bytes().startswith(span)


# --- rule: without [families] the built-ins in effect are preselected (fix) --------


def test_setup_positions_without_families_preselects_the_builtins_in_effect(
    full: Full,
) -> None:
    d, profile, _, service = full
    assert "families" not in _prefs(profile)
    registry = load_preferences(profile / "preferences.toml").families
    prefs_bytes = (profile / "preferences.toml").read_bytes()
    _start(d, "/setup positions")
    on = {label for label in _labels(d) if label.startswith("✅")}
    assert on == {"✅ AI", "✅ Backend"}
    d.press("done")
    assert d.step == "summary"
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert load_preferences(profile / "preferences.toml").families == registry
    assert service.loaded_preferences.families == registry
    assert (profile / "preferences.toml").read_bytes() == prefs_bytes


def test_first_setup_with_both_builtins_still_declares_them(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    d.start()

    def both(d: Driver) -> None:
        d.press("t:ai")
        d.press("t:backend")
        d.press("done")

    _answer_everything(d, "en", positions=both)
    d.press("confirm")
    families = _prefs(profile)["families"]
    assert families == {
        "ai": {"languages": ["es", "en"]},
        "backend": {"languages": ["es", "en"]},
    }
