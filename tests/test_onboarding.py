"""Guided setup in Telegram (onboarding-v2 T4, S3/S7): ``/setup`` (``/configurar``).

Driven through the bot's public handlers with the fakes of ``tests/test_bot.py``:
``setup_command`` starts it, ``ob:`` buttons go through ``callback_router``
(full app) or ``setup_onboarding_callback`` (setup mode), typed answers through
``paste_handler`` (full app) or ``setup_text`` (setup mode).
"""

import asyncio
import hashlib
import json
import shutil
import stat
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from telegram.ext import CallbackQueryHandler, MessageHandler

import bot
from cva.config import Settings
from cva.i18n import Lang, t
from cva.onboarding import flow
from cva.onboarding.portals import terms_warning
from cva.profile_identity import load_identity
from cva.profile_prefs import load_preferences
from cva.service import JobService
from cva.setup_mode import missing_profile_files
from tests.portability import has_posix_modes
from tests.test_bot import FakeMessage, FakeQuery, FakeUpdate

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"
OWNER = 5


class Driver:
    """One owner chatting with the bot: buttons of the current step, text."""

    def __init__(self, context: Any, *, full: bool, switch: bool = False) -> None:
        self.context = context
        self.full = full
        # Setup mode: a Confirm that completes the profile switches to the
        # full app (bot._switch_if_complete, which loads JobService). Only
        # the tests of that switch (test_setup_mode.py) let it run.
        self.switch = switch
        self.last = ""  # text of the last question shown
        self.markup: Any = None
        self.replies: list[str] = []

    @property
    def step(self) -> str | None:
        state = self.context.user_data.get(flow.STATE_KEY)
        return None if state is None else str(state["step"])

    def start(self) -> FakeMessage:
        message = FakeMessage("/setup")
        asyncio.run(bot.setup_command(FakeUpdate(message=message), self.context))
        self._seen(message.replies, message.markups)
        return message

    def press(self, action: str, step: str | None = None) -> FakeQuery:
        query = FakeQuery(f"ob:{step or self.step}:{action}")
        update = FakeUpdate(query=query)
        if self.full:
            asyncio.run(bot.callback_router(update, self.context))
        elif self.switch:
            asyncio.run(bot.setup_onboarding_callback(update, self.context))
        else:
            with mock.patch.object(bot, "_switch_if_complete", _no_switch):
                asyncio.run(bot.setup_onboarding_callback(update, self.context))
        assert query.answered
        if query.edited:
            self.last, self.markup = query.edited[-1], query.edited_markups[-1]
        self._seen(query.message.replies, query.message.markups)
        return query

    def text(self, value: str) -> FakeMessage:
        message = FakeMessage(value)
        handler = bot.paste_handler if self.full else bot.setup_text
        asyncio.run(handler(FakeUpdate(message=message), self.context))
        self._seen(message.replies, message.markups)
        return message

    def _seen(self, replies: list[str], markups: list[Any]) -> None:
        self.replies.extend(replies)
        if replies:
            self.last, self.markup = replies[-1], markups[-1]

    def buttons(self) -> list[tuple[str, str]]:
        return [
            (b.text, b.callback_data)
            for row in (self.markup.inline_keyboard if self.markup else [])
            for b in row
        ]


async def _no_switch(*args: Any, **kwargs: Any) -> bool:
    return False


def _context(bot_data: dict[str, Any]) -> Any:
    return SimpleNamespace(bot_data=bot_data, user_data={}, args=[])


def _setup_mode(tmp_path: Path, lang: Lang = "en") -> tuple[Driver, Path, Path]:
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    config = tmp_path / "config.local.toml"
    context = _context(
        {
            bot._OWNER_KEY: OWNER,
            bot._LANG_KEY: lang,
            bot._PROFILE_DIR_KEY: profile,
            bot._CONFIG_PATH_KEY: config,
        }
    )
    return Driver(context, full=False), profile, config


