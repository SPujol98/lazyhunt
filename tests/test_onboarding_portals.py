"""Portals are enabled only in the extension (portals-extension-only).

``/setup`` no longer asks for portals nor writes ``enabled_portals``: the
owner enables a portal in the extension's Options › Portals (its terms
warning) and ticks it in each search. A ``config.local.toml`` that an older
``/setup`` gave an ``enabled_portals`` list loses that key once, on the next
start (:meth:`cva.service.JobService.migrate_portals_config`, through the
bot's startup helper); a list the owner sets by hand afterwards is kept and
the bridge still enforces it. Driven through the bot's public handlers and
the bridge's HTTP endpoints.
"""

import logging
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import bot
from cva import bridge
from cva.config import Settings
from cva.i18n import Lang, t
from cva.ingest.sources import SOURCES
from cva.onboarding import flow
from cva.service import JobService
from tests.portability import symlink_or_skip
from tests.test_onboarding import _answer_everything, _full_app, _setup_mode
from tests.test_onboarding_sections import _start

SECRET = "s3cret-portals"
CONFIG = (
    "# My settings\r\n"
    "owner_telegram_id = 4242   # me\r\n"
    "\r\n"
    "# portals I use\r\n"
    "enabled_portals = [\r\n"
    '  "indeed",\r\n'
    "]  # old /setup list\r\n"
    'ui_language = "en"\r\n'
    "\r\n"
    "[extra]  # a table the bot ignores\r\n"
    "enabled_portals = 1\r\n"
)
# CONFIG without its top-level enabled_portals lines (the table's key stays).
MIGRATED = CONFIG.replace(
    'enabled_portals = [\r\n  "indeed",\r\n]  # old /setup list\r\n', ""
)


# --- /setup never asks for portals nor writes them ---------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_full_setup_never_asks_for_portals_nor_writes_them(
    tmp_path: Path, clean_settings: Callable[..., Settings], lang: Lang
) -> None:
    d, _, config = _setup_mode(tmp_path, lang)
    d.context.bot_data[bot._SETUP_SETTINGS_KEY] = clean_settings()
    d.start()
    _answer_everything(d, lang)
    d.press("confirm")
    reply = d.replies[-1]
    assert reply.startswith(t("onboarding.saved", lang))
    assert "enabled_portals" not in reply
    data = tomllib.loads(config.read_text("utf-8")) if config.is_file() else {}
    assert "enabled_portals" not in data
    # The full app main() starts next accepts every registered portal.
    assert d.context.bot_data[bot._SETUP_SETTINGS_KEY].enabled_portals is None


