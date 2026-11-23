"""beta.4 fixes from the owner's real test (odd/tasks/beta4-polish.md, T1).

S1: the Telegram token is validated by ``init`` and checked at bot startup,
never echoed; S3: the /setup country step proposes the work city's country;
S5: the exported config template has no owner-only notes; S6: a fresh
``init`` turns the AI judge on (``match_mode = "hybrid"``).
"""

import io
import logging
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from telegram.error import InvalidToken

import bot
from cva.config import Settings
from cva.i18n import Lang, t
from cva.setup import telegram_token
from tests.test_export_public import export_public
from tests.test_init_wizard import (
    API_KEY,
    FRESH_ANSWERS,
    TOKEN,
    ScriptedIO,
    local_settings,
    provider_answers,
    read_env,
    wizard,
)
from tests.test_onboarding import _setup_mode
from tests.test_onboarding_beta3 import _to_background

REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_CHECK = telegram_token.check_token
GEMINI_LIKE = "AIzaSyFakeGeminiKeyForTests_0123456789ab"
# Spelled in two parts: the export scan refuses the joined path anywhere.
OWNER_DEPLOY_DIR = "deploy" + "/"
TOKEN_LIKE_KEY = "987654321:AAThisIsAnApiKeyShapedLikeAToken_xyz"


# --- S1: init validates the pasted token --------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_a_value_that_is_not_a_token_is_refused_and_asked_again(
    tmp_path: Path, lang: Lang
) -> None:
    answers = [lang, *FRESH_ANSWERS[1:]]
    scripted = ScriptedIO(answers, [GEMINI_LIKE, "12345:short", TOKEN])
    assert wizard(tmp_path, scripted) == 0
    assert scripted.transcript.count(t("wizard.token_invalid", lang)) == 2
    assert read_env(tmp_path / ".env")["CVA_TELEGRAM_TOKEN"] == TOKEN
    assert GEMINI_LIKE not in scripted.transcript
    assert TOKEN not in scripted.transcript


def test_the_invalid_token_message_says_where_to_copy_it() -> None:
    assert t("wizard.token_invalid", "en") == (
        "  This does not look like a Telegram bot token: copy it from @BotFather "
        "(/mybots → your bot → API Token); it looks like 1234567890:AA…"
    )


def test_the_ai_key_pasted_as_the_token_is_refused(tmp_path: Path) -> None:
    scripted = ScriptedIO(
        provider_answers("anthropic"), [TOKEN_LIKE_KEY, TOKEN_LIKE_KEY, TOKEN]
    )
    assert wizard(tmp_path, scripted) == 0
    assert t("wizard.token_is_ai_key", "en") in scripted.transcript
    env = read_env(tmp_path / ".env")
    assert env["CVA_TELEGRAM_TOKEN"] == TOKEN
    assert env["ANTHROPIC_API_KEY"] == TOKEN_LIKE_KEY
    assert TOKEN_LIKE_KEY not in scripted.transcript


def test_the_ai_key_already_in_env_is_refused_as_the_token(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        f"CVA_LLM_API_KEY={TOKEN_LIKE_KEY}\nCVA_BRIDGE_SECRET=s\n", encoding="utf-8"
    )
    (tmp_path / "config.local.toml").write_text(
        'llm_provider = "openai_compatible"\n'
        'llm_base_url = "https://api.openai.com/v1"\nllm_model = "m"\n',
        encoding="utf-8",
    )
    # language, provider, preset, model, keep the bridge secret, profile dir,
    # kind, confirm, no secret show. Key: Enter (kept); token: the key, then
    # the real token.
    scripted = ScriptedIO(
        ["", "", "", "", "n", "", "3", "y", "n"], ["", TOKEN_LIKE_KEY, TOKEN]
    )
    assert wizard(tmp_path, scripted) == 0
    assert t("wizard.token_is_ai_key", "en") in scripted.transcript
    assert read_env(tmp_path / ".env")["CVA_TELEGRAM_TOKEN"] == TOKEN