def _full_app(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    lang: Lang,
) -> tuple[Driver, Path, Path, JobService]:
    profile = tmp_path / "me"
    shutil.copytree(DEMO_PROFILE, profile)
    monkeypatch.chdir(tmp_path)
    settings = clean_settings(profile_dir=str(profile), ui_language=lang)
    service = JobService.from_settings(settings, db_path=tmp_path / "app.db")
    config = tmp_path / "config.local.toml"
    context = _context(
        {
            bot._SERVICE_KEY: service,
            bot._OWNER_KEY: OWNER,
            bot._LANG_KEY: lang,
            bot._PROFILE_DIR_KEY: profile,
            bot._CONFIG_PATH_KEY: config,
        }
    )
    return Driver(context, full=True), profile, config, service


def _default_positions(d: Driver) -> None:
    # Exactly AI ticked, whatever was prefilled (a profile without
    # [families] preselects the built-ins in effect).
    for fid in ("ai", "backend"):
        chosen = d.context.user_data[flow.STATE_KEY]["a"].get("families") or []
        if (fid == "ai") != (fid in chosen):
            d.press(f"t:{fid}")
    d.press("other")
    assert d.step == "custom_titles"
    d.text("Data Analyst, BI Developer")
    assert d.step == "families"
    d.press("done")


def tick_exactly(d: Driver, key: str, wanted: tuple[str, ...]) -> None:
    """Toggle the buttons of the current multi-choice step until exactly
    ``wanted`` is ticked, whatever was prefilled (T9)."""
    for code in flow.A.OFFER_LANGUAGES:
        chosen = d.context.user_data[flow.STATE_KEY]["a"].get(key) or []
        if (code in wanted) != (code in chosen):
            d.press(f"t:{code}")


def answer_background(
    d: Driver,
    skills: str = "Python, SQL",
    education: str | None = "BSc Computer Science",
) -> None:
    """The T8 steps after the level: spoken languages and their levels,
    skills, experience (years, last role, company) and education."""
    assert d.step == "spoken_langs"
    tick_exactly(d, "spoken_langs", ("es", "en"))
    d.press("done")
    d.press("go:native")
    d.press("go:C1")
    assert d.step == "skills"
    d.text(skills)
    d.press("go:3-5")
    d.text("Backend Developer")
    d.text("Acme")
    if education is None:
        d.press("skip")
    else:
        d.text(education)
    assert d.step == "work_modes"


def _answer_everything(
    d: Driver, lang: Lang, positions: Callable[[Driver], None] = _default_positions
) -> None:
    """Every step of the questionnaire, with the bot in ``lang`` from step 2."""
    d.press(f"go:{lang}")
    assert t("onboarding.q.name", lang) in d.last
    d.text("José Pérez")
    d.text("not-an-email")
    assert t("onboarding.err.email", lang) in d.last and d.step == "email"
    d.text("jose@example.com")
    d.press("skip")  # phone is optional
    d.text("Valencia")
    d.text("linkedin.com/in/jose")
    assert d.step == "families"
    positions(d)
    d.press("go:mid")
    answer_background(d)
    d.press("t:remote")
    d.press("t:hybrid")
    d.press("done")
    assert t("onboarding.btn.keep", lang, value="Valencia") in [
        b for b, _ in d.buttons()
    ]
    d.press("keep")
    d.text("España")
    d.text("30.000")
    d.press("go:EUR")
    assert d.step == "offer_langs"
    # Prefilled (the UI language, or the profile's allowed languages on a
    # re-run, T9): tick exactly Spanish and English.
    tick_exactly(d, "offer_langs", ("es", "en"))
    d.press("done")
    d.press("t:linkedin")
    assert terms_warning("linkedin", lang) in d.last
    d.press("yes")
    d.press("t:infojobs")
    d.press("yes")
    d.press("done")
    if d.step == "cv_pdfs":  # step 9 (T5): no base CV here; an unreadable
        d.press("skip_all")  # preferences.toml has no slots to ask for
    assert d.step == "summary"
    # Edit one answer from the summary: back to the summary right after it.
    d.press("edit")
    d.press("go:level")
    d.press("go:senior")
    assert d.step == "summary"


# --- start, buttons, navigation --------------------------------------------------


