"""AI outage pause (llm-outage).

An auth/credential failure (Claude Code login expired, Codex not logged in,
HTTP 401/403) or a usage limit (5-hour limit, monthly quota) PAUSES the
automatic analysis instead of failing offer by offer: new offers wait
pending with no error cards, ONE alert explains the cause and the fix, a
periodic probe resumes by itself and re-drives the backlog (including the
offers that failed before the pause). Transient failures keep the beta.5
retry-later behaviour. Zero network, fake LLM clients only.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import bot
from cva.ai_outage import (
    STATE_KEY,
    AiOutage,
    classify_ai_failure,
    parse_reset,
    scrub,
)
from cva.fetch import FetchResponse
from cva.ingest import job_id_from_url
from cva.llm import LlmResponseError
from cva.scheduler import heartbeat
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.test_service import (
    JSONLD_BODY,
    OFFER_TEXT,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)

# Far enough in the past that the heartbeat's real-clock sweep sees every
# row this clock defers as due.
NOW = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)

CLAUDE_EXPIRED = (
    "claude CLI exited with status 1: Failed to authenticate: this login "
    "reached its time limit and cannot be renewed. Run `claude auth login` to "
    "sign in again, or use `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN "
    "for unattended jobs"
)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class _SwitchLlm(FakeLlmClient):
    """Fails every call with ``error`` while it is set; succeeds otherwise."""

    def __init__(self, error: Exception | None) -> None:
        super().__init__(_extraction())
        self.error = error
        self.attempts = 0

    async def json_completion(
        self,
        system: str,
        user: str,
        schema: type[object],
        *,
        model: str | None = None,
    ) -> object:
        self.attempts += 1
        if self.error is not None:
            raise self.error
        return await super().json_completion(system, user, schema, model=model)


class _Notify:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, text: str, keyboard: Any) -> None:
        self.calls.append((text, keyboard))


class _Bridge:
    def __init__(self) -> None:
        self.enqueued: list[tuple[int, str]] = []

    def enqueue_auto_analysis(
        self, offer_id: int, job_id: str, job: dict[str, object], *, kind: str = "new"
    ) -> None:
        self.enqueued.append((offer_id, kind))


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(
        self, chat_id: int, text: str, reply_markup: object = None
    ) -> None:
        self.sent.append((chat_id, text, reply_markup))


def _setup(
    tmp_path: Path, error: Exception | None, *, lang: str = "es"
) -> tuple[JobService, OfferRepository, _SwitchLlm, _Clock]:
    llm = _SwitchLlm(error)
    clock = _Clock()
    service, repo = make_service(
        tmp_path,
        llm,
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        now_fn=clock,
    )
    service._lang = lang  # type: ignore[assignment]
    return service, repo, llm, clock


def _offer(repo: OfferRepository, n: int = 0) -> tuple[int, str]:
    url = f"https://www.linkedin.com/jobs/view/44598045{n:02d}"
    offer_id = repo.upsert_offer(
        OfferInput(
            job_id=job_id_from_url(url),
            title="AI Engineer",
            company="Acme",
            url=url,
            text=OFFER_TEXT,
            source="link",
        )
    )
    return offer_id, url


def _run(service: JobService, offer_id: int, url: str, notify: _Notify) -> Any:
    return asyncio.run(
        service.analyze_new_bridge_job(
            offer_id,
            url=url,
            title="AI Engineer",
            company="Acme",
            notify=notify,
            min_score=0,
            text=OFFER_TEXT,
        )
    )


def _context(service: JobService, bridge: _Bridge, tg: _Bot) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"service": service, "owner_id": 5, "bridge": bridge}
        ),
        bot=tg,
    )


# --- classification -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        LlmResponseError(CLAUDE_EXPIRED),
        LlmResponseError(
            "claude CLI exited with status 1: Not logged in · Please run /login"
        ),
        LlmResponseError(
            "codex CLI is not logged in (exit status 1): run `codex login` with "
            "your ChatGPT account, then try again"
        ),
        LlmResponseError("LLM endpoint returned HTTP 401: invalid key", status=401),
        LlmResponseError("Anthropic HTTP 403", status=403),
        LlmResponseError("Anthropic HTTP 401: invalid x-api-key", status=401),
    ],
)
def test_auth_failures_are_outages(exc: LlmResponseError) -> None:
    assert classify_ai_failure(exc, NOW) == ("auth", None)


@pytest.mark.parametrize(
    "text",
    [
        "claude CLI exited with status 1: You've hit your 5-hour limit",
        "claude CLI exited with status 1: Claude AI usage limit reached",
        "codex CLI exited with status 1: You've hit your usage limit.",
        "LLM endpoint returned HTTP 429: You exceeded your current quota",
        "Anthropic HTTP 400: Your credit balance is too low",
    ],
)
def test_usage_limits_are_quota_outages(text: str) -> None:
    classified = classify_ai_failure(LlmResponseError(text), NOW)
    assert classified is not None and classified[0] == "quota"


@pytest.mark.parametrize(
    "exc",
    [
        LlmResponseError("LLM endpoint returned HTTP 503", status=503),
        LlmResponseError("LLM endpoint returned HTTP 429", status=429),
        LlmResponseError("claude CLI timed out after 180.0s"),
        LlmResponseError("claude CLI exited with status 1: api_error_status=529"),
        LlmResponseError("completion does not satisfy OfferExtraction"),
        RuntimeError("Failed to authenticate"),  # not an LLM error
    ],
)
def test_transient_and_other_failures_are_not_outages(exc: Exception) -> None:
    assert classify_ai_failure(exc, NOW) is None


def test_reset_time_parsing() -> None:
    epoch = int((NOW + timedelta(hours=2)).timestamp())
    assert parse_reset(
        f"Claude AI usage limit reached|{epoch}", NOW
    ) == NOW + timedelta(hours=2)
    assert parse_reset("try again in 2 hours 13 minutes", NOW) == NOW + timedelta(
        hours=2, minutes=13
    )
    madrid = parse_reset("hit your 5-hour limit · resets 3pm (Europe/Madrid)", NOW)
    assert madrid == datetime(2026, 1, 10, 15, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    # 09:00 UTC is 10:00 in Madrid: "resets 9am" is tomorrow there.
    tomorrow = parse_reset("resets 9am (Europe/Madrid)", NOW)
    assert tomorrow == datetime(2026, 1, 11, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    assert parse_reset("usage limit reached", NOW) is None


def test_scrub_masks_secrets() -> None:
    text = scrub("bad key sk-ant-api03-abcdefghijklmnop Bearer xyz123 token=abc")
    assert "abcdefghijklmnop" not in text
    assert "xyz123" not in text
    assert "token=abc" not in text


# --- service: pause on outage ---------------------------------------------------


def test_auth_outage_pauses_with_one_alert_and_no_failure_cards(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    notify = _Notify()
    first, url1 = _offer(repo, 1)
    second, url2 = _offer(repo, 2)

    outcome = _run(service, first, url1, notify)
    _run(service, second, url2, notify)

    assert outcome.zone == "pending"
    assert len(notify.calls) == 1  # ONE alert, no "análisis automático falló" cards
    text, keyboard = notify.calls[0]
    assert "falló" not in text
    assert "claude auth login" in text
    assert "claude setup-token" in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    assert keyboard is not None
    for offer_id in (first, second):
        row = repo.get_offer(offer_id)
        assert row.estado == "pendiente_analisis"
        assert row.next_analyze_at is not None  # waits for the resume
        assert row.analyze_attempts == 0  # an outage is not a failed attempt
    outage = service.ai_outage()
    assert outage is not None and outage.kind == "auth" and outage.alerted


def test_second_offer_during_outage_never_calls_the_llm(tmp_path: Path) -> None:
    service, repo, llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    notify = _Notify()
    first, url1 = _offer(repo, 1)
    second, url2 = _offer(repo, 2)
    _run(service, first, url1, notify)
    calls = llm.attempts
    _run(service, second, url2, notify)
    assert llm.attempts == calls


def test_english_alert_for_codex_login(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(
        tmp_path,
        LlmResponseError(
            "codex CLI is not logged in (exit status 1): run `codex login`"
        ),
        lang="en",
    )
    notify = _Notify()
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, notify)
    text = notify.calls[0][0]
    assert "paused" in text.lower()
    assert "codex login" in text


def test_quota_with_reset_time_resumes_at_reset(tmp_path: Path) -> None:
    epoch = int((NOW + timedelta(hours=2)).timestamp())
    service, repo, _llm, _clock = _setup(
        tmp_path,
        LlmResponseError(
            f"claude CLI exited with status 1: usage limit reached|{epoch}"
        ),
        lang="en",
    )
    notify = _Notify()
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, notify)
    outage = service.ai_outage()
    assert outage is not None and outage.kind == "quota"
    assert outage.reset_at == NOW + timedelta(hours=2)
    assert outage.next_probe >= NOW + timedelta(hours=2)
    local = (NOW + timedelta(hours=2)).astimezone().strftime("%H:%M")
    assert f"resume by myself at {local}" in notify.calls[0][0]


def test_transient_503_keeps_retry_later(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(
        tmp_path, LlmResponseError("LLM endpoint returned HTTP 503", status=503)
    )
    notify = _Notify()
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, notify)
    assert service.ai_outage() is None
    assert repo.get_offer(offer_id).analyze_attempts == 1  # beta.5 retry path
    assert notify.calls == []


def test_non_ai_failure_keeps_final_card(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(
        tmp_path, LlmResponseError("completion does not satisfy OfferExtraction")
    )
    service._analyze_retry_max = 0
    notify = _Notify()
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, notify)
    assert service.ai_outage() is None
    assert len(notify.calls) == 1
    assert "falló" in notify.calls[0][0]


def test_restart_keeps_outage_without_realerting(tmp_path: Path) -> None:
    service, repo, _llm, clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    notify = _Notify()
    first, url1 = _offer(repo, 1)
    _run(service, first, url1, notify)
    # "Restart": a brand-new service on the same database.
    restarted, _ = make_service(
        tmp_path,
        _SwitchLlm(LlmResponseError(CLAUDE_EXPIRED)),
        FakeFetcher(FetchResponse(200, JSONLD_BODY, OFFER_URL)),
        now_fn=clock,
    )
    second, url2 = _offer(repo, 2)
    notify2 = _Notify()
    _run(restarted, second, url2, notify2)
    assert restarted.ai_outage() is not None
    assert notify2.calls == []


# --- heartbeat probe + resume ---------------------------------------------------


def test_probe_still_failing_stays_paused(tmp_path: Path) -> None:
    service, repo, llm, clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    clock.now = NOW + timedelta(minutes=16)
    bridge, tg = _Bridge(), _Bot()
    before = llm.attempts
    asyncio.run(heartbeat(_context(service, bridge, tg)))
    assert llm.attempts == before + 1  # one probe call
    assert service.ai_outage() is not None
    assert bridge.enqueued == []  # retry sweep held while paused
    assert tg.sent == []  # already alerted: no new message
    outage = service.ai_outage()
    assert outage is not None and outage.next_probe > clock.now


def test_probe_not_due_makes_no_call(tmp_path: Path) -> None:
    service, repo, llm, clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    clock.now = NOW + timedelta(minutes=5)
    before = llm.attempts
    asyncio.run(heartbeat(_context(service, _Bridge(), _Bot())))
    assert llm.attempts == before


def test_probe_ok_resumes_and_redrives_backlog(tmp_path: Path) -> None:
    service, repo, llm, clock = _setup(
        tmp_path, LlmResponseError(CLAUDE_EXPIRED), lang="en"
    )
    # An offer that already failed with the final card BEFORE the pause
    # (the owner's 9): unanalysed, retry state cleared.
    old, _ = _offer(repo, 9)
    first, url1 = _offer(repo, 1)
    second, url2 = _offer(repo, 2)
    _run(service, first, url1, _Notify())
    _run(service, second, url2, _Notify())
    llm.error = None  # the owner logged in again
    clock.now = NOW + timedelta(minutes=16)
    bridge, tg = _Bridge(), _Bot()
    asyncio.run(heartbeat(_context(service, bridge, tg)))
    assert service.ai_outage() is None
    assert sorted(offer_id for offer_id, _ in bridge.enqueued) == sorted(
        [old, first, second]
    )
    assert all(kind == "retry" for _, kind in bridge.enqueued)
    assert len(tg.sent) == 1
    assert "AI is back" in tg.sent[0][1] and "3" in tg.sent[0][1]


def test_heartbeat_sends_a_missed_alert_once(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(tmp_path, None)
    repo.set_state(
        STATE_KEY,
        AiOutage(
            kind="auth",
            provider="openai_compatible",
            since=NOW,
            next_probe=NOW + timedelta(hours=1),
        ).to_json(),
    )
    tg = _Bot()
    asyncio.run(heartbeat(_context(service, _Bridge(), tg)))
    asyncio.run(heartbeat(_context(service, _Bridge(), tg)))
    assert len(tg.sent) == 1
    assert "CVA_LLM_API_KEY" in tg.sent[0][1]


# --- bot: manual analyze + buttons ----------------------------------------------


class _Message:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.markups: list[object] = []
        self.chat_id = 5
        self.message_id = 11

    async def reply_text(
        self, text: str, reply_markup: object = None, **_: Any
    ) -> None:
        self.replies.append(text)
        self.markups.append(reply_markup)


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.message = _Message()

    async def answer(self) -> None:
        return None


class _BotContext:
    def __init__(self, service: JobService, bridge: _Bridge | None = None) -> None:
        self.bot_data: dict[str, object] = {
            bot._SERVICE_KEY: service,
            bot._OWNER_KEY: 5,
        }
        if bridge is not None:
            self.bot_data["bridge"] = bridge
        self.user_data: dict[str, object] = {}
        self.args: list[str] = []


def test_manual_analyze_during_outage_answers_with_the_outage(tmp_path: Path) -> None:
    service, repo, llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    other, _ = _offer(repo, 3)
    message = _Message()
    before = llm.attempts
    asyncio.run(bot._analyze_and_reply(service, message, _BotContext(service), other))  # type: ignore[arg-type]
    assert llm.attempts == before
    assert len(message.replies) == 1
    assert "claude auth login" in message.replies[0]
    assert "El análisis falló" not in message.replies[0]
    assert repo.get_offer(other).next_analyze_at is not None  # resumes later


def test_manual_analyze_auth_failure_starts_the_outage(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(
        tmp_path, LlmResponseError("LLM endpoint returned HTTP 401", status=401)
    )
    offer_id, _ = _offer(repo)
    message = _Message()
    asyncio.run(
        bot._analyze_and_reply(service, message, _BotContext(service), offer_id)
    )  # type: ignore[arg-type]
    assert service.ai_outage() is not None
    assert "CVA_LLM_API_KEY" in message.replies[0]
    outage = service.ai_outage()
    assert outage is not None and outage.alerted


def test_check_now_button_resumes_when_the_ai_answers(tmp_path: Path) -> None:
    service, repo, llm, _clock = _setup(
        tmp_path, LlmResponseError(CLAUDE_EXPIRED), lang="en"
    )
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    llm.error = None
    bridge = _Bridge()
    query = _Query("aio:check")
    context = _BotContext(service, bridge)
    context.bot_data[bot._LANG_KEY] = "en"
    asyncio.run(bot.callback_router(SimpleNamespace(callback_query=query), context))  # type: ignore[arg-type]
    assert service.ai_outage() is None
    assert [oid for oid, _ in bridge.enqueued] == [offer_id]
    assert "AI is back" in query.message.replies[0]


def test_check_now_button_while_still_failing(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    query = _Query("aio:check")
    asyncio.run(
        bot.callback_router(SimpleNamespace(callback_query=query), _BotContext(service))
    )  # type: ignore[arg-type]
    assert service.ai_outage() is not None
    assert "claude auth login" in query.message.replies[0]


def test_resume_button_clears_without_probing(tmp_path: Path) -> None:
    service, repo, llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    before = llm.attempts
    bridge = _Bridge()
    query = _Query("aio:resume")
    asyncio.run(
        bot.callback_router(
            SimpleNamespace(callback_query=query), _BotContext(service, bridge)
        )
    )  # type: ignore[arg-type]
    assert llm.attempts == before
    assert service.ai_outage() is None
    assert [oid for oid, _ in bridge.enqueued] == [offer_id]


def test_status_command_shows_the_outage(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(tmp_path, LlmResponseError(CLAUDE_EXPIRED))
    offer_id, url = _offer(repo)
    _run(service, offer_id, url, _Notify())
    message = _Message()
    asyncio.run(
        bot.status_command(SimpleNamespace(message=message), _BotContext(service))
    )  # type: ignore[arg-type]
    assert "IA en pausa" in message.replies[0]


def test_claude_oauth_token_from_settings_reaches_the_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cva import llm as llm_module

    seen: dict[str, str] = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b"{}", b""

    async def fake_exec(*argv: str, **kwargs: Any) -> _Proc:
        seen.update(kwargs["env"])
        return _Proc()

    monkeypatch.setattr(llm_module.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    asyncio.run(
        llm_module._run_claude_subprocess(
            ["claude"], b"", 5.0, oauth_token="tok-from-env-file"
        )
    )
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-from-env-file"


def test_settings_read_claude_oauth_token_env_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cva.config import Settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    settings = Settings(_env_file=None, CVA_TELEGRAM_TOKEN="x")  # type: ignore[call-arg]
    assert settings.claude_code_oauth_token is not None
    assert settings.claude_code_oauth_token.get_secret_value() == "tok"


def test_unreadable_state_means_no_outage(tmp_path: Path) -> None:
    service, repo, _llm, _clock = _setup(tmp_path, None)
    repo.set_state(STATE_KEY, "{not json")
    assert service.ai_outage() is None


def test_a_plain_openai_or_groq_rate_limit_is_not_a_quota_outage() -> None:
    """'Rate limit reached for …' (OpenAI/Groq per-minute limits) is transient
    (beta.5 retry-later), not a usage-limit pause; Claude's own wording still is."""
    from datetime import UTC, datetime

    from cva.ai_outage import classify_ai_failure
    from cva.llm import LlmResponseError

    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    for text in (
        (
            "LLM endpoint returned HTTP 429: Rate limit reached for gpt-4o-mini in "
            "organization org-abc on requests per min (RPM): Limit 3, Used 3."
        ),
        (
            "LLM endpoint returned HTTP 429: Rate limit reached for model "
            "`llama-3.3-70b-versatile` in organization org_x on tokens per minute"
        ),
    ):
        assert classify_ai_failure(LlmResponseError(text, status=429), now) is None
    claude = LlmResponseError("Claude AI usage limit reached|1764079200")
    assert classify_ai_failure(claude, now) is not None