def test_setup_portals_answers_unknown_section(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, config, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    config.write_bytes(CONFIG.encode("utf-8"))
    _start(d, "/setup portals")
    assert d.last == t(
        "onboarding.section.unknown",
        "en",
        section="portals",
        sections=", ".join(flow.section_names("en")),
    )
    assert config.read_bytes() == CONFIG.encode("utf-8")


def test_a_section_rerun_never_touches_the_config(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, config, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    config.write_bytes(CONFIG.encode("utf-8"))
    _start(d, "/setup level")
    d.press("go:mid")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert config.read_bytes() == CONFIG.encode("utf-8")


# --- one-time removal of the old /setup list on start ------------------------------


@pytest.fixture
def boot(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    """A bot start: settings as loaded from ``config.local.toml``, the
    service (its ``app_state``) and a way to start the bridge from the
    settings the startup helper returns."""
    _, profile, config, service = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    handles: list[bridge.BridgeHandle] = []

    def settings_for(portals: list[str] | None) -> Settings:
        return clean_settings(
            profile_dir=str(profile),
            owner_telegram_id=4242,
            CVA_BRIDGE_SECRET=SECRET,
            bridge_port=0,
            enabled_portals=portals,
        )

    def start(settings: Settings, svc: JobService = service) -> tuple[Settings, str]:
        started = bot._migrate_portals_config(settings, svc, config)
        app = SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={})
        handle = bridge.register(app, started, svc)
        assert handle is not None
        handles.append(handle)
        return started, f"http://127.0.0.1:{handle.port}"

    yield SimpleNamespace(
        config=config, service=service, settings_for=settings_for, start=start
    )
    for handle in handles:
        handle.stop()


def _served(base: str) -> list[str]:
    resp = httpx.get(
        f"{base}/config", headers={"X-CVA-Bridge-Secret": SECRET}, timeout=10
    )
    assert resp.status_code == 200
    return list(resp.json()["enabledPortals"])


def test_start_removes_the_old_list_once_and_the_bridge_accepts_every_portal(
    boot: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    boot.config.write_bytes(CONFIG.encode("utf-8"))
    with caplog.at_level(logging.INFO):
        settings, base = boot.start(boot.settings_for(["indeed"]))
    # Only that key went: every other byte (CRLF, comments, table) kept.
    assert boot.config.read_bytes() == MIGRATED.encode("utf-8")
    assert settings.enabled_portals is None
    assert _served(base) == list(SOURCES)
    infos = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "enabled_portals" in r.getMessage()
    ]
    assert len(infos) == 1


def test_a_list_set_by_hand_after_the_removal_is_kept_and_enforced(
    boot: SimpleNamespace,
) -> None:
    boot.config.write_bytes(CONFIG.encode("utf-8"))
    boot.start(boot.settings_for(["indeed"]))
    by_hand = MIGRATED.replace(
        'ui_language = "en"\r\n',
        'ui_language = "en"\r\nenabled_portals = ["indeed"]\r\n',
    )
    boot.config.write_bytes(by_hand.encode("utf-8"))
    settings, base = boot.start(boot.settings_for(["indeed"]))  # next start
    assert boot.config.read_bytes() == by_hand.encode("utf-8")
    assert settings.enabled_portals == ["indeed"]
    assert _served(base) == ["indeed"]


def test_a_config_without_the_key_keeps_its_bytes_and_a_later_list_is_kept(
    boot: SimpleNamespace,
) -> None:
    boot.config.write_bytes(MIGRATED.encode("utf-8"))
    settings, base = boot.start(boot.settings_for(None))
    assert boot.config.read_bytes() == MIGRATED.encode("utf-8")
    assert settings.enabled_portals is None
    assert _served(base) == list(SOURCES)
    # The owner adds a list by hand later: the next start keeps it.
    boot.config.write_bytes(CONFIG.encode("utf-8"))
    settings, _ = boot.start(boot.settings_for(["indeed"]))
    assert boot.config.read_bytes() == CONFIG.encode("utf-8")
    assert settings.enabled_portals == ["indeed"]


def test_a_missing_config_is_not_created(boot: SimpleNamespace) -> None:
    assert not boot.config.exists()
    settings, _ = boot.start(boot.settings_for(None))
    assert not boot.config.exists()
    assert settings.enabled_portals is None


def test_a_config_that_cannot_be_written_logs_a_warning_and_keeps_running(
    boot: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    boot.config.write_bytes(CONFIG.encode("utf-8"))

    def refuse(src: object, dst: object) -> None:
        raise PermissionError("locked by another program")

    monkeypatch.setattr("cva.fsutil.os.replace", refuse)
    with caplog.at_level(logging.WARNING):
        settings, base = boot.start(boot.settings_for(["indeed"]))
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert boot.config.read_bytes() == CONFIG.encode("utf-8")
    assert sorted(p.name for p in boot.config.parent.glob("config.local.toml*")) == [
        "config.local.toml"
    ]
    # The bridge still honours the old list until a start can remove it.
    assert settings.enabled_portals == ["indeed"]
    assert _served(base) == ["indeed"]
    monkeypatch.undo()
    settings, _ = boot.start(boot.settings_for(["indeed"]))  # retried next start
    assert boot.config.read_bytes() == MIGRATED.encode("utf-8")
    assert settings.enabled_portals is None


@pytest.mark.parametrize(
    "line",
    [
        'enabled_portals = ["indeed"]  # my portals\n',
        'enabled_portals = [\n  "indeed",\n]   # mine # really\n',
        "enabled_portals = ['in#deed', \"x]\"]\n",
        '  enabled_portals=["indeed"]\n',
    ],
)
def test_start_removes_only_that_key_whatever_its_shape(
    boot: SimpleNamespace, line: str
) -> None:
    boot.config.write_text(f'# top\n{line}ui_language = "en"\n', encoding="utf-8")
    boot.start(boot.settings_for(None))
    assert boot.config.read_text("utf-8") == '# top\nui_language = "en"\n'


def test_start_writes_through_a_symlinked_config(
    boot: SimpleNamespace, tmp_path: Path
) -> None:
    real = tmp_path / "dotfiles" / "cva.toml"
    real.parent.mkdir()
    real.write_bytes(CONFIG.encode("utf-8"))
    symlink_or_skip(boot.config, real)
    boot.start(boot.settings_for(["indeed"]))
    assert boot.config.is_symlink() and boot.config.resolve() == real.resolve()
    assert real.read_bytes() == MIGRATED.encode("utf-8")
