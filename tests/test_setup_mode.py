"""Setup mode (onboarding-v2 T1, S3): the bot starts with an unconfigured profile.

A newcomer's ``cli.py init`` writes an explicit, possibly EMPTY ``profile_dir``.
Until that profile holds every file the strict loader needs, the bot runs a
reduced app (``/help``, ``/start``, ``/setup``) and a bridge that answers
without crashing; it never builds ``JobService`` and so never reads the legacy
(owner) layout. A broken profile (all files present, one malformed) is NOT setup
mode: it still fails loudly in ``JobService.from_settings``.
"""

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from telegram.ext import ApplicationHandlerStop, CommandHandler

import bot
from cva import bridge
from cva.claimsdb import ClaimsError
from cva.config import Settings
from cva.i18n import t
from cva.ingest.sources import SOURCES
from cva.service import JobService
from cva.setup_mode import missing_profile_files, needs_setup
from tests.test_bot import FakeMessage, FakeQuery, FakeUpdate

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PROFILE = REPO_ROOT / "profiles" / "demo"
SECRET = "s3cret-setup"
OWNER = 5


# --- detection ------------------------------------------------------------------


def test_unset_profile_dir_is_never_setup_mode(
    clean_settings: Callable[..., Settings],
) -> None:
    # The owner's install (legacy layout) keeps starting in full mode.
    assert missing_profile_files(None) == ()
    assert needs_setup(clean_settings()) is False


