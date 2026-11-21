"""Public-beta polish of the guided setup (onboarding-v2, verifier UX batch).

Each test drives the bot through its handlers, as the owner would: a stable
step counter, a shorter Confirm reply, singular/plural pages, lowercase
language names mid-sentence in Spanish, experience in years, a plain
``/start`` in setup mode and ``/setup`` hidden on a legacy install.
"""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bot
from cva.commands import menu_commands
from cva.config import Settings
from cva.i18n import Lang, t
from tests.test_bot import FakeDocument, FakeMessage, FakeUpdate
from tests.test_onboarding import Driver, _answer_everything, _setup_mode
from tests.test_onboarding_cv_upload import _pdf, _send, _to_cv_step
from tests.test_onboarding_sections import _set_up, _start

_PROGRESS = re.compile(r"^(?:Setup|Configuración) (\d+)/(\d+)$", re.MULTILINE)


class _Recorder(Driver):
    """A :class:`Driver` that keeps every question it was shown."""

    def __init__(self, base: Driver) -> None:
        super().__init__(base.context, full=base.full)
        self.shown: list[str] = []

    def press(self, action: str, step: str | None = None) -> Any:
        query = super().press(action, step)
        self.shown.append(self.last)
        return query

    def text(self, value: str) -> FakeMessage:
        message = super().text(value)
        self.shown.append(self.last)
        return message


# --- 1: the step counter's total does not grow during the run --------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_step_total_is_the_same_on_every_question(
    tmp_path: Path, lang: Lang
) -> None:
    base, _, _ = _setup_mode(tmp_path, lang)
    d = _Recorder(base)
    d.start()
    _answer_everything(d, lang)
    counters = [_PROGRESS.findall(text) for text in d.shown]
    pairs = [(int(n), int(total)) for found in counters for n, total in found]
    assert {total for _, total in pairs} == {20}
    # A follow-up question (the level of each language) keeps the number of
    # the question it follows, so the count never jumps back.
    numbers = [n for n, _ in pairs]
    first_edit = numbers.index(max(numbers)) + 1  # the summary's Edit after it
    assert numbers[:first_edit] == sorted(numbers[:first_edit])


def test_a_section_counts_only_its_own_questions(tmp_path: Path) -> None:
    base, _ = _set_up(tmp_path)
    d = _Recorder(base)
    _start(d, "/setup background")
    d.shown.append(d.last)
    for _ in range(12):
        if d.step == "summary":
            break
        actions = [a.split(":", 2)[2] for _, a in d.buttons()]
        ticked = [a for label, a in d.buttons() if label.startswith("✅")]
        if d.step == "skills":
            d.text("Python")
        elif ticked and ticked[0].startswith("ob:") and "go:" in ticked[0]:
            d.press(ticked[0].split(":", 2)[2])
        else:
            d.press(next(a for a in ("keep", "done", "skip") if a in actions))
    totals = {int(total) for text in d.shown for _, total in _PROGRESS.findall(text)}
    assert totals == {5}


# --- 2: a shorter Confirm reply ----------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_confirm_reply_does_not_mention_the_answers_file(
    tmp_path: Path, lang: Lang
) -> None:
    d, _ = _set_up(tmp_path, lang)
    assert "onboarding_answers.json" not in d.replies[-1]


@pytest.mark.parametrize("lang", ["en", "es"])
def test_a_section_rerun_does_not_repeat_the_missing_base_cvs(
    tmp_path: Path, lang: Lang
) -> None:
    d, _ = _set_up(tmp_path, lang)  # every base CV skipped
    missing = t("onboarding.cv.not_deliverable", lang, slots="")[:12]
    assert missing in d.replies[-1]  # the full run says it
    _start(d, "/setup salary" if lang == "en" else "/configurar salario")
    d.press("keep")
    assert d.step == "currency"
    d.press("go:EUR")
    assert d.step == "summary"
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", lang))
    assert missing not in d.replies[-1]


# --- 3: one page, two pages ----------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "pages", "said"),
    [
        ("en", 1, "(1 page)"),
        ("en", 2, "(2 pages)"),
        ("es", 1, "(1 página)"),
        ("es", 2, "(2 páginas)"),
    ],
)
def test_received_pdf_says_page_or_pages(
    tmp_path: Path, lang: Lang, pages: int, said: str
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)
    _to_cv_step(d, lang)
    taken, message = _send(d, FakeDocument(_pdf(pages)))
    assert taken
    assert said in message.replies[0]
    assert "(1 pages)" not in message.replies[0]
    assert "(1 páginas)" not in message.replies[0]


# --- 4: Spanish language names mid-sentence are lowercase --------------------------


def test_spanish_level_question_names_the_language_in_lowercase(
    tmp_path: Path,
) -> None:
    base, _, _ = _setup_mode(tmp_path, "es")
    d = _Recorder(base)
    d.start()
    _answer_everything(d, "es")
    asked = [text for text in d.shown if "¿Tu nivel de " in text]
    assert any("¿Tu nivel de español?" in text for text in asked)
    assert any("¿Tu nivel de inglés?" in text for text in asked)
    assert not any("¿Tu nivel de Español?" in text for text in asked)
    # A list item still starts with a capital (the summary).
    assert "Español (" in d.last