def test_setup_asks_the_language_first_with_buttons_and_cancel(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    assert t("onboarding.q.lang", "en") in d.last
    assert d.buttons() == [
        ("English", "ob:lang:go:en"),
        ("Español", "ob:lang:go:es"),
        (t("onboarding.btn.cancel", "en"), "ob:lang:cancel"),
    ]


def test_every_later_step_has_back_and_cancel(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    labels = [label for label, _ in d.buttons()]
    assert labels[-2:] == [
        t("onboarding.btn.back", "en"),
        t("onboarding.btn.cancel", "en"),
    ]


def test_setup_without_a_profile_dir_refuses(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.context.bot_data[bot._PROFILE_DIR_KEY] = None
    d.start()
    assert d.last == t("onboarding.no_profile_dir", "en")
    assert d.step is None


def test_back_returns_to_the_previous_question(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    d.text("Ana")
    assert d.step == "email"
    d.press("back")
    assert d.step == "name" and t("onboarding.q.name", "en") in d.last


def test_cancel_drops_the_setup_and_writes_nothing(tmp_path: Path) -> None:
    d, profile, config = _setup_mode(tmp_path)
    d.start()
    d.press("go:es")
    d.text("Ana")
    d.press("cancel")
    assert d.last == t("onboarding.cancelled", "es")
    assert d.step is None
    assert list(profile.iterdir()) == [] and not config.exists()


def test_a_button_of_an_earlier_question_gets_the_current_one(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    d.press("go:es", step="lang")  # the old language buttons, pressed again
    assert d.replies[-2] == t("onboarding.stale", "en")
    assert d.step == "name" and t("onboarding.q.name", "en") in d.last


def test_expired_setup_no_longer_captures_text_or_buttons(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    d.context.user_data[flow.STATE_AT_KEY] = time.time() - flow.TTL_SECONDS - 1
    d.text("Ana")
    assert d.last == t("bot.setup.not_ready", "en")  # not taken as the name
    d.press("skip", step="name")
    assert d.last == t("onboarding.expired", "en")


def test_invalid_answers_repeat_the_question_with_the_reason(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    d.text("Ana")
    d.text("ana@example.com")
    d.text("call me")
    assert d.step == "phone" and t("onboarding.err.phone", "en") in d.last
    d.text("+34 600 000 000")
    d.text("Madrid")
    d.text("not a url")
    assert d.step == "linkedin" and t("onboarding.err.linkedin", "en") in d.last


def test_a_multi_select_needs_one_choice(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    for answer in ("Ana", "ana@example.com"):
        d.text(answer)
    d.press("skip")
    d.text("Madrid")
    d.text("https://linkedin.com/in/ana")
    d.press("done")
    assert d.step == "families" and t("onboarding.err.pick_one", "en") in d.last


def test_a_portal_is_enabled_only_after_its_terms_warning(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.context.user_data[flow.STATE_KEY]["step"] = "portals"
    d.press("t:indeed")
    assert d.last.endswith(terms_warning("indeed", "en"))
    assert "Indeed" in d.last and "terms of use" in d.last  # the extension's text
    d.press("no")
    assert d.context.user_data[flow.STATE_KEY]["a"].get("portals", []) == []
    d.press("t:indeed")
    d.press("yes")
    assert d.context.user_data[flow.STATE_KEY]["a"]["portals"] == ["indeed"]


def test_spanish_terms_warning_is_the_extension_text() -> None:
    catalogue = json.loads(
        (REPO_ROOT / "extension/_locales/es/messages.json").read_text("utf-8")
    )
    assert terms_warning("linkedin", "es") == catalogue["termsLinkedin"]["message"]


# --- priority over the other text captures ----------------------------------------


def test_a_text_step_wins_over_paste_note_and_star(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, _, service = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    d.start()
    d.press("go:en")
    ingested: list[str] = []
    monkeypatch.setattr(service, "ingest", lambda *a: ingested.append("x"))
    d.context.user_data[bot._PASTE_KEY] = True
    d.context.user_data[bot._NOTE_KEY] = 1
    d.context.user_data[bot._NOTE_AT_KEY] = time.time()
    d.text("Ana")
    assert d.step == "email" and ingested == []
    assert d.context.user_data[flow.STATE_KEY]["a"]["name"] == "Ana"
    assert d.context.user_data[bot._NOTE_KEY] == 1  # untouched, not consumed


def test_a_button_step_leaves_text_to_the_paste_capture(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, _, service = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    d.start()  # language step: buttons only
    seen: list[str] = []

    async def ingest(url: object, text: str) -> int:
        seen.append(text)
        raise ValueError("stop here")

    monkeypatch.setattr(service, "ingest", ingest)
    d.context.user_data[bot._PASTE_KEY] = True
    d.text("an offer text")
    assert seen == ["an offer text"]


def test_setup_drops_a_pending_paste_note_and_star(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    d.context.user_data.update(
        {
            bot._PASTE_KEY: True,
            bot._NOTE_KEY: 1,
            bot._NOTE_AT_KEY: time.time(),
            bot._STAR_KEY: {"id": 1, "queue": ["s"], "total": 1},
            bot._STAR_AT_KEY: time.time(),
        }
    )
    d.start()
    assert d.context.user_data[bot._PASTE_KEY] is False
    assert bot._NOTE_KEY not in d.context.user_data
    assert bot._STAR_KEY not in d.context.user_data


# --- the whole questionnaire ---------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_whole_questionnaire_writes_loadable_files_and_reloads_the_service(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    lang: Lang,
) -> None:
    d, profile, config, service = _full_app(tmp_path, clean_settings, monkeypatch, lang)
    d.start()
    _answer_everything(d, lang)
    assert t("onboarding.edit.level", lang) in d.last  # the summary
    d.press("confirm")
    assert d.step is None
    done = d.replies[-1]
    assert done.startswith(t("onboarding.saved", lang))
    assert t("onboarding.portals.enabled", lang, portals="LinkedIn, InfoJobs") in done
    assert tomllib.loads(config.read_text("utf-8"))["enabled_portals"] == [
        "linkedin",
        "infojobs",
    ]

    # The strict loaders accept what was written.
    prefs = load_preferences(profile / "preferences.toml")
    assert prefs.search.work_modes == ("remote", "hybrid")
    assert prefs.search.level == "senior"
    assert prefs.search.min_salary == 30000 and prefs.search.currency == "EUR"
    assert (prefs.location.city, prefs.location.country) == ("Valencia", "España")
    assert prefs.gates.allowed_languages == ("es", "en")
    assert prefs.families.ids == ("ai", "data_analyst")
    custom = prefs.families.get("data_analyst")
    assert custom is not None and custom.title_terms == ("Data Analyst", "BI Developer")
    assert custom.search_roles == ("Data Analyst", "BI Developer")
    assert custom.languages == ("es", "en")
    identity = load_identity(profile / "profile.toml")
    assert identity.name == "José Pérez" and identity.file_slug == "Jose_Perez"
    assert identity.identity_claim_id == "identity-contact"  # kept
    raw = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    assert raw["stack_wall"] == {"core_language": "Python"}  # other keys kept
    assert "email" not in json.dumps(raw)  # contact data stays out of prefs

    # Contact data waits for the claims step, private to the owner.
    answers_file = profile / "onboarding_answers.json"
    record = json.loads(answers_file.read_text("utf-8"))
    assert record["contact"] == {
        "name": "José Pérez",
        "email": "jose@example.com",
        "phone": None,
        "city": "Valencia",
        "linkedin": "https://linkedin.com/in/jose",
    }
    assert record["portals"] == ["linkedin", "infojobs"]
    if has_posix_modes():
        assert stat.S_IMODE(answers_file.stat().st_mode) == 0o600

    # The running service picked the new preferences up.
    assert service.preferences.search.level == "senior"
    assert service.loaded_preferences.families.ids == ("ai", "data_analyst")
    # Same language as the bot: no ui_language saved (only the portals).
    assert "ui_language" not in tomllib.loads(config.read_text("utf-8"))


_FRONTEND = """
[families.frontend]
label = "Frontend Dev"
languages = ["es", "en"]
title_terms = ["Frontend", "React"]
threshold = 70
search_roles = ["Frontend Developer"]
cover_letter_emphasis = "UI work"

[families.frontend.title_filter]
include_groups = [["Frontend", "React"], ["Developer", "Engineer"]]
exclude_terms = ["Senior"]
"""


def _with_frontend(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Driver, Path, dict[str, Any]]:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    prefs = profile / "preferences.toml"
    prefs.write_text(prefs.read_text("utf-8") + _FRONTEND, encoding="utf-8")
    before = tomllib.loads(prefs.read_text("utf-8"))["families"]["frontend"]
    assert len(before) == 7
    return d, profile, before


def test_setup_keeps_a_hand_declared_family_with_every_key(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, before = _with_frontend(tmp_path, clean_settings, monkeypatch)
    d.start()

    def keep_defaults(d: Driver) -> None:
        assert ("✅ Frontend Dev", "ob:families:k:frontend") in d.buttons()
        d.press("done")

    _answer_everything(d, "en", keep_defaults)
    assert "Frontend Dev" in d.last  # the summary lists it
    d.press("confirm")
    raw = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    assert raw["families"] == {"frontend": before}
    assert load_preferences(profile / "preferences.toml").families.ids == ("frontend",)


def test_setup_removes_a_declared_family_the_user_deselects(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _ = _with_frontend(tmp_path, clean_settings, monkeypatch)
    d.start()

    def drop_frontend(d: Driver) -> None:
        d.press("k:frontend")
        assert ("▫️ Frontend Dev", "ob:families:k:frontend") in d.buttons()
        d.press("t:ai")
        d.press("done")

    _answer_everything(d, "en", drop_frontend)
    d.press("confirm")
    raw = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    assert list(raw["families"]) == ["ai"]


@pytest.mark.parametrize("bad", ["Ana\x00", "An\x1ba", "Ana\x7f"])
def test_free_text_answers_refuse_control_characters(tmp_path: Path, bad: str) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    d.press("go:en")
    d.text(bad)
    assert d.step == "name" and t("onboarding.err.name", "en") in d.last
    d.text("Ana")
    d.text("ana@example.com")
    d.press("skip")
    d.text(bad)
    assert d.step == "city"
    d.text("Madrid")
    d.text("linkedin.com/in/ana\x00")
    assert d.step == "linkedin"
    d.text("linkedin.com/in/ana")
    d.press("other")
    d.text(f"Data, {bad}")
    assert d.step == "custom_titles"


def test_choosing_another_language_switches_the_bot_and_saves_it(
    tmp_path: Path,
) -> None:
    d, profile, config = _setup_mode(tmp_path, lang="en")
    config.write_text('# mine\nllm_provider = "claude_code"\n', encoding="utf-8")
    d.start()
    _answer_everything(d, "es")
    d.press("confirm")
    assert d.context.bot_data[bot._LANG_KEY] == "es"
    saved = tomllib.loads(config.read_text("utf-8"))
    assert saved == {
        "llm_provider": "claude_code",
        "enabled_portals": ["linkedin", "infojobs"],  # step 12, activated
        "ui_language": "es",
    }
    assert config.read_text("utf-8").startswith("# mine\n")  # wizard writer
    assert t("onboarding.saved_language", "es") in d.replies[-1]
    # T7: Confirm also builds the minimal profile, so setup mode is over.
    assert (profile / "claims.json").is_file()
    assert missing_profile_files(profile) == ()
    assert load_preferences(profile / "preferences.toml").search.level == "senior"


def test_setup_mode_language_switch_reaches_the_full_app_settings(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    app = bot.build_setup_app(
        clean_settings(
            profile_dir=str(profile), owner_telegram_id=OWNER, ui_language="en"
        )
    )
    app.bot_data[bot._CONFIG_PATH_KEY] = tmp_path / "config.local.toml"
    d = Driver(_context(app.bot_data), full=False)
    d.start()
    _answer_everything(d, "es")
    d.press("confirm")
    assert app.bot_data[bot._SETUP_SETTINGS_KEY].ui_language == "es"


def test_a_write_error_keeps_the_summary_and_names_no_absolute_path(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    (profile / "preferences.toml").write_text("[broken\n", encoding="utf-8")
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.write_failed", "en", error="")[:20])
    assert str(tmp_path) not in d.replies[-1]
    assert d.step == "summary"
    assert not (profile / "profile.toml").exists()  # nothing half-written


# --- wiring ------------------------------------------------------------------------


def test_setup_app_routes_answers_before_the_not_ready_fallback(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    app = bot.build_setup_app(
        clean_settings(profile_dir=str(profile), owner_telegram_id=OWNER)
    )
    callbacks = [h.callback for h in app.handlers[1]]
    fallback = callbacks.index(bot.setup_not_ready)
    assert callbacks.index(bot.setup_onboarding_callback) < fallback
    assert callbacks.index(bot.setup_text) < fallback
    ob = next(h for h in app.handlers[1] if h.callback is bot.setup_onboarding_callback)
    assert isinstance(ob, CallbackQueryHandler) and ob.pattern is not None
    assert ob.pattern.match("ob:lang:go:en") and not ob.pattern.match("cv:1")
    text = next(h for h in app.handlers[1] if h.callback is bot.setup_text)
    assert isinstance(text, MessageHandler)
    assert any(h.callback is bot.gate for h in app.handlers[-1])  # owner gate kept


def test_full_app_routes_onboarding_buttons_through_the_table() -> None:
    assert bot.CALLBACK_HANDLERS[flow.PREFIX] is bot._cb_onboarding


# --- T8 (S9): spoken languages, skills, experience, education -----------------------


def _to_spoken_langs(d: Driver) -> None:
    d.start()
    d.press("go:en")
    d.text("Ana")
    d.text("ana@example.com")
    d.press("skip")
    d.text("Madrid")
    d.text("linkedin.com/in/ana")
    d.press("t:ai")
    d.press("done")
    d.press("go:junior")
    assert d.step == "spoken_langs"


def _state_answers(d: Driver) -> dict[str, Any]:
    answers: dict[str, Any] = d.context.user_data[flow.STATE_KEY]["a"]
    return answers


def test_spoken_languages_are_common_ones_or_typed_then_a_level_each(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_spoken_langs(d)
    data = [cb for _, cb in d.buttons()]
    for code in ("es", "en", "ca", "fr", "de", "it", "pt"):
        assert f"ob:spoken_langs:t:{code}" in data
    assert "ob:spoken_langs:other" in data
    d.press("done")
    assert d.step == "spoken_langs" and t("onboarding.err.pick_one", "en") in d.last
    d.press("t:es")
    d.press("other")
    assert d.step == "spoken_other"
    d.text("Basque,  english")  # a common language typed maps to its button
    assert d.step == "spoken_langs"
    assert ("✅ " + t("onboarding.olang.en", "en"), "ob:spoken_langs:t:en") in (
        d.buttons()
    )
    d.press("done")
    assert d.step == "spoken_levels"
    assert [cb for _, cb in d.buttons()][:6] == [
        f"ob:spoken_levels:go:{v}" for v in ("native", "C2", "C1", "B2", "B1", "A2")
    ]
    assert [label for label, _ in d.buttons()][:6] == [
        "Native",
        "C2",
        "C1",
        "B2",
        "B1",
        "A2",
    ]
    assert "Spanish" in d.last
    d.press("go:native")
    assert d.step == "spoken_levels" and "English" in d.last
    d.press("back")
    assert d.step == "spoken_langs"
    d.press("done")  # every level is asked again
    for level in ("native", "C1", "B1"):
        d.press(f"go:{level}")
    assert d.step == "skills"
    answers = _state_answers(d)
    assert answers["spoken_langs"] == ["es", "en"]
    assert answers["spoken_other"] == ["Basque"]
    assert answers["spoken_levels"] == {"es": "native", "en": "C1", "Basque": "B1"}


def test_skills_are_comma_separated_normalized_and_capped(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_spoken_langs(d)
    d.press("t:es")
    d.press("done")
    d.press("go:native")
    assert d.step == "skills"
    for bad in (
        "Python, SQL\x00",
        ", ".join(f"skill{i}" for i in range(21)),  # more than 20
        "x" * 51,  # one longer than 50 characters
        " , ;",
    ):
        d.text(bad)
        assert d.step == "skills" and t("onboarding.err.skills", "en") in d.last
    d.text("  Python ,  SQL   Server,python;\nDocker ")
    assert d.step == "exp_years"
    assert _state_answers(d)["skills"] == ["Python", "SQL Server", "Docker"]


def test_experience_is_years_buttons_then_optional_role_and_company(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_spoken_langs(d)
    d.press("t:es")
    d.press("done")
    d.press("go:native")
    d.text("Python")
    assert d.step == "exp_years"
    assert [b for b in d.buttons()][:6] == [
        ("0", "ob:exp_years:go:0"),
        ("<1", "ob:exp_years:go:lt1"),
        ("1-2", "ob:exp_years:go:1-2"),
        ("3-5", "ob:exp_years:go:3-5"),
        ("6-10", "ob:exp_years:go:6-10"),
        ("10+", "ob:exp_years:go:10plus"),
    ]
    d.press("go:lt1")
    assert d.step == "last_role"
    d.press("skip")  # no role: the company is not asked
    assert d.step == "education"
    d.press("back")
    d.text("Data Intern\x1b")
    assert d.step == "last_role" and t("onboarding.err.text", "en") in d.last
    d.text("Data Intern")
    assert d.step == "last_company"
    d.press("skip")
    assert d.step == "education"
    d.press("skip")  # education is optional
    assert d.step == "work_modes"
    answers = _state_answers(d)
    assert (answers["exp_years"], answers["last_role"]) == ("lt1", "Data Intern")
    assert answers["last_company"] is None and answers["education"] is None


def test_summary_lists_the_background_answers(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    d.start()
    _answer_everything(d, "en")
    for line in (
        "Spoken languages: Spanish (Native), English (C1)",
        "Skills: Python, SQL",
        "Experience: 3-5",
        "Last role: Backend Developer · Acme",
        "Education: BSc Computer Science",
    ):
        assert line in d.last


def _claims_text(profile: Path) -> str:
    return (profile / "claims.json").read_text("utf-8")


def _hand_written_prefix(text: str) -> str:
    """The claims array without its closing bracket: every hand-written claim."""
    return text.rstrip()[:-1].rstrip()


def test_confirm_adds_setup_claims_and_keeps_hand_written_ones_byte_identical(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    original = _claims_text(profile)
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))

    text = _claims_text(profile)
    assert text.startswith(_hand_written_prefix(original))
    claims = json.loads(text)
    assert [c for c in claims if not c["id"].startswith("setup-")] == json.loads(
        original
    )
    assert [c["id"] for c in claims if c["id"].startswith("setup-")] == [
        "setup-lang-es",
        "setup-lang-en",
        "setup-skill-python",
        "setup-skill-sql",
        "setup-experience",
        "setup-education",
    ]
    record = json.loads((profile / "onboarding_answers.json").read_text("utf-8"))
    assert record["schema"] == 2
    assert record["background"] == {
        "spoken_languages": [
            {"language": "es", "level": "native"},
            {"language": "en", "level": "C1"},
        ],
        "skills": ["Python", "SQL"],
        "experience": {"years": "3-5", "role": "Backend Developer", "company": "Acme"},
        "education": "BSc Computer Science",
    }


def test_rerun_replaces_only_the_setup_claims(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    original = _claims_text(profile)
    digest = hashlib.sha256(_hand_written_prefix(original).encode()).hexdigest()
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")

    def rerun(d: Driver) -> None:
        d.press("done")  # the families the profile declares stay
        d.press("go:mid")
        answer_background(d, skills="Go, Rust", education=None)

    d.start()
    d.press("go:en")
    d.text("José Pérez")
    d.text("jose@example.com")
    d.press("skip")
    d.text("Valencia")
    d.text("linkedin.com/in/jose")
    rerun(d)
    d.press("t:remote")
    d.press("done")
    d.press("keep")
    d.text("España")
    d.press("skip")
    d.press("done")
    d.press("done")
    if d.step == "cv_pdfs":
        d.press("skip_all")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))

    text = _claims_text(profile)
    prefix = _hand_written_prefix(original)
    assert hashlib.sha256(text[: len(prefix)].encode()).hexdigest() == digest
    ids = [c["id"] for c in json.loads(text) if c["id"].startswith("setup-")]
    assert ids == [
        "setup-lang-es",
        "setup-lang-en",
        "setup-skill-go",
        "setup-skill-rust",
        "setup-experience",
    ]


def test_a_failed_confirm_restores_the_merged_claims(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    before = (profile / "claims.json").read_bytes()

    def broken_hook(profile_dir: Path, answers: dict[str, object]) -> None:
        raise flow.ProfileError("hook failed")

    monkeypatch.setattr(flow, "CONFIRM_HOOKS", [flow.confirm_hook, broken_hook])
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    assert d.step == "summary" and "hook failed" in d.replies[-1]
    assert (profile / "claims.json").read_bytes() == before
