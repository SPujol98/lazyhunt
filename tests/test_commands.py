"""Command table: one source for /help and the Telegram command menu."""

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from telegram.ext import CommandHandler

import bot
from cva.commands import (
    COMMANDS,
    SECTIONS,
    help_text,
    menu_commands,
    section_title,
)
from cva.config import Settings
from cva.fetch import FetchResponse
from cva.i18n import LANGS, Lang
from cva.notifier import help_message
from tests.test_bot import FakeFetcher, FakeUpdate, make_service

_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _handler_names(settings: Settings, tmp_path: Path) -> set[str]:
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    app = bot.build_app(settings, service)
    names: set[str] = set()
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                names |= set(handler.commands)
    return names


def test_every_registered_command_is_in_table_and_help(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    registered = _handler_names(clean_settings(), tmp_path)
    known = {n for c in COMMANDS for n in c.names} | {
        a for c in COMMANDS for a in c.aliases
    }
    assert registered, "no CommandHandler found"
    assert registered <= known, f"undocumented: {registered - known}"
    documented = {lang: help_text(lang=lang) for lang in LANGS}
    # Unlisted commands (/setup until onboarding-v2 T4) stay out of /help.
    unlisted = {n for c in COMMANDS if not c.listed for n in c.names}
    for name in registered - unlisted:
        assert any(f"/{name}" in text for text in documented.values()), name


def test_table_has_no_phantom_commands(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    registered = _handler_names(clean_settings(), tmp_path)
    assert {c.name for c in COMMANDS} <= registered


def test_every_table_command_has_exactly_one_bot_handler() -> None:
    assert set(bot._COMMAND_HANDLERS) == {c.name for c in COMMANDS}


@pytest.mark.parametrize("lang", LANGS)
def test_menu_respects_telegram_limits(lang: Lang) -> None:
    menu = menu_commands(lang)
    assert 1 <= len(menu) <= 100
    names = [name for name, _ in menu]
    assert len(names) == len(set(names))
    for name, description in menu:
        assert _NAME_RE.match(name), name
        assert 1 <= len(description) <= 256, name


@pytest.mark.parametrize("lang", LANGS)
def test_every_command_has_section_explanation_and_example(lang: Lang) -> None:
    from cva.commands import commands_for

    for command in commands_for(lang=lang):
        assert command.section in SECTIONS
        assert command.explanation.strip()
        assert command.example.startswith(f"/{command.name}")


@pytest.mark.parametrize("lang", LANGS)
def test_help_is_grouped_and_fits_one_message(lang: Lang) -> None:
    text = help_text(lang=lang)
    assert len(text) < 4096
    for section in SECTIONS:
        assert section_title(section, lang) in text
    assert "/panel" in text


def test_help_documents_every_card_button() -> None:
    text = help_message()
    assert section_title("buttons", "es") in text
    for label in (
        "Analizar",
        "Generar CV",
        "Idioma",
        "Variante",
        "Investigar salario",
        "Carta de presentación",
        "Descartar",
        "Marcar enviada",
        "Rehacer CV",
        "Usar esta",
        "Reactivar",
        "Desactivar",
        "Resultado",
    ):
        assert label in text, label


def test_buttons_in_help_match_keyboards() -> None:
    """Every button label built by the notifier keyboards is named in /help."""
    from cva.calibration import CalibrationExample
    from cva.calibration_ui import list_keyboard, question_keyboard, reason_keyboard
    from cva.notifier import (
        beacon_alert_keyboard,
        cover_letter_keyboard,
        cv_keyboard,
        followup_keyboard,
        new_job_keyboard,
        outcome_keyboard,
        sent_keyboard,
        sent_outcome_keyboard,
    )

    def _example() -> CalibrationExample:
        return CalibrationExample(
            id=1,
            offer_id=1,
            outcome="screening",
            polarity="positive",
            score_at_send=50,
            status="active",
            created_at="2026-11-02",
        )

    text = help_message()
    keyboards = [
        cv_keyboard(1),
        sent_keyboard(1, cv_base="AI_ES"),
        cover_letter_keyboard(1, "a", 1),
        new_job_keyboard("job-1"),
        outcome_keyboard(1),
        sent_outcome_keyboard(1),
        followup_keyboard(1),
        beacon_alert_keyboard(1),
        question_keyboard(1),
        reason_keyboard(1),
        list_keyboard([_example()]),
    ]
    for keyboard in keyboards:
        for row in keyboard.inline_keyboard:
            for button in row:
                core = re.sub(r"^[^\w]+", "", button.text).split(":")[0].split(" (")[0]
                assert core in text, button.text


def test_start_help_replies_with_table_text() -> None:
    from tests.test_bot import FakeMessage

    message = FakeMessage()
    asyncio.run(
        bot.start_help(FakeUpdate(message=message), None)  # type: ignore[arg-type]
    )
    assert message.replies == [help_message()]


class FakeMenuBot:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[tuple[str, str]]] = []
        self.fail = fail

    async def set_my_commands(self, commands: list[object]) -> None:
        if self.fail:
            raise RuntimeError("telegram down")
        self.calls.append([(c.command, c.description) for c in commands])  # type: ignore[attr-defined]


class FakeApp:
    def __init__(self, tg_bot: FakeMenuBot) -> None:
        self.bot = tg_bot
        self.post_init = None


def test_register_menu_sets_commands_and_chains_previous_hook() -> None:
    order: list[str] = []

    async def previous(app: object) -> None:
        order.append("previous")

    app = FakeApp(FakeMenuBot())
    app.post_init = previous  # type: ignore[assignment]
    bot.register_menu(app, "es")  # type: ignore[arg-type]
    asyncio.run(app.post_init(app))  # type: ignore[misc]
    assert app.bot.calls == [list(menu_commands("es"))]
    assert order == ["previous"]


def test_register_menu_failure_never_raises() -> None:
    app = FakeApp(FakeMenuBot(fail=True))
    bot.register_menu(app, "es")  # type: ignore[arg-type]
    asyncio.run(app.post_init(app))  # type: ignore[misc]


def test_register_menu_failure_still_runs_previous_hook() -> None:
    order: list[str] = []

    async def previous(app: object) -> None:
        order.append("previous")

    app = FakeApp(FakeMenuBot(fail=True))
    app.post_init = previous  # type: ignore[assignment]
    bot.register_menu(app, "es")  # type: ignore[arg-type]
    asyncio.run(app.post_init(app))  # type: ignore[misc]
    assert order == ["previous"]


@pytest.mark.parametrize("name", [n for c in COMMANDS for n in (*c.names, *c.aliases)])
def test_names_are_valid(name: str) -> None:
    assert _NAME_RE.match(name)


def test_pause_commands_are_in_the_menu_and_help() -> None:
    names = {name for name, _ in menu_commands()}
    assert {"pausa", "reanudar"} <= names
    text = help_message()
    assert "/pausa" in text and "/reanudar" in text


# --- onboarding-v2 T4: /setup (/configurar) is listed again in the full app ----
# T1 hid it while it only answered "coming soon"; with the guided setup it is
# advertised in /help and the menu (and /help still fits one message).


@pytest.mark.parametrize("lang", LANGS)
def test_full_help_and_menu_advertise_setup(lang: Lang) -> None:
    name = "setup" if lang == "en" else "configurar"
    text = help_text(lang=lang)
    assert f"/{name}" in text
    assert name in {n for n, _ in menu_commands(lang)}
    assert len(text) < 4096


@pytest.mark.parametrize("lang", LANGS)
def test_setup_menu_still_lists_setup(lang: Lang) -> None:
    from cva.commands import SETUP_COMMANDS

    names = {name for name, _ in menu_commands(lang, only=SETUP_COMMANDS)}
    # beta3-ux T1: /connect pairs the extension from setup mode too.
    assert names == {
        "setup" if lang == "en" else "configurar",
        "connect" if lang == "en" else "conectar",
        "help",
    }


def test_connect_stays_out_of_the_full_help_and_menu() -> None:
    # The full /help is close to Telegram's limit: /connect is offered by the
    # setup-mode menu and /help, the /setup Confirm reply and the docs.
    for lang in LANGS:
        assert "/connect" not in help_text(lang=lang)
        assert "/conectar" not in help_text(lang=lang)
        names = {n for n, _ in menu_commands(lang)}
        assert "connect" not in names and "conectar" not in names


# --- onboarding-v2 T9: /setup [section] re-runs one section --------------------


@pytest.mark.parametrize("lang", LANGS)
def test_help_advertises_setup_with_an_optional_section(lang: Lang) -> None:
    name = "setup" if lang == "en" else "configurar"
    section = "salary" if lang == "en" else "salario"
    text = help_text(lang=lang)
    assert f"/{name} [" in text
    assert f"/{name} {section}" in text
    assert len(text) < 4096
