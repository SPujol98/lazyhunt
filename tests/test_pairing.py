"""Pairing codes (beta3-ux T1, S1/S2 P1): owner claim and extension pairing.

* The owner Telegram id is captured with a one-time code: while
  ``owner_telegram_id`` is unset the bot runs a claim app; the first private
  ``/start <code>`` with the code printed in the bot's terminal makes its sender
  the owner (written to ``config.local.toml``). Everything else is ignored
  with no reply; wrong codes get no reply and lock the code after 5 tries.
* The extension trades a one-time code from ``/connect`` for the bridge
  secret through ``POST /pair`` (extension origin only, single use, 401 for
  any bad code, locked after 5 tries).
* Long replies (``/help`` with a real profile's families) are split under
  Telegram's 4096-character limit.
"""

import asyncio
import io
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from telegram.ext import ApplicationHandlerStop

import bot
from cva import bridge
from cva.commands import help_text, split_message
from cva.config import Settings
from cva.families import BUILTIN_REGISTRY, Family, FamilyRegistry
from cva.i18n import t
from cva.setup.pairing import (
    ALPHABET,
    CODE_LENGTH,
    OneTimeCode,
    display,
    save_owner_id,
)
from tests.test_bot import FakeMessage, FakeUpdate

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"
CLAIMER = 777
SECRET = "pair-secret-xyz"
EXT_ORIGIN = "chrome-extension://abcdefghijklmnop"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- OneTimeCode -----------------------------------------------------------------


def test_code_is_eight_characters_of_the_unambiguous_alphabet() -> None:
    codes = {OneTimeCode(ttl_seconds=60).issue() for _ in range(50)}
    assert len(codes) == 50  # random, not a counter
    for code in codes:
        assert len(code) == CODE_LENGTH and set(code) <= set(ALPHABET)
    assert not set("0O1IL") & set(ALPHABET)


def test_code_works_once_and_ignores_case_blanks_and_dashes() -> None:
    store = OneTimeCode(ttl_seconds=60)
    code = store.issue()
    assert store.verify(f" {display(code).lower()} ") is True
    assert store.verify(code) is False  # single use
    assert store.active is False


def test_code_expires() -> None:
    clock = Clock()
    store = OneTimeCode(ttl_seconds=600, clock=clock)
    code = store.issue()
    clock.now += 600
    assert store.verify(code) is False


def test_five_wrong_tries_lock_the_code_until_a_new_one() -> None:
    store = OneTimeCode(ttl_seconds=60)
    code = store.issue()
    for _ in range(4):
        assert store.verify("WRONG") is False
    assert store.locked is False
    assert store.verify("WRONG") is False
    assert store.locked is True
    assert store.verify(code) is False  # even the right one, once locked
    fresh = store.issue()
    assert store.locked is False and store.verify(fresh) is True


# --- save_owner_id ---------------------------------------------------------------


def test_save_owner_id_changes_only_that_line(tmp_path: Path) -> None:
    config = tmp_path / "config.local.toml"
    config.write_bytes(
        b'# mine\r\nui_language = "es"\r\nowner_telegram_id = 0  # x\r\n'
    )
    save_owner_id(config, CLAIMER)
    assert config.read_bytes() == (
        b'# mine\r\nui_language = "es"\r\nowner_telegram_id = 777  # x\r\n'
    )


def test_save_owner_id_creates_a_missing_file(tmp_path: Path) -> None:
    config = tmp_path / "config.local.toml"
    save_owner_id(config, CLAIMER)
    assert tomllib.loads(config.read_text("utf-8")) == {"owner_telegram_id": CLAIMER}


