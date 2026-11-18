"""App-config tests: env overrides TOML, fail-fast secrets, no secrets in TOML."""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from cva.config import Settings


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated cwd with a config.toml, so the repo file never leaks into tests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_config(config_dir: Path, body: str) -> None:
    (config_dir / "config.toml").write_text(body, encoding="utf-8")


def _secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok-test")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-test")


def test_env_overrides_toml(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(config_dir, "threshold = 50\n")
    _secret_env(monkeypatch)
    monkeypatch.setenv("CVA_THRESHOLD", "70")

    settings = Settings(_env_file=None)

    assert settings.threshold == 70


def test_toml_is_base_layer_with_sane_defaults(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_dir, "threshold = 50\nchecks_per_day = 2\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.threshold == 50
    assert settings.checks_per_day == 2
    assert settings.llm_base_url == "https://api.openai.com/v1"
    assert settings.llm_model


def test_beacon_global_daily_cap_default_and_env_override(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Global anti-ban cap across beacons (design D11): default + env override."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.beacon_global_daily_cap == 30

    monkeypatch.setenv("CVA_BEACON_GLOBAL_DAILY_CAP", "7")
    settings = Settings(_env_file=None)
    assert settings.beacon_global_daily_cap == 7


def test_llm_disable_thinking_defaults_false_and_env_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v3 part C: optional thinking kill-switch for GLM-style endpoints."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.llm_disable_thinking is False

    monkeypatch.setenv("CVA_LLM_DISABLE_THINKING", "true")
    settings = Settings(_env_file=None)
    assert settings.llm_disable_thinking is True


def test_llm_429_retry_wait_seconds_defaults_25_and_env_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single bounded 429 retry wait; env override; 0 disables the retry."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.llm_429_retry_wait_seconds == 25

    monkeypatch.setenv("CVA_LLM_429_RETRY_WAIT_SECONDS", "40")
    settings = Settings(_env_file=None)
    assert settings.llm_429_retry_wait_seconds == 40

    monkeypatch.setenv("CVA_LLM_429_RETRY_WAIT_SECONDS", "0")
    settings = Settings(_env_file=None)
    assert settings.llm_429_retry_wait_seconds == 0


def test_auto_analyze_defaults_true_and_toml_controls_it(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto-triage D8: bridged jobs auto-analyze by default; TOML can opt out."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.auto_analyze is True

    _write_config(config_dir, "auto_analyze = false\n")
    settings = Settings(_env_file=None)
    assert settings.auto_analyze is False

    monkeypatch.setenv("CVA_AUTO_ANALYZE", "true")
    settings = Settings(_env_file=None)
    assert settings.auto_analyze is True


def test_cv_mirror_dir_defaults_none_and_toml_controls_it(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto-triage D8: mirror root is a plain TOML string; unset → None."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.cv_mirror_dir is None

    _write_config(config_dir, 'cv_mirror_dir = "/tmp/cv-mirror"\n')
    settings = Settings(_env_file=None)
    assert settings.cv_mirror_dir == "/tmp/cv-mirror"

    monkeypatch.setenv("CVA_CV_MIRROR_DIR", "/other/mirror")
    settings = Settings(_env_file=None)
    assert settings.cv_mirror_dir == "/other/mirror"


def test_missing_telegram_token_fails_fast_naming_secret(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_dir, "threshold = 65\n")
    monkeypatch.delenv("CVA_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-test")

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert "CVA_TELEGRAM_TOKEN" in str(excinfo.value)


def test_settings_read_no_secret_from_toml(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        config_dir,
        'telegram_token = "toml-leak"\n'
        'CVA_TELEGRAM_TOKEN = "toml-leak"\n'
        'llm_api_key = "toml-leak"\n',
    )
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("CVA_LLM_API_KEY", "env-key")

    settings = Settings(_env_file=None)

    assert settings.telegram_token.get_secret_value() == "env-token"
    assert settings.llm_api_key is not None  # narrows SecretStr | None for mypy
    assert settings.llm_api_key.get_secret_value() == "env-key"


def test_bridge_secret_env_only_and_toml_stripped(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """beacon-bridge D6: bridge secret follows the env-only secret contract."""
    _write_config(
        config_dir,
        'bridge_secret = "toml-leak"\n'
        'CVA_BRIDGE_SECRET = "toml-leak"\n'
        "bridge_port = 9999\n",
    )
    _secret_env(monkeypatch)
    monkeypatch.setenv("CVA_BRIDGE_SECRET", "env-secret")

    settings = Settings(_env_file=None)

    assert settings.bridge_secret.get_secret_value() == "env-secret"
    assert settings.bridge_port == 9999  # non-secret port reads from TOML


def test_bridge_secret_unset_defaults_empty_and_port_default(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset secret → "" → endpoint disabled at startup (fail-safe, D6)."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)
    monkeypatch.delenv("CVA_BRIDGE_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.bridge_secret.get_secret_value() == ""
    assert settings.bridge_port == 8765


# --- Notification pacing (notification-pacing design D8) --------------------


def test_notification_pacing_keys_defaults(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: config.toml without the three keys → delay=45, gate=None, max=5."""
    from cva.config import _SECRET_TOML_KEYS

    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.analyze_delay_seconds == 45
    assert settings.notify_min_score is None
    assert settings.analyze_retry_max == 5
    # Non-secret TOML-only keys: never registered in the secret-guard set.
    assert "analyze_delay_seconds" not in _SECRET_TOML_KEYS
    assert "notify_min_score" not in _SECRET_TOML_KEYS
    assert "analyze_retry_max" not in _SECRET_TOML_KEYS


def test_notification_pacing_keys_toml_base_and_env_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOML is the base layer; any CVA_<FIELD> env var overrides it."""
    _write_config(
        config_dir,
        "analyze_delay_seconds = 10\nnotify_min_score = 70\nanalyze_retry_max = 2\n",
    )
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.analyze_delay_seconds == 10
    assert settings.notify_min_score == 70
    assert settings.analyze_retry_max == 2

    monkeypatch.setenv("CVA_ANALYZE_DELAY_SECONDS", "0")
    monkeypatch.setenv("CVA_NOTIFY_MIN_SCORE", "55")
    monkeypatch.setenv("CVA_ANALYZE_RETRY_MAX", "0")
    settings = Settings(_env_file=None)
    assert settings.analyze_delay_seconds == 0
    assert settings.notify_min_score == 55
    assert settings.analyze_retry_max == 0


def test_notify_min_score_none_stays_none_not_threshold_snapshot(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8 coherence: an unset notify_min_score stays None at startup — it is
    NEVER materialized from threshold; the gate resolves the live threshold
    at call time (service-side, notification-pacing design D8)."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.threshold == 65
    assert settings.notify_min_score is None


def test_notify_gray_zone_default_15_non_secret_toml_and_env_overridable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gray-zone amendment: notify_gray_zone defaults to 15, is TOML-only
    non-secret (never in the guard set), and accepts env overrides like every
    other non-secret field. 0 = zone disabled (pure threshold behavior)."""
    from cva.config import _SECRET_TOML_KEYS

    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.notify_gray_zone == 15
    assert "notify_gray_zone" not in _SECRET_TOML_KEYS

    _write_config(config_dir, "notify_gray_zone = 0\n")
    settings = Settings(_env_file=None)
    assert settings.notify_gray_zone == 0  # disabled zone stays an int 0

    monkeypatch.setenv("CVA_NOTIFY_GRAY_ZONE", "30")
    settings = Settings(_env_file=None)
    assert settings.notify_gray_zone == 30


# --- LLM provider seam (claude-code-llm-provider) ---------------------------


def test_llm_provider_defaults_claude_code_and_is_overridable(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.llm_provider == "claude_code"

    _write_config(config_dir, 'llm_provider = "anthropic"\n')
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "anthropic"

    monkeypatch.setenv("CVA_LLM_PROVIDER", "openai_compatible")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "openai_compatible"


def test_claude_code_settings_defaults_and_toml_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.claude_code_path == "claude"
    assert settings.claude_code_model is None
    assert settings.claude_code_timeout_seconds == 180

    _write_config(
        config_dir,
        'claude_code_path = "C:\\\\Users\\\\me\\\\.local\\\\bin\\\\claude.exe"\n'
        'claude_code_model = "haiku"\n'
        "claude_code_timeout_seconds = 300\n",
    )
    settings = Settings(_env_file=None)
    assert settings.claude_code_path == "C:\\Users\\me\\.local\\bin\\claude.exe"
    assert settings.claude_code_model == "haiku"
    assert settings.claude_code_timeout_seconds == 300


@pytest.mark.parametrize("value", [9, 1801])
def test_claude_code_timeout_seconds_rejects_out_of_bounds_values(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    """Bounded [10, 1800]s (robustness follow-up): an unbounded value could
    hang forever (too low) or exhaust the caller's own timeout budget
    silently (too high)."""
    _write_config(config_dir, f"claude_code_timeout_seconds = {value}\n")
    _secret_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", [10, 1800])
def test_claude_code_timeout_seconds_accepts_boundary_values(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, value: int
) -> None:
    _write_config(config_dir, f"claude_code_timeout_seconds = {value}\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.claude_code_timeout_seconds == value


def test_llm_api_key_is_optional_and_none_without_env(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claude_code provider never needs an API key: the Settings field
    itself is optional now (OpenAiCompatibleLlmClient.from_settings is where
    a missing key fails clearly, for the openai_compatible provider only)."""
    _write_config(config_dir, 'llm_provider = "claude_code"\n')
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok-test")
    monkeypatch.delenv("CVA_LLM_API_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "claude_code"
    assert settings.llm_api_key is None


# --- LLM offer judge (llm-offer-judge design) --------------------------------


def test_extraction_and_judge_model_default_none_and_toml_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-step model selection: both optional, None keeps the provider's
    single configured model for that step."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.extraction_model is None
    assert settings.judge_model is None

    _write_config(config_dir, 'extraction_model = "haiku"\njudge_model = "sonnet"\n')
    settings = Settings(_env_file=None)
    assert settings.extraction_model == "haiku"
    assert settings.judge_model == "sonnet"

    monkeypatch.setenv("CVA_EXTRACTION_MODEL", "haiku-env")
    monkeypatch.setenv("CVA_JUDGE_MODEL", "sonnet-env")
    settings = Settings(_env_file=None)
    assert settings.extraction_model == "haiku-env"
    assert settings.judge_model == "sonnet-env"


def test_match_mode_defaults_keyword_and_toml_overrides(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default 'keyword' is today's unchanged behavior; hybrid/judge are opt-in."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.match_mode == "keyword"

    _write_config(config_dir, 'match_mode = "hybrid"\n')
    settings = Settings(_env_file=None)
    assert settings.match_mode == "hybrid"


@pytest.mark.parametrize("value", ["judge", "bogus"])
def test_match_mode_rejects_unknown_value(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """'judge' (judge-only, no keyword breakdown) is not implemented — it is
    removed from the allowed values until it lands, so setting it is a
    clear config validation error instead of a silent/raising runtime path
    (robustness follow-up)."""
    _write_config(config_dir, f'match_mode = "{value}"\n')
    _secret_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- Layered TOML: config.toml + config.local.toml (shared-config) ----------


def _write_local_config(config_dir: Path, body: str) -> None:
    (config_dir / "config.local.toml").write_text(body, encoding="utf-8")


def test_config_local_toml_overrides_tracked_config_toml(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.local.toml (machine-specific/personal) overrides config.toml
    (shared/tracked) — the shared-config layering design."""
    _write_config(config_dir, "threshold = 65\nowner_telegram_id = 0\n")
    _write_local_config(config_dir, "owner_telegram_id = 999\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.threshold == 65  # unset locally: tracked value stands
    assert settings.owner_telegram_id == 999  # overridden by the local layer


def test_env_overrides_config_local_toml(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CVA_* env vars keep overriding both TOML layers, local included."""
    _write_config(config_dir, "threshold = 65\n")
    _write_local_config(config_dir, "threshold = 50\n")
    _secret_env(monkeypatch)
    monkeypatch.setenv("CVA_THRESHOLD", "70")

    settings = Settings(_env_file=None)

    assert settings.threshold == 70


def test_absent_config_local_toml_is_fine(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing config.local.toml is not an error: the tracked layer alone
    still produces valid settings (machine with no local overrides)."""
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)
    assert not (config_dir / "config.local.toml").exists()

    settings = Settings(_env_file=None)

    assert settings.threshold == 65


def test_secret_keys_in_config_local_toml_are_refused_too(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env-only secret contract applies to BOTH TOML layers: a secret
    placed in config.local.toml must never leak into Settings either."""
    _write_config(config_dir, "threshold = 65\n")
    _write_local_config(
        config_dir,
        'telegram_token = "local-leak"\n'
        'CVA_TELEGRAM_TOKEN = "local-leak"\n'
        'llm_api_key = "local-leak"\n',
    )
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("CVA_LLM_API_KEY", "env-key")

    settings = Settings(_env_file=None)

    assert settings.telegram_token.get_secret_value() == "env-token"
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "env-key"


def test_round_summary_default_true_toml_false_and_env_override(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-summary amendment: round_summary defaults to true (the owner wants
    the per-payload recap), is TOML-controllable + env-overridable, and is a
    non-secret plain bool (never in the secret-guard set). false → exactly the
    silent pre-amendment behavior."""
    from cva.config import _SECRET_TOML_KEYS

    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)
    assert settings.round_summary is True
    assert "round_summary" not in _SECRET_TOML_KEYS

    _write_config(config_dir, "round_summary = false\n")
    settings = Settings(_env_file=None)
    assert settings.round_summary is False

    monkeypatch.setenv("CVA_ROUND_SUMMARY", "true")
    settings = Settings(_env_file=None)
    assert settings.round_summary is True


def test_cover_letter_review_default_true_toml_false_and_env_override(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cover_letter_review defaults to true, is TOML + env controllable and a
    non-secret plain bool."""
    from cva.config import _SECRET_TOML_KEYS

    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.cover_letter_review is True
    assert "cover_letter_review" not in _SECRET_TOML_KEYS

    _write_config(config_dir, "cover_letter_review = false\n")
    assert Settings(_env_file=None).cover_letter_review is False

    monkeypatch.setenv("CVA_COVER_LETTER_REVIEW", "true")
    assert Settings(_env_file=None).cover_letter_review is True


def test_clean_settings_toml_file_override_isolates_from_repo_config(
    clean_settings: Callable[..., Settings],
) -> None:
    """Regression (shared-config T3): the repo's tracked config.toml sets
    llm_provider="claude_code" as a shared production value. clean_settings
    must still isolate every other test from it via its toml_file override
    (Settings.settings_customise_sources), not silently leak it in."""
    settings = clean_settings()
    assert settings.match_mode == "keyword"  # config.toml sets "hybrid"


# --- gmail-sync (M1) ---------------------------------------------------------


def test_gmail_defaults_are_off_and_hourly(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(config_dir, "threshold = 65\n")
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.gmail_enabled is False
    assert settings.gmail_poll_minutes == 60
    assert settings.gmail_credentials_path.endswith("gmail_credentials.json")
    assert settings.gmail_token_path.endswith("gmail_token.json")


def test_gmail_keys_load_from_toml(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(
        config_dir,
        "gmail_enabled = true\ngmail_poll_minutes = 45\n"
        "gmail_token_path = '~/tok.json'\n",
    )
    _secret_env(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.gmail_enabled is True
    assert settings.gmail_poll_minutes == 45
    assert settings.gmail_token_path == "~/tok.json"


@pytest.mark.parametrize("minutes", [0, 29, -5])
def test_gmail_poll_minutes_below_the_minimum_is_rejected(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, minutes: int
) -> None:
    _write_config(config_dir, f"gmail_poll_minutes = {minutes}\n")
    _secret_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_enabled_portals_and_paused_defaults_are_legacy(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings()
    assert settings.enabled_portals is None  # None = every registered portal
    assert settings.paused is False


def test_enabled_portals_accepts_registered_ids(
    clean_settings: Callable[..., Settings],
) -> None:
    assert clean_settings(enabled_portals=["linkedin", "infojobs"]).enabled_portals == [
        "linkedin",
        "infojobs",
    ]


def test_unknown_enabled_portal_is_a_clear_startup_error(
    clean_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="nopeportal"):
        clean_settings(enabled_portals=["linkedin", "nopeportal"])


@pytest.mark.parametrize(
    "key", ["CVA_LLM_API_KEY", "CVA_TELEGRAM_TOKEN", "CVA_BRIDGE_SECRET"]
)
def test_alias_named_secret_in_toml_is_never_read(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """A secret written under its env alias name in TOML is ignored too."""
    _write_config(config_dir, f'{key} = "from-toml"\n')
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok-env")
    monkeypatch.delenv("CVA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CVA_BRIDGE_SECRET", raising=False)

    settings = Settings(_env_file=None)

    assert settings.llm_api_key is None
    assert settings.bridge_secret.get_secret_value() == ""
    assert settings.telegram_token.get_secret_value() == "tok-env"