def test_empty_profile_dir_is_setup_mode(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    missing = missing_profile_files(profile)
    assert "claims.json" in missing and "judge/facts.md" in missing
    assert needs_setup(clean_settings(profile_dir=str(profile))) is True


def test_missing_profile_dir_is_setup_mode(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "nowhere"
    assert needs_setup(clean_settings(profile_dir=str(profile))) is True


def test_complete_profile_is_not_setup_mode(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "me"
    shutil.copytree(DEMO_PROFILE, profile)
    assert missing_profile_files(profile) == ()
    assert needs_setup(clean_settings(profile_dir=str(profile))) is False


def test_broken_profile_is_not_setup_mode_and_still_fails_loudly(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "me"
    shutil.copytree(DEMO_PROFILE, profile)
    (profile / "claims.json").write_text("{not json", encoding="utf-8")
    settings = clean_settings(profile_dir=str(profile))
    monkeypatch.chdir(tmp_path)
    assert needs_setup(settings) is False
    with pytest.raises((ClaimsError, ValueError)):
        JobService.from_settings(settings, db_path=tmp_path / "app.db")


# --- the setup-mode bot ---------------------------------------------------------


def _setup_app(tmp_path: Path, clean_settings: Callable[..., Settings], lang: str):
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True, exist_ok=True)
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=OWNER,
        ui_language=lang,
    )
    return bot.build_setup_app(settings), profile


class _StoppableApp:
    def __init__(self) -> None:
        self.stopped = 0

    def stop_running(self) -> None:
        self.stopped += 1


def _context(app: Any, application: Any = None) -> Any:
    return SimpleNamespace(
        bot_data=app.bot_data,
        user_data={},
        args=[],
        application=application if application is not None else _StoppableApp(),
    )


def _commands(app: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in app.handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                out.update(dict.fromkeys(handler.commands, handler.callback))
    return out


def test_setup_app_answers_only_help_start_and_setup(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    assert set(_commands(app)) == {
        "help",
        "start",
        "setup",
        "configurar",
        "connect",  # beta3-ux T1: pair the extension during setup
        "conectar",
    }
    assert bot._SERVICE_KEY not in app.bot_data  # no JobService, no legacy data


@pytest.mark.parametrize("lang", ["en", "es"])
def test_start_in_setup_mode_says_the_profile_is_not_set_up(
    tmp_path: Path, clean_settings: Callable[..., Settings], lang: str
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, lang)
    message = FakeMessage("/start")
    asyncio.run(_commands(app)["start"](FakeUpdate(message=message), _context(app)))
    (reply,) = message.replies
    command = "/setup" if lang == "en" else "/configurar"
    assert command in reply
    # Plain words, no file names (a broken profile names its file instead).
    assert "claims.json" not in reply and "judge/facts.md" not in reply
    assert reply == t("bot.setup.not_set_up", lang, setup=command)


def test_help_in_setup_mode_lists_the_setup_commands(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    message = FakeMessage("/help")
    asyncio.run(_commands(app)["help"](FakeUpdate(message=message), _context(app)))
    (reply,) = message.replies
    assert "/setup" in reply and "/help" in reply
    assert "/analyze" not in reply


def test_setup_command_starts_the_guided_setup(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    message = FakeMessage("/setup")
    asyncio.run(_commands(app)["setup"](FakeUpdate(message=message), _context(app)))
    (reply,) = message.replies
    assert t("onboarding.q.lang", "en") in reply
    assert message.markups[0] is not None  # language buttons


def test_other_commands_text_and_buttons_get_the_not_ready_reply(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    fallbacks = [
        h
        for group in app.handlers.values()
        for h in group
        if h.callback is bot.setup_not_ready
    ]
    assert len(fallbacks) == 2  # messages (any command or text) + buttons
    message = FakeMessage("/analyze 1")
    asyncio.run(bot.setup_not_ready(FakeUpdate(message=message), _context(app)))
    assert message.replies == [t("bot.setup.not_ready", "en")]
    query = FakeQuery("a:1")
    asyncio.run(bot.setup_not_ready(FakeUpdate(query=query), _context(app)))
    assert query.message.replies == [t("bot.setup.not_ready", "en")]


def test_setup_app_keeps_the_owner_gate(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    assert any(h.callback is bot.gate for h in app.handlers[-1])
    message = FakeMessage("/start")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.gate(FakeUpdate(message=message, user_id=999), _context(app)))
    assert message.replies == []


def test_incomplete_profile_stays_in_setup_mode(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app, _ = _setup_app(tmp_path, clean_settings, "en")
    application = _StoppableApp()
    message = FakeMessage("/start")
    asyncio.run(
        bot.setup_reload_check(FakeUpdate(message=message), _context(app, application))
    )
    assert application.stopped == 0 and message.replies == []
    assert not app.bot_data.get(bot._SETUP_DONE_KEY)


def test_completed_profile_leaves_setup_mode_without_a_manual_restart(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)  # the validated service opens data/ here
    app, profile = _setup_app(tmp_path, clean_settings, "en")
    profile.rmdir()
    shutil.copytree(DEMO_PROFILE, profile)  # what T4/T5 will write
    application = _StoppableApp()
    message = FakeMessage("/start")
    update = FakeUpdate(message=message)
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.setup_reload_check(update, _context(app, application)))
    assert application.stopped == 1
    assert app.bot_data[bot._SETUP_DONE_KEY] is True
    assert message.replies == [t("bot.setup.ready", "en")]
    # The full load was validated once and is reused by main().
    assert isinstance(app.bot_data[bot._SETUP_SERVICE_KEY], JobService)
    # The update that saw the profile complete is replayed in the full app.
    assert app.bot_data[bot._SETUP_REPLAY_KEY] == [update]


def test_updates_of_the_switching_batch_are_kept_and_ready_is_said_once(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One getUpdates batch can hold several updates: after the first one sees
    # the profile complete, the rest still run through the setup app before
    # polling stops. They must neither get another "ready" nor be lost.
    monkeypatch.chdir(tmp_path)
    app, profile = _setup_app(tmp_path, clean_settings, "en")
    profile.rmdir()
    shutil.copytree(DEMO_PROFILE, profile)
    application = _StoppableApp()
    first, second = FakeMessage("/start"), FakeMessage("/status")
    updates = [FakeUpdate(message=first), FakeUpdate(message=second)]
    for update in updates:
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(bot.setup_reload_check(update, _context(app, application)))
    assert first.replies == [t("bot.setup.ready", "en")]
    assert second.replies == []
    assert application.stopped == 1
    assert app.bot_data[bot._SETUP_REPLAY_KEY] == updates


def _real_update(update_id: int, text: str) -> Any:
    from telegram import Update

    return Update.de_json(
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 0,
                "chat": {"id": OWNER, "type": "private"},
                "from": {"id": OWNER, "is_bot": False, "first_name": "o"},
                "text": text,
            },
        },
        None,
    )


def test_replayed_updates_reach_the_full_app_queue_in_order() -> None:
    from telegram.ext import Application

    app = Application.builder().token("1:dummy").build()
    calls: list[str] = []

    async def previous(application: Any) -> None:
        calls.append("previous")

    app.post_init = previous
    updates = [_real_update(1, "/start"), _real_update(2, "/status")]
    bot.register_replay(app, updates)
    asyncio.run(app.post_init(app))
    assert calls == ["previous"]
    queued = [app.update_queue.get_nowait() for _ in range(2)]
    assert [u.message.text for u in queued] == ["/start", "/status"]
    assert all(u.get_bot() is app.bot for u in queued)  # rebound to the new bot
    assert app.update_queue.empty()


@pytest.mark.parametrize("lang", ["en", "es"])
def test_complete_but_malformed_profile_stays_in_setup_mode_and_says_which_file(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    lang: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    app, profile = _setup_app(tmp_path, clean_settings, lang)
    profile.rmdir()
    shutil.copytree(DEMO_PROFILE, profile)
    (profile / "claims.json").write_text("{not json", encoding="utf-8")
    application = _StoppableApp()
    message = FakeMessage("/status")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(
            bot.setup_reload_check(
                FakeUpdate(message=message), _context(app, application)
            )
        )
    assert application.stopped == 0  # no switch, so no crash loop
    assert not app.bot_data.get(bot._SETUP_DONE_KEY)
    (reply,) = message.replies
    assert t("bot.setup.ready", lang) not in reply
    assert "claims.json" in reply and "line 1" in reply
    assert str(tmp_path) not in reply  # relative path only
    assert reply.startswith(t("bot.setup.broken", lang, error="")[:20])


def test_semantic_profile_error_names_the_file_without_absolute_paths(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app, profile = _setup_app(tmp_path, clean_settings, "en")
    profile.rmdir()
    shutil.copytree(DEMO_PROFILE, profile)
    # Valid JSON, wrong shape: the loader's own error names the file.
    (profile / "claims.json").write_text("{}", encoding="utf-8")
    message = FakeMessage("/help")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.setup_reload_check(FakeUpdate(message=message), _context(app)))
    (reply,) = message.replies
    assert "claims.json must contain a JSON array" in reply
    assert str(tmp_path) not in reply


def test_load_error_description_keeps_paths_relative(tmp_path: Path) -> None:
    from cva.setup_mode import describe_load_error

    profile = tmp_path / "me"
    profile.mkdir()
    exc = ValueError(
        f"profile.toml unreadable ({profile}/profile.toml): bad; "
        "see /etc/cva/other.toml, /setup and ES/EN"
    )
    text = describe_load_error(profile, exc)
    assert text == (
        "ValueError: profile.toml unreadable (profile.toml): bad; "
        "see other.toml, /setup and ES/EN"
    )
    assert len(describe_load_error(profile, ValueError("x" * 1000))) == 300


def test_fixing_the_malformed_file_then_switches(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    app, profile = _setup_app(tmp_path, clean_settings, "en")
    profile.rmdir()
    shutil.copytree(DEMO_PROFILE, profile)
    good = (profile / "synonyms.json").read_text(encoding="utf-8")
    (profile / "synonyms.json").write_text("[", encoding="utf-8")
    application = _StoppableApp()
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(
            bot.setup_reload_check(
                FakeUpdate(message=FakeMessage("/x")), _context(app, application)
            )
        )
    (profile / "synonyms.json").write_text(good, encoding="utf-8")
    message = FakeMessage("/start")
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(
            bot.setup_reload_check(
                FakeUpdate(message=message), _context(app, application)
            )
        )
    assert message.replies == [t("bot.setup.ready", "en")]
    assert application.stopped == 1


# --- main(): which app starts ---------------------------------------------------


def _patch_main(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, runs: list[Any]
) -> None:
    from telegram.ext import Application

    monkeypatch.setattr(bot, "Settings", lambda: settings)
    monkeypatch.setattr(bot, "configure_logging", lambda: None)

    def fake_run_polling(self: Any, *args: Any, **kwargs: Any) -> None:
        runs.append((self, kwargs))

    monkeypatch.setattr(Application, "run_polling", fake_run_polling)


def test_main_starts_the_setup_app_without_building_the_service(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    settings = clean_settings(profile_dir=str(profile), owner_telegram_id=OWNER)
    runs: list[Any] = []
    _patch_main(monkeypatch, settings, runs)

    def no_service(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("setup mode must not load any profile (legacy data)")

    monkeypatch.setattr(bot.JobService, "from_settings", no_service)
    monkeypatch.setattr(bot.bridge, "register", no_service)
    bot.main()
    (app, kwargs) = runs[0]
    assert len(runs) == 1
    assert "setup" in _commands(app)
    assert kwargs.get("close_loop") is False


def test_main_with_a_complete_profile_starts_the_full_app(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "me"
    shutil.copytree(DEMO_PROFILE, profile)
    # An owner is set: no claim app (beta3-ux T1), straight to the full app.
    settings = clean_settings(profile_dir=str(profile), owner_telegram_id=OWNER)
    runs: list[Any] = []
    _patch_main(monkeypatch, settings, runs)
    built: list[Settings] = []

    def fake_service(s: Settings, **kwargs: Any) -> Any:
        built.append(s)
        return SimpleNamespace(
            threshold=65,
            repository=None,
            migrate_portals_config=lambda path: False,
        )

    monkeypatch.setattr(bot.JobService, "from_settings", fake_service)
    monkeypatch.setattr(bot, "build_app", lambda s, svc: _FakeFullApp(runs))
    monkeypatch.setattr(bot.scheduler, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot.bridge, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_menu", lambda *a, **k: None)
    bot.main()
    assert built == [settings]
    assert runs == ["full"]


class _FakeFullApp:
    def __init__(self, runs: list[Any]) -> None:
        self._runs = runs

    def run_polling(self, *args: Any, **kwargs: Any) -> None:
        self._runs.append("full")


def test_main_switches_to_the_full_app_once_the_profile_is_complete(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    settings = clean_settings(profile_dir=str(profile), owner_telegram_id=OWNER)
    runs: list[Any] = []
    _patch_main(monkeypatch, settings, runs)
    from telegram.ext import Application

    def setup_run(self: Any, *args: Any, **kwargs: Any) -> None:
        runs.append("setup")
        profile.rmdir()
        shutil.copytree(DEMO_PROFILE, profile)
        self.bot_data[bot._SETUP_DONE_KEY] = True

    monkeypatch.setattr(Application, "run_polling", setup_run)
    monkeypatch.setattr(
        bot.JobService,
        "from_settings",
        lambda s, **k: SimpleNamespace(
            threshold=65,
            repository=None,
            migrate_portals_config=lambda path: False,
        ),
    )
    monkeypatch.setattr(bot, "build_app", lambda s, svc: _FakeFullApp(runs))
    monkeypatch.setattr(bot.scheduler, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot.bridge, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_menu", lambda *a, **k: None)
    bot.main()
    assert runs == ["setup", "full"]


def test_main_reuses_the_validated_service_and_replays_the_batch(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    settings = clean_settings(profile_dir=str(profile), owner_telegram_id=OWNER)
    runs: list[Any] = []
    _patch_main(monkeypatch, settings, runs)
    from telegram.ext import Application

    validated = SimpleNamespace(
        threshold=65,
        repository=None,
        migrate_portals_config=lambda path: False,
    )
    pending = [_real_update(7, "/status")]

    def setup_run(self: Any, *args: Any, **kwargs: Any) -> None:
        runs.append("setup")
        profile.rmdir()
        shutil.copytree(DEMO_PROFILE, profile)
        self.bot_data[bot._SETUP_DONE_KEY] = True
        self.bot_data[bot._SETUP_SERVICE_KEY] = validated
        self.bot_data[bot._SETUP_REPLAY_KEY] = pending

    def no_second_load(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the service validated in setup mode is reused")

    services: list[Any] = []
    replayed: list[Any] = []
    monkeypatch.setattr(Application, "run_polling", setup_run)
    monkeypatch.setattr(bot.JobService, "from_settings", no_second_load)
    monkeypatch.setattr(
        bot, "build_app", lambda s, svc: services.append(svc) or _FakeFullApp(runs)
    )
    monkeypatch.setattr(bot.scheduler, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot.bridge, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_menu", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_replay", lambda app, u: replayed.extend(u))
    bot.main()
    assert runs == ["setup", "full"]
    assert services == [validated]
    assert replayed == pending


# --- the setup-mode bridge ------------------------------------------------------


@pytest.fixture()
def setup_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> Any:
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=OWNER,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
    )
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot_data={})
    handle = bridge.register_setup(app, settings)
    assert handle is not None
    yield SimpleNamespace(handle=handle, app=app, tmp=tmp_path)
    handle.stop()


def _url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def _auth(value: str = SECRET) -> dict[str, str]:
    return {bridge.SECRET_HEADER: value}


def test_setup_bridge_config_is_paused_with_no_portals(setup_bridge: Any) -> None:
    resp = httpx.get(_url(setup_bridge.handle.port, "/config"), headers=_auth())
    assert resp.status_code == 200
    # beta3-ux T2: "setup" lets the popup say to finish /setup first.
    assert resp.json() == {"paused": True, "enabledPortals": [], "setup": True}


def test_setup_bridge_suggests_no_searches(setup_bridge: Any) -> None:
    resp = httpx.get(
        _url(setup_bridge.handle.port, "/searches/suggested"), headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json() == {"searches": []}


@pytest.mark.parametrize("path", ["/bridge", "/scan-report"])
def test_setup_bridge_rejects_ingestion_with_a_clear_error(
    setup_bridge: Any, path: str
) -> None:
    resp = httpx.post(
        _url(setup_bridge.handle.port, path),
        json={"beacon": "b", "jobs": []},
        headers=_auth(),
    )
    assert resp.status_code == 503
    assert "profile not set up" in resp.json()["error"]
    assert not (setup_bridge.tmp / "data").exists()  # nothing stored


def test_setup_bridge_keeps_auth_and_unknown_paths(setup_bridge: Any) -> None:
    port = setup_bridge.handle.port
    assert httpx.get(_url(port, "/config")).status_code == 401
    assert httpx.get(_url(port, "/config"), headers=_auth("bad")).status_code == 401
    assert httpx.post(_url(port, "/bridge"), json={}).status_code == 401
    assert httpx.get(_url(port, "/nope"), headers=_auth()).status_code == 404


def test_setup_bridge_frees_its_port_on_shutdown(setup_bridge: Any) -> None:
    assert setup_bridge.app.post_shutdown is not None
    asyncio.run(setup_bridge.app.post_shutdown(setup_bridge.app))
    with pytest.raises(RuntimeError):
        _ = setup_bridge.handle.port


def test_setup_bridge_disabled_without_a_secret(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot_data={})
    settings = clean_settings(owner_telegram_id=OWNER)
    assert bridge.register_setup(app, settings) is None


# --- B1: Confirm itself switches to the full app ---------------------------------


def test_confirm_switches_to_the_full_app_with_no_further_message(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Right after a successful /setup Confirm in setup mode, main() starts the
    # full app: its bridge serves every portal and the suggested
    # searches although the owner sends nothing else.
    from telegram.ext import Application

    from tests.test_onboarding import Driver, _answer_everything

    monkeypatch.chdir(tmp_path)  # config.local.toml and data/ live here
    profile = tmp_path / "profiles" / "me"
    profile.mkdir(parents=True)
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=OWNER,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
    )
    runs: list[Any] = []
    _patch_main(monkeypatch, settings, runs)

    class _SettingsFactory(type):  # bot.Settings() → these settings
        def __call__(cls, *args: Any, **kwargs: Any) -> Settings:
            return settings

        def __instancecheck__(cls, obj: Any) -> bool:
            return isinstance(obj, Settings)

    monkeypatch.setattr(bot, "Settings", _SettingsFactory("Settings", (), {}))
    confirm_replies: list[str] = []
    served: dict[str, Any] = {}
    replayed: list[Any] = []

    def setup_run(self: Any, *args: Any, **kwargs: Any) -> None:
        runs.append("setup")
        application = _StoppableApp()
        context = SimpleNamespace(
            bot_data=self.bot_data, user_data={}, args=[], application=application
        )
        d = Driver(context, full=False, switch=True)
        d.start()
        _answer_everything(d, "en")
        query = d.press("confirm")
        confirm_replies.extend(query.message.replies)
        assert application.stopped == 1  # polling stops: main() goes on

    handles: list[Any] = []

    def register(app: Any, s: Settings, service: Any) -> Any:
        handle = real_register(
            SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={}),
            s,
            service,
        )
        handles.append(handle)
        return handle

    class _FullApp:
        def run_polling(self, *args: Any, **kwargs: Any) -> None:
            runs.append("full")
            base = f"http://127.0.0.1:{handles[0].port}"
            for path in ("/config", "/searches/suggested"):
                served[path] = httpx.get(base + path, headers=_auth(), timeout=10)

    real_register = bridge.register
    monkeypatch.setattr(Application, "run_polling", setup_run)
    monkeypatch.setattr(bot.bridge, "register_setup", lambda *a, **k: None)
    monkeypatch.setattr(bot.bridge, "register", register)
    monkeypatch.setattr(bot, "build_app", lambda s, svc: _FullApp())
    monkeypatch.setattr(bot.scheduler, "register", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_menu", lambda *a, **k: None)
    monkeypatch.setattr(bot, "register_replay", lambda app, u: replayed.extend(u))
    try:
        bot.main()
    finally:
        for handle in handles:
            handle.stop()
    assert runs == ["setup", "full"]
    # Validated before announcing: "ready" comes after the saved reply.
    assert confirm_replies[-1] == t("bot.setup.ready", "en")
    assert confirm_replies[-2].startswith(t("onboarding.saved", "en"))
    assert replayed == []  # the Confirm was handled, never replayed
    config = served["/config"].json()
    assert config["paused"] is False
    # Portals are enabled only in the extension: /setup wrote no list, so
    # the full app's bridge accepts every registered portal.
    assert config["enabledPortals"] == list(SOURCES)
    searches = served["/searches/suggested"].json()["searches"]
    assert {s["location"] for s in searches} == {"Valencia", "remoto"}


def test_confirm_that_leaves_a_broken_profile_stays_in_setup_mode(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validated before announcing: a profile the Confirm completes but the
    # strict loader rejects never says "ready" nor stops polling.
    from tests.test_onboarding import Driver, _answer_everything

    monkeypatch.chdir(tmp_path)
    app, profile = _setup_app(tmp_path, clean_settings, "en")
    (profile / "synonyms.json").write_text("{broken", encoding="utf-8")
    application = _StoppableApp()
    context = SimpleNamespace(
        bot_data=app.bot_data, user_data={}, args=[], application=application
    )
    d = Driver(context, full=False, switch=True)
    d.start()
    _answer_everything(d, "en")
    d.press("confirm")
    assert application.stopped == 0
    assert not app.bot_data.get(bot._SETUP_DONE_KEY)
    assert t("bot.setup.ready", "en") not in d.replies
    # Confirm's own strict loaders refuse it first: nothing written.
    assert t("onboarding.write_undone", "en") in d.replies[-1]
    assert missing_profile_files(profile) != ()