def test_save_owner_id_refuses_a_bad_id_and_keeps_the_file(tmp_path: Path) -> None:
    config = tmp_path / "config.local.toml"
    config.write_text("profile_dir = 'p'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        save_owner_id(config, 0)
    assert config.read_text("utf-8") == "profile_dir = 'p'\n"


# --- the claim app ---------------------------------------------------------------


class _Stoppable:
    def __init__(self) -> None:
        self.stopped = 0

    def stop_running(self) -> None:
        self.stopped += 1


def _claim_app(
    tmp_path: Path, clean_settings: Callable[..., Settings], *, profile: bool = False
) -> tuple[Any, str, Path]:
    kwargs: dict[str, object] = {"ui_language": "en"}
    if profile:
        target = tmp_path / "me"
        shutil.copytree(DEMO_PROFILE, target)
        kwargs["profile_dir"] = str(target)
    else:
        (tmp_path / "empty").mkdir()
        kwargs["profile_dir"] = str(tmp_path / "empty")
    store = OneTimeCode(ttl_seconds=600)
    code = store.issue()
    config = tmp_path / "config.local.toml"
    config.write_text('ui_language = "en"\n', encoding="utf-8")
    app = bot.build_claim_app(clean_settings(**kwargs), store, config)
    return app, code, config


def _message(text: str, chat: str = "private") -> FakeMessage:
    message = FakeMessage(text)
    message.chat = SimpleNamespace(type=chat)  # type: ignore[attr-defined]
    return message


def _gate(app: Any, update: Any, application: Any = None) -> Any:
    context = SimpleNamespace(
        bot_data=app.bot_data,
        user_data={},
        application=application or _Stoppable(),
    )
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.claim_gate(update, context))
    return context


def test_claim_app_has_only_the_claim_gate(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _, _ = _claim_app(tmp_path, clean_settings)
    handlers = [h for group in app.handlers.values() for h in group]
    assert [h.callback for h in handlers] == [bot.claim_gate]
    assert app.error_handlers  # errors are logged like the other apps


@pytest.mark.parametrize(
    "text", ["/start", "hello", "/help", "/start a b", "/setup", "/start WRONG1"]
)
def test_before_the_claim_everything_else_gets_no_reply(
    tmp_path: Path, clean_settings: Callable[..., Settings], text: str
) -> None:
    app, _, config = _claim_app(tmp_path, clean_settings)
    message = _message(text)
    application = _Stoppable()
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER), application)
    assert message.replies == []
    assert application.stopped == 0
    assert config.read_text("utf-8") == 'ui_language = "en"\n'
    assert app.bot_data[bot._OWNER_KEY] == 0


def test_the_right_code_makes_the_sender_the_owner(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, config = _claim_app(tmp_path, clean_settings)
    message = _message(f"/start {code}")
    application = _Stoppable()
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER), application)
    assert tomllib.loads(config.read_text("utf-8")) == {
        "ui_language": "en",
        "owner_telegram_id": CLAIMER,
    }
    assert app.bot_data[bot._OWNER_KEY] == CLAIMER  # the gate allows it at once
    assert app.bot_data[bot._CLAIMED_KEY] == CLAIMER
    assert application.stopped == 1
    # An empty profile: the next step is /setup (claim first, then setup mode).
    next_step = t("bot.claim.next_setup", "en", setup="/setup")
    assert message.replies == [t("bot.claim.done", "en", next=next_step)]


def test_a_complete_profile_points_to_help(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, _ = _claim_app(tmp_path, clean_settings, profile=True)
    message = _message(f"/start@my_bot {display(code)}")
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER))
    next_step = t("bot.claim.next_help", "en")
    assert message.replies == [t("bot.claim.done", "en", next=next_step)]


def test_the_code_works_only_in_a_private_chat(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, _ = _claim_app(tmp_path, clean_settings)
    message = _message(f"/start {code}", chat="group")
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER))
    assert message.replies == [] and app.bot_data[bot._OWNER_KEY] == 0


