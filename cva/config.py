"""Typed application settings: layered TOML + environment overrides.

Non-secret settings load from two TOML layers, later overriding earlier:
tracked ``config.toml`` (shared bot settings) then gitignored
``config.local.toml`` (machine-specific/personal values — see
``config.local.toml.example``); a missing ``config.local.toml`` is fine.
Every secret (``CVA_TELEGRAM_TOKEN``, ``CVA_LLM_API_KEY``,
``ANTHROPIC_API_KEY``) comes from the
environment only — never from either TOML file. Any ``CVA_<FIELD>``
environment variable overrides both TOML layers.
"""

import locale
import os
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from cva.cvgen.fonts import DEFAULT_CV_FONT, CvFont
from cva.i18n import Lang
from cva.ingest.sources import SOURCES

_SECRET_TOML_KEYS: frozenset[str] = frozenset(
    {
        "telegram_token",
        "llm_api_key",
        "CVA_TELEGRAM_TOKEN",
        "CVA_LLM_API_KEY",
        "anthropic_api_key",
        "ANTHROPIC_API_KEY",
        "bridge_secret",
        "CVA_BRIDGE_SECRET",
    }
)


class _NoSecretTomlSource(TomlConfigSettingsSource):
    """TOML base layer that refuses to supply secret fields (env-only by contract).

    ``toml_file``, when given, overrides ``model_config["toml_file"]`` for
    this instance only (tests: an isolated/nonexistent path instead of the
    real ``config.toml`` / ``config.local.toml``) — the installed
    pydantic-settings 2.15 ``BaseSettings.__init__`` has no constructor
    kwarg for this, so ``Settings.settings_customise_sources`` below pulls
    it out of the init kwargs itself.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        toml_file: str | tuple[str, ...] | None = None,
    ) -> None:
        if toml_file is None:
            super().__init__(settings_cls)
        else:
            super().__init__(settings_cls, toml_file=toml_file)
        # The parent copied the TOML data into ``init_kwargs`` (what actually
        # feeds validation), so strip both: popping only ``toml_data`` let an
        # alias-named key (``CVA_LLM_API_KEY = ...``) slip through.
        for key in _SECRET_TOML_KEYS:
            self.toml_data.pop(key, None)
            self.init_kwargs.pop(key, None)


# Default model of the native Anthropic provider. Model ids change over time:
# re-check https://docs.anthropic.com/en/docs/about-claude/models and override
# with ``anthropic_model`` (or the per-step extraction_model/judge_model).
DEFAULT_ANTHROPIC_MODEL: Final[str] = "claude-sonnet-4-5"

# Default notify gate of the backend profile; JobService shares it.
DEFAULT_BACKEND_THRESHOLD: Final[int] = 50


def system_ui_language() -> Lang:
    """The UI language of the system locale (``ui_language = "auto"``).

    ``LC_ALL``, ``LC_MESSAGES`` and ``LANG`` are checked in that order (first
    non-empty wins), then ``locale.getlocale()``. A value starting with ``es``
    gives ``"es"``; anything else, or no locale at all, gives ``"en"``.
    """
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "").strip()
        if value:
            return "es" if value.lower().startswith("es") else "en"
    try:
        value = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        value = ""
    return "es" if value.lower().startswith("es") else "en"


class Settings(BaseSettings):
    """Typed layered settings consumed by all cva modules."""

    model_config = SettingsConfigDict(
        env_prefix="CVA_",
        env_file=".env",
        # Layered TOML (shared-config): pydantic-settings reads each file in
        # order and shallow-merges later keys over earlier ones (a missing
        # file is silently skipped — see TomlConfigSettingsSource._read_files
        # in the installed pydantic-settings 2.15), so config.local.toml
        # overrides config.toml, both flat (no nested tables here).
        toml_file=("config.toml", "config.local.toml"),
        extra="ignore",
    )

    # Secrets: env-only. The validation aliases force env lookups by exact name
    # and the TOML source strips these keys, so config.toml can never provide them.
    telegram_token: SecretStr = Field(
        validation_alias=AliasChoices("CVA_TELEGRAM_TOKEN")
    )
    # Optional: required only by the openai_compatible provider (enforced with
    # a clear error at OpenAiCompatibleLlmClient.from_settings, not here) —
    # the claude_code provider never needs an API key.
    llm_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("CVA_LLM_API_KEY")
    )

    # Native Anthropic API key (provider "anthropic"): env-only, by its
    # standard name, never from TOML. Optional here; AnthropicLlmClient
    # .from_settings is the fail-fast boundary.
    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY")
    )

    # LLM provider seam: "claude_code" (default) routes completions through
    # the user's own installed `claude` CLI in headless mode, billed against
    # their Claude subscription; "anthropic" calls the Anthropic Messages API
    # directly (ANTHROPIC_API_KEY); "openai_compatible" uses the generic HTTP
    # client for any OpenAI-style endpoint (CVA_LLM_API_KEY).
    llm_provider: Literal["openai_compatible", "claude_code", "anthropic"] = (
        "claude_code"
    )
    # Anthropic provider default model (see DEFAULT_ANTHROPIC_MODEL).
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL

    # LLM endpoint (provider-swappable via env, OpenAI-compatible defaults).
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    # GLM/OpenAI-compatible extension: some endpoints ignore the JSON-schema
    # response format's thinking defaults; this sends "thinking": disabled.
    llm_disable_thinking: bool = False
    # One bounded retry for transient LLM HTTP 429 (the D3 no-retry rule
    # covers LinkedIn walls, not the LLM endpoint). 0 disables the retry.
    llm_429_retry_wait_seconds: int = 25

    # Claude Code CLI provider settings (used only when llm_provider ==
    # "claude_code"). claude_code_path may be a full path (e.g. on Windows,
    # where a scheduled task's PATH may not include the user's install dir).
    claude_code_path: str = "claude"
    claude_code_model: str | None = None
    # Bounded [10, 1800]s (robustness follow-up): too low can never complete
    # a real call; unbounded high silently swallows an unresponsive CLI far
    # past any reasonable caller-side budget.
    claude_code_timeout_seconds: int = Field(default=180, ge=10, le=1800)
    # The `claude` child never inherits ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN
    # (they would silently switch the CLI from the subscription to API
    # billing). Set true only to deliberately let the CLI use them.
    claude_code_pass_api_key: bool = False

    # Global kill switch default. The persistent Telegram toggle (/pausa,
    # /reanudar, stored in app_state) is the source of truth at runtime: this
    # value only applies until someone has used the toggle (precedence:
    # stored toggle > this setting). While paused the bot runs no beacon
    # sweep, no auto-analysis/retry queue, no Gmail poll and rejects bridge
    # POSTs with 503; manual commands such as /analyze keep working.
    paused: bool = False
    # Portals allowed to feed the bot. None (default) = legacy: every portal
    # registered in cva/ingest/sources.py is enabled, so an existing install
    # keeps working unchanged. A list narrows it: the bridge rejects jobs from
    # any other portal and the suggested searches only name enabled portals.
    enabled_portals: list[str] | None = None

    # Tracker/matching policy.
    threshold: int = 65
    checks_per_day: int = 3
    beacon_interval_minutes: int = 480
    beacon_global_daily_cap: int = 30
    owner_telegram_id: int = 0

    # Auto-triage (auto-triage D8): bridged jobs are analyzed automatically
    # after upsert (flag off = the legacy [Analizar] card). The PDF mirror
    # root is a plain non-secret TOML string; None/"" disables the mirror.
    auto_analyze: bool = True
    cv_mirror_dir: str | None = None
    # Full tracker xlsx (every offer, all estados) rewritten after each
    # service mutation. A full FILE path inside a synced folder — machine-
    # specific, set in config.local.toml; None/"" disables it.
    tracker_xlsx_path: str | None = None

    # Beacon bridge (beacon-bridge D6): port is a normal TOML setting; the
    # secret is env-only like every other secret. Default "" disables the
    # endpoint at startup — fail-safe, like owner_telegram_id = 0.
    bridge_port: int = 8765
    bridge_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("CVA_BRIDGE_SECRET")
    )

    # Notification pacing (notification-pacing D8): TOML-only, non-secret.
    # analyze_delay_seconds: pacing sleep between queued analyses; 0 → no sleep.
    # notify_min_score: score gate for breakdown cards; None → the gate reads
    # the live threshold at call time (never copied at startup).
    # analyze_retry_max: heartbeat sweep retry budget per failed analysis;
    # 0 → sweep disabled (first failure → immediate final fallback card).
    analyze_delay_seconds: int = 45
    notify_min_score: int | None = None
    analyze_retry_max: int = 5
    # notify_gray_zone (borderline amendment): scores in [gate - zone, gate)
    # get a compact borderline card for manual review; 0 → zone disabled
    # (pure threshold behavior, exactly the pre-amendment semantics).
    notify_gray_zone: int = 15
    # Backend profile (backend-profile): offers found by a backend beacon (or
    # inferred as backend from the title) use their own, laxer notify gate and
    # gray zone. backend_gray_zone None -> follows notify_gray_zone.
    backend_threshold: int = DEFAULT_BACKEND_THRESHOLD
    backend_gray_zone: int | None = None
    # round_summary (round-summary amendment): after a payload's batch of new
    # jobs settles, the bridge sends ONE compact Spanish round-summary card —
    # ADDITIONAL to the individual cards. false → exactly the silent
    # pre-amendment behavior.
    round_summary: bool = True
    # cover_letter_review (cover-letter-rules): after the two letters pass the
    # validator, one extra LLM call reviews and revises them against the rule
    # checklist; the revision replaces them only when it re-validates. false
    # → exactly the single-generation behavior.
    cover_letter_review: bool = True

    # LLM offer judge (llm-offer-judge design): per-step model overrides,
    # both optional — None keeps the provider's single configured model
    # (openai_compatible -> llm_model / claude_code -> claude_code_model) for
    # that step. claude_code passes "--model" per call; openai_compatible
    # passes the "model" payload field per call (cva/llm.py LlmClient
    # per-call override).
    extraction_model: str | None = None
    judge_model: str | None = None

    # match_mode (llm-offer-judge design): "keyword" is today's deterministic
    # scorer only (default, UNCHANGED behavior). "hybrid" runs the keyword
    # scorer (breakdown kept for transparency) AND the judge; the judge's
    # score/verdict drive the threshold gate, gray zone, notifications and
    # round summary. A judge-only mode (no keyword breakdown) is not
    # implemented and is deliberately absent from these allowed values
    # (robustness follow-up) — setting an unsupported value is a clear
    # config validation error rather than a silent/raising runtime path.
    match_mode: Literal["keyword", "hybrid"] = "keyword"

    # Gmail sync (gmail-sync): read-only polling of the owner's mailbox that
    # proposes application status changes. Off by default — nothing runs and
    # no credential is touched until it is configured. Paths accept "~" and
    # point at machine-local files (never committed; ``data/`` is gitignored).
    gmail_enabled: bool = False
    gmail_credentials_path: str = "data/gmail_credentials.json"
    gmail_token_path: str = "data/gmail_token.json"
    # Poll interval in minutes; below 30 is a config error (quota/noise guard).
    gmail_poll_minutes: int = Field(default=60, ge=30)

    # Profile directory (profile-extraction): every owner-specific file
    # (claims, synonyms, voice, salary table, masters, base CV PDFs,
    # profile.toml) is read from here. None/"" keeps today's repository
    # locations (cva/profile_paths.py). Relative paths resolve against the cwd.
    profile_dir: Path | None = None

    # CV font family of the render engine (cva/cvgen/fonts.py): "carlito"
    # (default, SIL OFL 1.1, metric-compatible with Calibri, shipped in
    # cvgen/fonts/carlito/) or "calibri" (Microsoft's proprietary Calibri,
    # only where cvgen/fonts/calibri*.ttf exist). Any other value is a clear
    # startup validation error.
    cv_font: CvFont = DEFAULT_CV_FONT

    # Language of everything the user reads in Telegram, the CLI and the HTML
    # panel (bot-i18n): "auto" (default) follows the system locale (``es*`` ->
    # "es", anything else "en"); "en" / "es" force it. "auto" is resolved ONCE
    # here (default factory + before-validator), so the field stays a resolved
    # ``Lang`` for every consumer. Documents (CVs, cover letters, follow-up
    # drafts) keep following the offer's language. Any other value is a clear
    # startup validation error. Env: CVA_UI_LANGUAGE.
    ui_language: Lang = Field(default_factory=system_ui_language)

    @field_validator("ui_language", mode="before")
    @classmethod
    def _auto_ui_language_is_the_system_locale(cls, value: Any) -> Any:
        """``"auto"`` (any case) resolves to the system locale at load."""
        if isinstance(value, str) and value.strip().lower() == "auto":
            return system_ui_language()
        return value

    @field_validator("enabled_portals")
    @classmethod
    def _enabled_portals_are_registered(cls, value: list[str] | None) -> Any:
        """Unknown portal ids are a clear startup error (typo guard)."""
        if value is None:
            return None
        unknown = [p for p in value if p not in SOURCES]
        if unknown:
            raise ValueError(
                f"unknown portal id(s) in enabled_portals: {', '.join(unknown)} "
                f"(known: {', '.join(SOURCES)})"
            )
        return value

    @field_validator("profile_dir", mode="before")
    @classmethod
    def _blank_profile_dir_is_unset(cls, value: Any) -> Any:
        """A blank TOML/env value means "unset" (like cv_mirror_dir = "")."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Per-instance toml_file test override (clean_settings in
        # conftest.py): "toml_file" is never a field name/alias, so
        # InitSettingsSource keeps it verbatim in init_kwargs as an unmatched
        # extra instead of dropping it — pop it here before it reaches
        # BaseSettings validation, where extra="ignore" would silently
        # discard it as an unknown field.
        toml_file_override = None
        init_kwargs = getattr(init_settings, "init_kwargs", None)
        if isinstance(init_kwargs, dict):
            toml_file_override = init_kwargs.pop("toml_file", None)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _NoSecretTomlSource(settings_cls, toml_file=toml_file_override),
        )