# --- 5: experience in years -------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "bucket", "said"),
    [
        ("en", "3-5", "3-5 years"),
        ("es", "3-5", "3-5 años"),
        ("en", "lt1", "Less than 1 year"),
        ("es", "lt1", "Menos de 1 año"),
        ("en", "0", "0 years"),
        ("es", "0", "0 años"),
        ("en", "1-2", "1-2 years"),
        ("es", "6-10", "6-10 años"),
        ("en", "10plus", "10+ years"),
        ("es", "10plus", "10+ años"),
    ],
)
def test_summary_gives_the_experience_in_years(
    tmp_path: Path, lang: Lang, bucket: str, said: str
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)
    d.start()
    _answer_everything(d, lang)
    d.press("edit")
    d.press("go:exp_years")
    d.press(f"go:{bucket}")
    assert d.step == "summary"
    assert f"{t('onboarding.edit.exp_years', lang)}: {said}\n" in d.last


# --- 6: /start in setup mode -----------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_start_in_setup_mode_says_to_run_setup_without_file_names(
    tmp_path: Path, clean_settings: Callable[..., Settings], lang: Lang
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    app = bot.build_setup_app(
        clean_settings(profile_dir=str(profile), owner_telegram_id=5, ui_language=lang)
    )
    message = FakeMessage("/start")
    context = SimpleNamespace(bot_data=app.bot_data, user_data={}, args=[])
    asyncio.run(bot.setup_start(FakeUpdate(message=message), context))
    (reply,) = message.replies
    assert ("/setup" if lang == "en" else "/configurar") in reply
    assert "claims.json" not in reply and "judge/" not in reply


# --- 7: an identity claim /setup did not create ------------------------------------


# (tests/test_onboarding_sections.py: the demo full re-run says it was kept
# because /setup did not create it.)


# --- 8: the init command ----------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_no_profile_dir_points_to_the_documented_init_command(lang: Lang) -> None:
    text = t("onboarding.no_profile_dir", lang)
    assert "`uv run cli.py init`" in text
    assert "python cli.py" not in text


# --- 10: /setup is hidden on a legacy install --------------------------------------


def _help(profile_dir: Path | None, lang: Lang) -> str:
    message = FakeMessage("/help")
    context = SimpleNamespace(
        bot_data={bot._PROFILE_DIR_KEY: profile_dir, bot._LANG_KEY: lang},
        user_data={},
        args=[],
    )
    asyncio.run(bot.start_help(FakeUpdate(message=message), context))
    (reply,) = message.replies
    return reply


@pytest.mark.parametrize("lang", ["en", "es"])
def test_legacy_install_help_and_menu_hide_setup(tmp_path: Path, lang: Lang) -> None:
    name = "setup" if lang == "en" else "configurar"
    legacy = _help(None, lang)
    assert f"/{name} " not in legacy and f"/{name}\n" not in legacy
    assert len(legacy) < 4096
    shown = _help(tmp_path, lang)
    assert f"/{name} [" in shown and len(shown) < 4096
    hidden = {n for n, _ in menu_commands(lang, hidden=("configurar",))}
    assert name not in hidden and "help" in hidden
    assert name in {n for n, _ in menu_commands(lang)}


def test_legacy_install_publishes_a_menu_without_setup(
    clean_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_commands import FakeMenuBot

    menus: list[Any] = []

    def capture(app: Any, lang: Lang, only: Any = None, hidden: Any = ()) -> None:
        menus.append(hidden)

    # An owner is set: owner 0 now starts the claim app first (beta3-ux T1).
    settings = clean_settings(profile_dir=None, owner_telegram_id=5)  # legacy
    assert settings.profile_dir is None
    monkeypatch.setattr(bot, "Settings", lambda: settings)
    monkeypatch.setattr(bot, "configure_logging", lambda: None)
    monkeypatch.setattr(
        bot.JobService,
        "from_settings",
        lambda s, **k: SimpleNamespace(threshold=65, repository=None),
    )
    full = SimpleNamespace(run_polling=lambda *a, **k: None)
    monkeypatch.setattr(bot, "build_app", lambda s, svc: full)
    monkeypatch.setattr(bot.scheduler, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot.bridge, "register", lambda *a, **k: None)
    real_register_menu = bot.register_menu
    monkeypatch.setattr(bot, "register_menu", capture)
    bot.main()
    assert menus == [("configurar",)]
    menu_bot = FakeMenuBot()
    app = SimpleNamespace(post_init=None)
    real_register_menu(app, "en", hidden=("configurar",))  # type: ignore[arg-type]
    asyncio.run(app.post_init(SimpleNamespace(bot=menu_bot)))
    assert "setup" not in {name for name, _ in menu_bot.calls[0]}