def test_after_the_claim_others_are_ignored_and_owner_updates_kept(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, _ = _claim_app(tmp_path, clean_settings)
    _gate(app, FakeUpdate(message=_message(f"/start {code}"), user_id=CLAIMER))
    stranger = _message(f"/start {code}")
    _gate(app, FakeUpdate(message=stranger, user_id=999))
    owner_update = FakeUpdate(message=_message("/setup"), user_id=CLAIMER)
    _gate(app, owner_update)
    assert stranger.replies == []
    assert app.bot_data[bot._CLAIMED_KEY] == CLAIMER
    assert app.bot_data[bot._CLAIM_REPLAY_KEY] == [owner_update]


def test_five_wrong_codes_lock_the_claim_until_a_restart(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, config = _claim_app(tmp_path, clean_settings)
    for _ in range(5):
        _gate(app, FakeUpdate(message=_message("/start AAAAAAAA"), user_id=999))
    message = _message(f"/start {code}")
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER))
    assert message.replies == []
    assert "owner_telegram_id" not in config.read_text("utf-8")


def test_a_config_that_cannot_be_saved_is_reported_to_the_claimer(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, code, config = _claim_app(tmp_path, clean_settings)
    config.write_text("this is not toml\n", encoding="utf-8")
    message = _message(f"/start {code}")
    application = _Stoppable()
    _gate(app, FakeUpdate(message=message, user_id=CLAIMER), application)
    assert message.replies == [t("bot.claim.save_failed", "en", error="ValueError")]
    assert app.bot_data[bot._OWNER_KEY] == 0 and application.stopped == 0


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_the_code_is_shown_only_on_a_terminal(caplog: pytest.LogCaptureFixture) -> None:
    terminal = _Stream(tty=True)
    assert bot.announce_claim_code("ABCDEFGH", "es", terminal) is True
    assert "/start ABCD-EFGH" in terminal.getvalue()
    assert t("bot.claim.terminal", "es", code="ABCD-EFGH") in terminal.getvalue()

    log_file = _Stream(tty=False)
    with caplog.at_level("INFO"):
        assert bot.announce_claim_code("ABCDEFGH", "en", log_file) is False
    assert log_file.getvalue() == ""
    assert "ABCD" not in caplog.text and "owner_telegram_id is not set" in caplog.text


def test_main_without_owner_claims_first_then_starts_setup_mode(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram.ext import Application

    profile = tmp_path / "me"
    profile.mkdir()
    settings = clean_settings(profile_dir=str(profile))
    monkeypatch.setattr(bot, "Settings", lambda: settings)
    monkeypatch.setattr(bot, "configure_logging", lambda: None)
    monkeypatch.setattr(bot, "announce_claim_code", lambda *a, **k: False)
    runs: list[Any] = []

    def fake_run(self: Any, *args: Any, **kwargs: Any) -> None:
        if bot._CLAIM_CODE_KEY in self.bot_data:
            runs.append(("claim", self.bot_data[bot._OWNER_KEY]))
            self.bot_data[bot._CLAIMED_KEY] = CLAIMER
            self.bot_data[bot._CLAIM_REPLAY_KEY] = []
        else:
            runs.append(("setup", self.bot_data[bot._OWNER_KEY]))

    monkeypatch.setattr(Application, "run_polling", fake_run)
    bot.main()
    assert runs == [("claim", 0), ("setup", CLAIMER)]


def test_main_without_owner_stops_when_nobody_claims(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from telegram.ext import Application

    settings = clean_settings(profile_dir=str(tmp_path / "me"))
    monkeypatch.setattr(bot, "Settings", lambda: settings)
    monkeypatch.setattr(bot, "configure_logging", lambda: None)
    monkeypatch.setattr(bot, "announce_claim_code", lambda *a, **k: False)
    runs: list[Any] = []
    monkeypatch.setattr(
        Application, "run_polling", lambda self, *a, **k: runs.append(self)
    )

    def never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("nothing else starts before the claim")

    monkeypatch.setattr(bot, "build_setup_app", never)
    monkeypatch.setattr(bot.JobService, "from_settings", never)
    bot.main()
    assert len(runs) == 1


# --- /connect ----------------------------------------------------------------------


def _connect(bot_data: dict[str, Any], lang: str = "en") -> FakeMessage:
    message = FakeMessage("/connect")
    context = SimpleNamespace(bot_data={bot._LANG_KEY: lang, **bot_data})
    asyncio.run(bot.connect_command(FakeUpdate(message=message), context))
    return message


@pytest.mark.parametrize("lang", ["en", "es"])
def test_connect_issues_a_one_time_code(lang: str) -> None:
    store = OneTimeCode(ttl_seconds=600)
    (reply,) = _connect({bridge.PAIRING_KEY: store}, lang).replies
    shown = next(word for word in reply.split() if len(word) == 9 and "-" in word)
    assert store.verify(shown) is True
    command = "/connect" if lang == "en" else "/conectar"
    assert reply == t("bot.connect.code", lang, code=shown, connect=command)


def test_connect_without_a_bridge_says_it_is_off() -> None:
    assert _connect({}).replies == [t("bot.connect.disabled", "en")]


def test_connect_is_registered_in_the_full_and_setup_apps(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    from tests.test_setup_mode import _commands

    profile = tmp_path / "me"
    profile.mkdir()
    setup = bot.build_setup_app(
        clean_settings(profile_dir=str(profile), owner_telegram_id=5)
    )
    assert _commands(setup)["connect"] is bot.connect_command
    assert _commands(setup)["conectar"] is bot.connect_command
    assert bot._COMMAND_HANDLERS["conectar"] is bot.connect_command


# --- POST /pair --------------------------------------------------------------------


@pytest.fixture(params=["full", "setup"])
def pair_bridge(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> Any:
    monkeypatch.chdir(tmp_path)
    if request.param == "setup":
        profile = tmp_path / "profiles" / "me"
        profile.mkdir(parents=True)
        settings = clean_settings(
            profile_dir=str(profile),
            owner_telegram_id=5,
            CVA_BRIDGE_SECRET=SECRET,
            bridge_port=0,
        )
        app = SimpleNamespace(post_init=None, post_shutdown=None, bot_data={})
        handle: Any = bridge.register_setup(app, settings)
    else:
        monkeypatch.setattr(bridge, "DEFAULT_DB_PATH", tmp_path / "app.db")
        handle = bridge._Bridge(secret=SECRET, port=0, owner_id=5, threshold=65)
        handle.start()
        app = SimpleNamespace(bot_data={bridge.PAIRING_KEY: handle.pairing})
    assert handle is not None
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{handle.port}/pair",
        store=app.bot_data[bridge.PAIRING_KEY],
    )
    handle.stop()


def _pair(url: str, code: str, origin: str | None = EXT_ORIGIN) -> httpx.Response:
    headers = {"Origin": origin} if origin is not None else {}
    return httpx.post(url, json={"code": code}, headers=headers, timeout=5)


def test_pair_trades_the_code_for_the_secret_once(pair_bridge: Any) -> None:
    code = pair_bridge.store.issue()
    first = _pair(pair_bridge.url, display(code).lower())
    assert (first.status_code, first.json()) == (200, {"secret": SECRET})
    again = _pair(pair_bridge.url, code)
    assert (again.status_code, again.json()) == (
        401,
        {"error": "invalid or expired code"},
    )


def test_pair_refuses_without_an_extension_origin(pair_bridge: Any) -> None:
    code = pair_bridge.store.issue()
    for origin in (None, "https://evil.example", "null"):
        response = _pair(pair_bridge.url, code, origin)
        assert response.status_code == 403 and SECRET not in response.text
    assert _pair(pair_bridge.url, code, "moz-extension://x").status_code == 200


def test_pair_refuses_bad_bodies_and_leaks_nothing(pair_bridge: Any) -> None:
    pair_bridge.store.issue()
    headers = {"Origin": EXT_ORIGIN}
    as_form = httpx.post(pair_bridge.url, data={"code": "x"}, headers=headers)
    assert as_form.status_code == 415
    no_code = httpx.post(pair_bridge.url, json={}, headers=headers)
    assert no_code.status_code == 400
    for response in (as_form, no_code):
        assert SECRET not in response.text


def test_pair_without_an_issued_code_is_refused(pair_bridge: Any) -> None:
    response = _pair(pair_bridge.url, "ABCDEFGH")
    assert response.status_code == 401 and SECRET not in response.text


def test_pair_locks_after_five_wrong_codes(pair_bridge: Any) -> None:
    code = pair_bridge.store.issue()
    for _ in range(5):
        assert _pair(pair_bridge.url, "WRONGCOD").status_code == 401
    assert _pair(pair_bridge.url, code).status_code == 401


def test_pair_answers_no_get_and_no_preflight(pair_bridge: Any) -> None:
    assert httpx.get(pair_bridge.url).status_code == 405
    preflight = httpx.request(
        "OPTIONS", pair_bridge.url, headers={"Origin": EXT_ORIGIN}
    )
    assert preflight.status_code == 501
    assert "access-control-allow-origin" not in preflight.headers


def test_pair_code_store_is_published_for_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge._Bridge, "start", lambda self: None)
    monkeypatch.setattr(bridge.atexit, "register", lambda fn: None)
    monkeypatch.setattr(bridge, "DEFAULT_DB_PATH", tmp_path / "app.db")
    settings = SimpleNamespace(
        bridge_secret=SimpleNamespace(get_secret_value=lambda: SECRET),
        owner_telegram_id=5,
        bridge_port=0,
        profile_dir=None,
        auto_analyze=False,
        analyze_delay_seconds=0,
        notify_min_score=None,
        round_summary=True,
        paused=False,
        enabled_portals=None,
        ui_language="en",
    )
    service = SimpleNamespace(threshold=65, loaded_preferences=None)
    app = SimpleNamespace(bot_data={}, post_init=None, post_shutdown=None)
    handle = bridge.register(app, settings, service)  # type: ignore[arg-type]
    assert handle is not None
    assert app.bot_data[bridge.PAIRING_KEY] is handle.pairing


# --- long replies (owner's beta.2 test: /start crashed "Message is too long") ------


def _many_families() -> FamilyRegistry:
    extra = tuple(
        Family(
            id=f"family{n:02d}",
            label=f"Very long custom role family label number {n:02d} from setup",
            languages=("en", "es"),
        )
        for n in range(30)
    )
    return FamilyRegistry((*BUILTIN_REGISTRY.families, *extra))


def test_split_message_keeps_every_line_under_the_limit() -> None:
    text = "\n".join(f"line {n} " + "x" * 50 for n in range(200))
    pieces = split_message(text, limit=500)
    assert len(pieces) > 1 and all(len(p) <= 500 for p in pieces)
    assert "\n".join(pieces).replace("\n", "") == text.replace("\n", "")
    assert split_message("short") == ["short"]
    assert all(len(p) <= 10 for p in split_message("y" * 25, limit=10))


@pytest.mark.parametrize("lang", ["en", "es"])
def test_help_with_a_big_profile_is_sent_in_several_messages(lang: str) -> None:
    families = _many_families()
    full = help_text(families, lang=lang)
    assert len(full) > 4096  # what crashed /start after a real /setup
    service = SimpleNamespace(preferences=SimpleNamespace(families=families))
    message = FakeMessage("/start")
    context = SimpleNamespace(bot_data={bot._SERVICE_KEY: service, bot._LANG_KEY: lang})
    asyncio.run(bot.start_help(FakeUpdate(message=message), context))
    assert len(message.replies) > 1
    assert all(len(reply) <= 4096 for reply in message.replies)
    assert "\n".join(message.replies).split() == full.split()


def test_help_that_fits_is_still_one_message() -> None:
    message = FakeMessage("/help")
    context = SimpleNamespace(bot_data={bot._LANG_KEY: "en"})
    asyncio.run(bot.start_help(FakeUpdate(message=message), context))
    assert message.replies == [help_text(lang="en")]


def test_setup_app_registers_the_error_handler(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    app = bot.build_setup_app(
        clean_settings(profile_dir=str(profile), owner_telegram_id=5)
    )
    assert bot.on_error in app.error_handlers
