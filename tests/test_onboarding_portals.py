"""``/setup`` activates the chosen portals in ``config.local.toml`` (fix after T10).

Confirm (the full questionnaire and ``/setup portals``) writes
``enabled_portals`` with the line-preserving writer of the setup wizard,
inside the all-or-nothing snapshot, and the running bot serves the new list
at once (the bridge's ``/config``); setup mode passes it to the full app.
Driven through the bot's public handlers (:class:`tests.test_onboarding.Driver`).
"""

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
from cva.onboarding import flow
from cva.onboarding.portals import portal_label
from tests.test_onboarding import Driver, _answer_everything, _full_app, _setup_mode
from tests.test_onboarding_sections import _start

SECRET = "s3cret-portals"
CONFIG = (
    "# My settings\r\n"
    "owner_telegram_id = 4242   # me\r\n"
    "\r\n"
    "# portals I use\r\n"
    "enabled_portals = [\r\n"
    '  "indeed",\r\n'
    "]\r\n"
    'ui_language = "en"\r\n'
    "\r\n"
    "[extra]  # a table the bot ignores\r\n"
    "x = 1\r\n"
)


def _portals_section(d: Driver, wanted: tuple[str, ...]) -> None:
    _start(d, "/setup portals")
    assert d.step == "portals"
    for portal in ("linkedin", "infojobs", "indeed"):
        chosen = d.context.user_data[flow.STATE_KEY]["a"].get("portals") or []
        if (portal in wanted) != (portal in chosen):
            d.press(f"t:{portal}")
            if portal not in chosen:
                d.press("yes")  # its terms warning
    d.press("done")
    assert d.step == "summary"
    d.press("confirm")


@pytest.fixture
def live(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    d, profile, config, service = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    config.write_bytes(CONFIG.encode("utf-8"))
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=4242,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
        enabled_portals=["indeed"],
    )
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={})
    handle = bridge.register(app, settings, service)
    assert handle is not None
    d.context.bot_data[bridge.BOT_DATA_KEY] = handle
    yield SimpleNamespace(d=d, config=config, base=f"http://127.0.0.1:{handle.port}")
    handle.stop()


def _served(live: SimpleNamespace) -> list[str]:
    resp = httpx.get(
        f"{live.base}/config", headers={"X-CVA-Bridge-Secret": SECRET}, timeout=10
    )
    assert resp.status_code == 200
    return list(resp.json()["enabledPortals"])


def test_setup_portals_writes_the_list_and_the_bridge_serves_it_at_once(
    live: SimpleNamespace,
) -> None:
    assert _served(live) == ["indeed"]
    _portals_section(live.d, ("linkedin", "infojobs"))
    reply = live.d.replies[-1]
    assert reply.startswith(t("onboarding.saved", "en"))
    labels = ", ".join(portal_label(p) for p in ("linkedin", "infojobs"))
    assert t("onboarding.portals.enabled", "en", portals=labels) in reply
    assert "add this line" not in reply
    # Only that line changed: every other byte (CRLF, comments, table) kept.
    expected = CONFIG.replace(
        'enabled_portals = [\r\n  "indeed",\r\n]\r\n',
        'enabled_portals = ["linkedin", "infojobs"]\r\n',
    )
    assert live.config.read_bytes() == expected.encode("utf-8")
    assert _served(live) == ["linkedin", "infojobs"]  # no restart


@pytest.mark.parametrize("lang", ["en", "es"])
def test_full_setup_activates_the_portals_in_setup_mode(
    tmp_path: Path, clean_settings: Callable[..., Settings], lang: Lang
) -> None:
    d, _, config = _setup_mode(tmp_path, lang)
    d.context.bot_data[bot._SETUP_SETTINGS_KEY] = clean_settings()
    d.start()
    _answer_everything(d, lang)
    d.press("confirm")
    reply = d.replies[-1]
    labels = ", ".join(portal_label(p) for p in ("linkedin", "infojobs"))
    assert t("onboarding.portals.enabled", lang, portals=labels) in reply
    data = tomllib.loads(config.read_text("utf-8"))
    assert data["enabled_portals"] == ["linkedin", "infojobs"]
    # The full app main() starts next is built from these settings.
    settings = d.context.bot_data[bot._SETUP_SETTINGS_KEY]
    assert settings.enabled_portals == ["linkedin", "infojobs"]


def test_a_config_the_bot_could_not_load_rolls_everything_back(
    live: SimpleNamespace,
) -> None:
    bad = CONFIG.replace("owner_telegram_id = 4242", 'owner_telegram_id = "me"')
    live.config.write_bytes(bad.encode("utf-8"))
    profile = Path(live.d.context.bot_data[bot._PROFILE_DIR_KEY])
    answers = profile / "onboarding_answers.json"
    before = answers.read_bytes() if answers.is_file() else None
    _portals_section(live.d, ("linkedin",))
    assert t("onboarding.write_undone", "en") in live.d.replies[-1]
    assert live.config.read_bytes() == bad.encode("utf-8")
    assert (answers.read_bytes() if answers.is_file() else None) == before
    assert _served(live) == ["indeed"]


def test_other_sections_never_touch_the_config(live: SimpleNamespace) -> None:
    raw = live.config.read_bytes()
    _start(live.d, "/setup level")
    live.d.press("go:mid")
    live.d.press("confirm")
    assert live.d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert live.config.read_bytes() == raw
    assert _served(live) == ["indeed"]


def test_the_portals_step_is_prefilled_from_the_config(live: SimpleNamespace) -> None:
    _start(live.d, "/setup portals")
    on = {b.text for row in live.d.markup.inline_keyboard for b in row}
    assert "✅ " + portal_label("indeed") in on


def test_legacy_install_without_profile_dir_never_writes_the_config(
    tmp_path: Path,
) -> None:
    d, _, config = _setup_mode(tmp_path)
    config.write_bytes(CONFIG.encode("utf-8"))
    d.context.bot_data[bot._PROFILE_DIR_KEY] = None
    d.start()
    assert d.last == t("onboarding.no_profile_dir", "en")
    assert config.read_bytes() == CONFIG.encode("utf-8")
