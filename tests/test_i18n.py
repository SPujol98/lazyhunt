"""bot-i18n T1: catalogs, ``ui_language``, localized /help, menu and command names.

``tests/golden/commands_es.json`` is the HEAD output (before i18n) of
``menu_commands``, ``help_text``, ``buttons_for`` and ``cv_usage``, captured
from a ``git archive`` copy: Spanish must stay byte-identical (S5).
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError
from telegram.ext import CommandHandler

import bot
from cva import i18n
from cva.commands import COMMANDS, buttons_for, cv_usage, help_text, menu_commands
from cva.config import Settings
from cva.families import (
    BUILTIN_AI,
    BUILTIN_BACKEND,
    BUILTIN_REGISTRY,
    Family,
    FamilyRegistry,
)
from cva.fetch import FetchResponse
from cva.i18n import CATALOGS, LANGS, Lang, join_list, placeholders, t
from tests.test_bot import (
    FakeContext,
    FakeFetcher,
    FakeMessage,
    FakeUpdate,
    make_service,
)

GOLDEN = Path(__file__).parent / "golden" / "commands_es.json"
REGISTRIES = {
    "builtin": BUILTIN_REGISTRY,
    "three": FamilyRegistry(
        (
            BUILTIN_AI,
            BUILTIN_BACKEND,
            Family(id="frontend", label="Frontend", languages=("en", "es")),
        ),
        "ai",
    ),
    "single": FamilyRegistry((Family(id="data", label="Data", languages=("en",)),)),
}
SPANISH_ONLY = {"rescatar", "calibracion", "pausa", "reanudar", "cartabase"}
ENGLISH = {"rescue", "calibration", "pause", "resume", "basecover"}


def _render(lang: Lang) -> dict[str, object]:
    out: dict[str, object] = {"menu": [list(p) for p in menu_commands(lang)]}
    for name, registry in REGISTRIES.items():
        out[f"help.{name}"] = help_text(registry, lang=lang)
        out[f"buttons.{name}"] = [list(p) for p in buttons_for(registry, lang=lang)]
        out[f"cv_usage.{name}"] = cv_usage(registry, lang=lang)
    return out


# --- catalogs ----------------------------------------------------------------


def test_every_key_exists_in_both_languages_with_the_same_placeholders() -> None:
    assert set(LANGS) == {"en", "es"}
    assert CATALOGS["en"].keys() == CATALOGS["es"].keys()
    for key, english in CATALOGS["en"].items():
        assert placeholders(english) == placeholders(CATALOGS["es"][key]), key
        assert english.strip() and CATALOGS["es"][key].strip(), key


def test_every_area_module_is_complete_on_its_own() -> None:
    from cva.i18n import bot as bot_catalog
    from cva.i18n import commands, core, notifier, panel, prompts

    for module in (core, commands, notifier, bot_catalog, panel, prompts):
        assert module.EN.keys() == module.ES.keys(), module.__name__
        prefix = module.__name__.rsplit(".", 1)[1] + "."
        assert all(key.startswith(prefix) for key in module.EN), module.__name__


def test_t_formats_and_fails_loudly() -> None:
    assert t("commands.example", "en", example="/cv 1") == "e.g. /cv 1"
    assert t("commands.example", "es", example="/cv 1") == "Ej.: /cv 1"
    with pytest.raises(KeyError, match="no.such"):
        t("no.such", "en")
    with pytest.raises(KeyError):
        t("commands.example", "en")  # missing placeholder value


def test_join_list() -> None:
    assert join_list(["a"], "en") == "a"
    assert join_list(["a", "b", "c"], "en") == "a, b and c"
    assert join_list(["a", "b"], "es") == "a y b"


def test_default_language_is_english() -> None:
    assert i18n.DEFAULT_LANG == "en"


# --- Settings.ui_language ----------------------------------------------------


def _system_locale(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], getlocale: str | None = None
) -> None:
    """Pin the system locale: only ``env`` is set; ``locale.getlocale`` fixed."""
    monkeypatch.delenv("CVA_UI_LANGUAGE", raising=False)
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("locale.getlocale", lambda *a, **k: (getlocale, None))


@pytest.mark.parametrize(
    ("env", "getlocale", "expected"),
    [
        ({"LANG": "es_ES.UTF-8"}, None, "es"),
        ({"LANG": "es"}, None, "es"),
        ({"LANG": "en_US.UTF-8"}, None, "en"),
        ({"LANG": "fr_FR"}, None, "en"),
        ({"LANG": "C"}, None, "en"),
        ({}, None, "en"),
        ({"LC_ALL": "es_ES.UTF-8", "LANG": "en_US.UTF-8"}, None, "es"),
        ({"LC_ALL": "en_US.UTF-8", "LANG": "es_ES.UTF-8"}, None, "en"),
        ({"LC_MESSAGES": "es_MX.UTF-8", "LANG": "en_US.UTF-8"}, None, "es"),
        ({}, "es_ES", "es"),
        ({}, "de_DE", "en"),
    ],
)
def test_ui_language_defaults_to_the_system_locale(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    env: dict[str, str],
    getlocale: str | None,
    expected: str,
) -> None:
    _system_locale(monkeypatch, env, getlocale)
    assert clean_settings().ui_language == expected


def test_explicit_auto_equals_the_default(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    _system_locale(monkeypatch, {"LANG": "es_ES.UTF-8"})
    monkeypatch.setenv("CVA_UI_LANGUAGE", "auto")
    assert clean_settings().ui_language == "es"
    monkeypatch.setenv("CVA_UI_LANGUAGE", "AUTO")
    assert clean_settings().ui_language == "es"


@pytest.mark.parametrize(("system", "forced"), [("es_ES.UTF-8", "en"), ("en_US", "es")])
def test_explicit_language_overrides_the_system(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    system: str,
    forced: str,
) -> None:
    _system_locale(monkeypatch, {"LANG": system})
    monkeypatch.setenv("CVA_UI_LANGUAGE", forced)
    assert clean_settings().ui_language == forced


def test_ui_language_env_and_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    monkeypatch.setenv("CVA_UI_LANGUAGE", "en")
    assert clean_settings().ui_language == "en"
    monkeypatch.delenv("CVA_UI_LANGUAGE")
    toml = tmp_path / "config.toml"
    toml.write_text('ui_language = "es"\n', encoding="utf-8")
    assert clean_settings(toml_file=str(toml)).ui_language == "es"


def test_ui_language_rejects_unknown_values(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("CVA_UI_LANGUAGE", "fr")
    with pytest.raises(ValidationError, match="ui_language"):
        clean_settings()


def test_the_suite_pins_spanish(clean_settings: Callable[..., Settings]) -> None:
    assert clean_settings().ui_language == "es"


# --- /help, menu, buttons ----------------------------------------------------


def test_spanish_is_byte_identical_to_before_i18n() -> None:
    assert _render("es") == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_english_help_is_english() -> None:
    text = help_text(lang="en")
    assert text.startswith("Job offer tracking assistant.")
    assert "🔘 Card buttons" in text
    assert "e.g. /cv 42 BACKEND en" in text
    assert "/rescue <id>" in text and "/basecover [es|en|ca] (or /base)" in text
    assert "Language: ES/EN/CA — changes the CV language (Backend only ES/EN)." in text
    assert (
        "Variant: AI/Backend — switches the CV profile between AI and Backend." in text
    )
    assert "Ej.:" not in text and "/rescatar" not in text
    assert len(text) < 4096


def test_english_toggles_name_three_families() -> None:
    text = help_text(REGISTRIES["three"], lang="en")
    assert "switches the CV profile between AI, Backend and Frontend." in text


@pytest.mark.parametrize("lang", LANGS)
def test_help_names_every_command_of_its_language(lang: Lang) -> None:
    text = help_text(lang=lang)
    for command in (c for c in COMMANDS if c.listed):
        assert f"/{command.name_for(lang)}" in text, command.name
        for alias in command.aliases:
            assert f"/{alias}" in text, alias


def test_menu_shows_the_ui_language_names() -> None:
    english = {name for name, _ in menu_commands("en")}
    spanish = {name for name, _ in menu_commands("es")}
    assert ENGLISH <= english and not SPANISH_ONLY & english
    assert SPANISH_ONLY <= spanish and not ENGLISH & spanish
    assert (
        dict(menu_commands("en"))["rescue"] == "Recover a discarded offer (see /panel)"
    )


# --- bot wiring --------------------------------------------------------------


def _handlers(settings: Settings, tmp_path: Path) -> dict[str, object]:
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    app = bot.build_app(settings, service)
    out: dict[str, object] = {}
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                out.update(dict.fromkeys(handler.commands, handler.callback))
    return out


def test_every_spanish_and_english_name_and_alias_is_registered(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    handlers = _handlers(clean_settings(), tmp_path)
    expected = {name for c in COMMANDS for name in (*c.names, *c.aliases)}
    assert set(handlers) == expected
    assert ENGLISH | SPANISH_ONLY | {"base", "start"} <= expected
    for spanish, english in [
        ("rescatar", "rescue"),
        ("calibracion", "calibration"),
        ("pausa", "pause"),
        ("reanudar", "resume"),
        ("cartabase", "basecover"),
        ("cartabase", "base"),
        ("help", "start"),
    ]:
        assert handlers[spanish] is handlers[english], english


def test_start_help_follows_the_ui_language(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    for lang in LANGS:
        message = FakeMessage()
        context = FakeContext(service)
        context.bot_data[bot._LANG_KEY] = lang
        asyncio.run(bot.start_help(FakeUpdate(message=message), context))  # type: ignore[arg-type]
        assert message.replies == [help_text(lang=lang)]


def test_build_app_stores_the_ui_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    monkeypatch.setenv("CVA_UI_LANGUAGE", "en")
    app = bot.build_app(clean_settings(), service)
    assert app.bot_data[bot._LANG_KEY] == "en"


def test_register_menu_publishes_the_ui_language_menu() -> None:
    from tests.test_commands import FakeApp, FakeMenuBot

    app = FakeApp(FakeMenuBot())
    bot.register_menu(app, "en")  # type: ignore[arg-type]
    asyncio.run(app.post_init(app))  # type: ignore[misc]
    assert app.bot.calls == [list(menu_commands("en"))]