@pytest.mark.parametrize("lang", ["en", "es"])
def test_a_valid_token_is_shown_only_by_its_last_four_characters(
    tmp_path: Path, lang: Lang
) -> None:
    scripted = ScriptedIO([lang, *FRESH_ANSWERS[1:]], [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    assert t("wizard.token_saved", lang, tail="…" + TOKEN[-4:]) in scripted.transcript
    assert TOKEN not in scripted.transcript
    assert TOKEN[-8:] not in scripted.transcript


def test_the_token_step_lists_the_botfather_steps_in_order() -> None:
    for lang in ("en", "es"):
        text = t("wizard.botfather", lang)
        steps = [text.index(f"    {n}. ") for n in range(1, 6)]
        assert steps == sorted(steps)
        assert "@BotFather" in text and "/newbot" in text and '"bot"' in text


# --- S1: the bot checks the token before printing anything --------------------


def _main_with_token_check(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    status: str | None,
) -> list[str]:
    """``bot.main`` with ``settings``; ``status`` fakes the check (None runs
    the real one). Returns what started (claim code, polling)."""
    started: list[str] = []
    monkeypatch.setattr(bot, "Settings", lambda: settings)
    monkeypatch.setattr(bot, "configure_logging", lambda: None)
    if status is None:
        monkeypatch.setattr(telegram_token, "check_token", _REAL_CHECK)
    else:
        monkeypatch.setattr(telegram_token, "check_token", lambda token: status)
    monkeypatch.setattr(
        bot, "announce_claim_code", lambda *a, **k: started.append("code") or False
    )
    from telegram.ext import Application

    def fake_run(self: Any, *args: Any, **kwargs: Any) -> None:
        started.append("polling")

    monkeypatch.setattr(Application, "run_polling", fake_run)
    return started


def _rejecting_get_me(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telegram answers 401/404 to getMe: PTB raises InvalidToken quoting it."""
    from telegram import Bot

    async def initialize(self: Any) -> None:
        raise InvalidToken(f"The token `{self.token}` was rejected by the server.")

    monkeypatch.setattr(Bot, "initialize", initialize)


@pytest.mark.parametrize("lang", ["en", "es"])
def test_a_rejected_token_stops_the_bot_with_one_plain_line(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lang: Lang,
) -> None:
    settings = clean_settings(
        profile_dir=str(tmp_path / "me"),
        CVA_TELEGRAM_TOKEN=GEMINI_LIKE,
        ui_language=lang,
    )
    started = _main_with_token_check(monkeypatch, settings, None)
    _rejecting_get_me(monkeypatch)
    with pytest.raises(SystemExit) as exit_info:
        bot.main()
    assert exit_info.value.code not in (0, None)
    err = capsys.readouterr()
    assert err.err == t("bot.token_rejected", lang) + "\n"
    assert GEMINI_LIKE not in err.out + err.err
    assert "Traceback" not in err.err
    assert started == []  # no claim code, no polling


def test_the_rejected_token_message_names_the_env_key() -> None:
    assert t("bot.token_rejected", "en") == (
        "Telegram rejected the bot token. Check CVA_TELEGRAM_TOKEN in .env — "
        "it must be the token @BotFather gave you."
    )


def test_a_token_revoked_while_running_is_reported_without_a_traceback(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = clean_settings(
        profile_dir=str(tmp_path / "me"), CVA_TELEGRAM_TOKEN=TOKEN, ui_language="en"
    )
    _main_with_token_check(monkeypatch, settings, "ok")
    from telegram.ext import Application

    def revoked(self: Any, *args: Any, **kwargs: Any) -> None:
        raise InvalidToken(f"The token `{TOKEN}` was rejected by the server.")

    monkeypatch.setattr(Application, "run_polling", revoked)
    with pytest.raises(SystemExit):
        bot.main()
    err = capsys.readouterr().err
    assert err == t("bot.token_rejected", "en") + "\n"


def test_an_unreachable_telegram_keeps_starting_the_bot(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = clean_settings(
        profile_dir=str(tmp_path / "me"), CVA_TELEGRAM_TOKEN=TOKEN
    )
    started = _main_with_token_check(monkeypatch, settings, "unreachable")
    bot.main()
    assert started == ["code", "polling"]  # polling retries the network


def test_an_accepted_token_starts_as_before(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = clean_settings(
        profile_dir=str(tmp_path / "me"), CVA_TELEGRAM_TOKEN=TOKEN
    )
    started = _main_with_token_check(monkeypatch, settings, "ok")
    bot.main()
    assert started == ["code", "polling"]


def test_any_traceback_that_would_show_the_token_is_scrubbed(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = clean_settings(
        profile_dir=str(tmp_path / "me"), CVA_TELEGRAM_TOKEN=TOKEN
    )
    _main_with_token_check(monkeypatch, settings, "ok")
    from telegram.ext import Application

    def boom(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(f"GET https://api.telegram.org/bot{TOKEN}/getUpdates")

    monkeypatch.setattr(Application, "run_polling", boom)
    with pytest.raises(SystemExit):
        bot.main()
    err = capsys.readouterr().err
    assert TOKEN not in err and "<redacted>" in err


def test_log_records_never_carry_the_token() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("beta4.redaction")
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        telegram_token.install_redaction(TOKEN, logger)
        logger.warning("calling /bot%s/getMe", TOKEN)
        try:
            raise InvalidToken(f"The token `{TOKEN}` was rejected by the server.")
        except InvalidToken:
            logger.exception("polling failed")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert TOKEN not in text
    assert text.count("<redacted>") == 2


def test_the_real_check_maps_telegram_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from telegram import Bot
    from telegram.error import NetworkError

    _rejecting_get_me(monkeypatch)
    assert _REAL_CHECK(TOKEN) == "rejected"

    async def offline(self: Any) -> None:
        raise NetworkError("connection refused")

    monkeypatch.setattr(Bot, "initialize", offline)
    assert _REAL_CHECK(TOKEN) == "unreachable"

    async def fine(self: Any) -> None:
        return None

    monkeypatch.setattr(Bot, "initialize", fine)
    assert _REAL_CHECK(TOKEN) == "ok"


# --- S3: the country step proposes the work city's country --------------------


def _to_country(d: Any, city: str, lang: Lang = "en") -> None:
    _to_background(d, lang)
    d.text("Python, SQL")
    d.press("go:1-2")
    d.press("skip")
    d.press("skip")  # no education
    d.press("t:hybrid")
    d.press("done")
    d.text(city)
    assert d.step == "country"


@pytest.mark.parametrize(
    ("lang", "label", "saved"),
    [
        ("en", "Spain (from Barcelona)", "Spain"),
        ("es", "España (por Barcelona)", "España"),
    ],
)
def test_the_country_of_the_work_city_is_one_tap(
    tmp_path: Path, lang: Lang, label: str, saved: str
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)
    _to_country(d, "Barcelona", lang)
    assert [b for b, _ in d.buttons()][:2] == [
        label,
        t("onboarding.btn.country_other", lang),
    ]
    d.press("default")
    assert d.step == "salary"
    assert d.context.user_data[bot.onboarding.STATE_KEY]["a"]["country"] == saved


def test_other_asks_to_type_the_country(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path, "en")
    _to_country(d, "Barcelona")
    d.press("other")
    assert t("onboarding.q.country_other", "en") in d.last
    d.text("Portugal")
    assert d.step == "salary"
    assert d.context.user_data[bot.onboarding.STATE_KEY]["a"]["country"] == "Portugal"


def test_an_unknown_city_proposes_no_country(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path, "en")
    _to_country(d, "Atlantis")
    labels = [b for b, _ in d.buttons()]
    assert not any("(from" in b for b in labels)
    assert t("onboarding.btn.country_other", "en") not in labels
    d.text("Spain")
    assert d.step == "salary"


def test_a_saved_country_is_kept_on_a_rerun(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path, "en")
    _to_country(d, "Barcelona")
    d.context.user_data[bot.onboarding.STATE_KEY]["a"]["country"] = "Andorra"
    d.press("back")
    d.text("Barcelona")
    assert d.step == "country"
    labels = [b for b, _ in d.buttons()]
    assert t("onboarding.btn.keep", "en", value="Andorra") in labels
    assert not any("(from" in b for b in labels)
    d.press("keep")
    assert d.context.user_data[bot.onboarding.STATE_KEY]["a"]["country"] == "Andorra"


# --- S5: the config template has no owner-only notes --------------------------


def test_the_exported_config_template_has_no_owner_notes() -> None:
    text = (REPO_ROOT / "config.local.toml.example").read_text(encoding="utf-8")
    public = export_public.strip_owner_regions(text)
    for owner_only in (OWNER_DEPLOY_DIR, "C:\\", "cv_mirror_dir", "tracker_xlsx_path"):
        assert owner_only not in public, owner_only
    tomllib.loads(public)  # still a valid TOML file
    for doc in ("docs/running.md", "docs/providers.md", "docs/gmail.md"):
        assert doc in public


def test_the_export_scan_refuses_a_link_to_the_owner_deploy_notes(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.toml").write_text(
        f"# see {OWNER_DEPLOY_DIR}README.md §12\n", "utf-8"
    )
    assert export_public.scan(tmp_path) == [
        f"a.toml: points at the owner-only {OWNER_DEPLOY_DIR}readme"
    ]


# --- S6: a fresh install turns the AI judge on ---------------------------------


def test_a_fresh_install_writes_match_mode_hybrid(tmp_path: Path) -> None:
    scripted = ScriptedIO(
        provider_answers("openai_compatible", "2", ""), [API_KEY, TOKEN]
    )
    assert wizard(tmp_path, scripted) == 0
    assert local_settings(tmp_path)["match_mode"] == "hybrid"


@pytest.mark.parametrize("where", ["config.local.toml", "config.toml"])
def test_an_existing_keyword_match_mode_is_kept(tmp_path: Path, where: str) -> None:
    (tmp_path / where).write_text('match_mode = "keyword"\n', encoding="utf-8")
    scripted = ScriptedIO(FRESH_ANSWERS, [TOKEN])
    assert wizard(tmp_path, scripted) == 0
    local = local_settings(tmp_path)
    assert local.get("match_mode", "keyword") == "keyword"
    if where == "config.toml":
        assert "match_mode" not in local


def test_a_match_mode_from_the_environment_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVA_MATCH_MODE", "keyword")
    assert wizard(tmp_path, ScriptedIO(FRESH_ANSWERS, [TOKEN])) == 0
    assert "match_mode" not in local_settings(tmp_path)


def test_a_saved_token_that_is_not_a_token_is_not_kept_silently(
    tmp_path: Path,
) -> None:
    """The owner's case: an AI key saved as the token. Re-running init and
    pressing Enter must not keep it; the real token is asked for."""
    (tmp_path / ".env").write_text(
        "CVA_TELEGRAM_TOKEN=AQ.not-a-telegram-token\nCVA_BRIDGE_SECRET=s\n",
        encoding="utf-8",
    )
    # Same answers as the .env test above; token: Enter (refused), then the
    # real token.
    scripted = ScriptedIO(["", "", "n", "", "3", "y", "n"], ["", TOKEN])
    assert wizard(tmp_path, scripted) == 0
    assert t("wizard.token_saved_invalid", "en") in scripted.transcript
    assert read_env(tmp_path / ".env")["CVA_TELEGRAM_TOKEN"] == TOKEN
