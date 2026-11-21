"""LLM client contract tests: typed completion, temp 0 + JSON-schema format, env-swappable provider.

v3 part C hardening: the owner's live endpoint appends chat prose AFTER the
JSON payload (observed: content = '{"ok": true, ...}\\n\\n¿Necesitas ayuda
con algo más...?'), so ``json_completion`` must extract the first balanced
JSON object before validation. It also honors ``CVA_LLM_DISABLE_THINKING``
by sending ``"thinking": {"type": "disabled"}`` (GLM/OpenAI-compatible
extension) for endpoints that ignore ``response_format`` thinking defaults.
"""

import asyncio
import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel

from cva.config import Settings
from cva.llm import LlmResponseError, OpenAiCompatibleLlmClient, _no_json_diagnostic


class Reply(BaseModel):
    title: str
    must_haves: list[str]


def _completion_response(
    content: str, *, finish_reason: str | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ]
        },
        request=httpx.Request("POST", "https://unit.test/v1/chat/completions"),
    )


def _capturing_handler(
    captured: dict[str, object],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _completion_response(
            json.dumps({"title": "ML Engineer", "must_haves": ["python"]})
        )

    return handler


def _settings(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    *,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> Settings:
    monkeypatch.setenv("CVA_LLM_BASE_URL", base_url)
    monkeypatch.setenv("CVA_LLM_MODEL", model)
    return clean_settings()


def test_json_completion_parses_into_requested_model(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    captured: dict[str, object] = {}
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    reply = asyncio.run(client.json_completion("sys prompt", "user text", Reply))

    assert reply == Reply(title="ML Engineer", must_haves=["python"])
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer sk-test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["model"] == "gpt-4o-mini"
    response_format = body["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    schema_wrapper = response_format["json_schema"]
    assert isinstance(schema_wrapper, dict)
    assert schema_wrapper["name"] == "Reply"
    assert schema_wrapper["schema"] == Reply.model_json_schema()
    messages = body["messages"]
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert "thinking" not in body  # default: thinking left at endpoint default


def _static_handler(
    content: str, *, finish_reason: str | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _completion_response(content, finish_reason=finish_reason)

    return handler


def test_json_completion_parses_json_followed_by_trailing_chat_prose(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    """Live-observed quirk: the coding endpoint appends chat prose after the
    JSON payload; strict json.loads would crash — the first balanced JSON
    object must be extracted instead."""
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(
            _static_handler(
                '{"title": "ML Engineer", "must_haves": ["python"]}\n\n'
                "¿Necesitas ayuda con algo más? Puedo ajustar el esquema "
                "o regenerar la respuesta."
            )
        ),
    )

    reply = asyncio.run(client.json_completion("sys", "user", Reply))

    assert reply == Reply(title="ML Engineer", must_haves=["python"])


def test_json_completion_parses_json_inside_code_fence(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(
            _static_handler(
                '```json\n{"title": "Data Scientist", "must_haves": ["sql"]}\n```'
            )
        ),
    )

    reply = asyncio.run(client.json_completion("sys", "user", Reply))

    assert reply == Reply(title="Data Scientist", must_haves=["sql"])


def test_json_completion_tolerates_braces_inside_strings(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    """The balanced-object scan must not terminate on '}' inside strings."""
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(
            _static_handler(
                '{"title": "roles {y proyectos} \\"anidados\\"", '
                '"must_haves": ["python"]}\n\nDone.'
            )
        ),
    )

    reply = asyncio.run(client.json_completion("sys", "user", Reply))

    assert reply == Reply(title='roles {y proyectos} "anidados"', must_haves=["python"])


def test_json_completion_without_any_json_object_raises(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(
            _static_handler("Lo siento, no puedo generar una respuesta JSON.")
        ),
    )

    with pytest.raises(LlmResponseError, match="JSON"):
        asyncio.run(client.json_completion("sys", "user", Reply))


def test_disable_thinking_flag_sends_thinking_disabled(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("CVA_LLM_DISABLE_THINKING", "true")
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    asyncio.run(client.json_completion("sys", "user", Reply))

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0  # unchanged by the flag


# --- per-call model override (llm-offer-judge per-step model selection) ---


def test_json_completion_model_override_sent_in_payload(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    captured: dict[str, object] = {}
    settings = _settings(monkeypatch, clean_settings, model="gpt-4o-mini")
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    asyncio.run(client.json_completion("sys", "user", Reply, model="gpt-4o"))

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-4o"  # override wins over the configured default


def test_json_completion_without_override_keeps_configured_model(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    captured: dict[str, object] = {}
    settings = _settings(monkeypatch, clean_settings, model="gpt-4o-mini")
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    asyncio.run(client.json_completion("sys", "user", Reply))

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-4o-mini"


def test_json_completion_empty_string_override_keeps_configured_model(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    """An empty-string override ("") means "no override" — treated exactly
    like ``None`` — consistently with ``ClaudeCodeLlmClient`` (llm.py
    robustness follow-up: the two clients used to disagree here)."""
    captured: dict[str, object] = {}
    settings = _settings(monkeypatch, clean_settings, model="gpt-4o-mini")
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    asyncio.run(client.json_completion("sys", "user", Reply, model=""))

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-4o-mini"  # "" is never sent as a real model name


def test_from_settings_raises_clearly_without_api_key(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    """llm_api_key is optional at the Settings layer now (claude_code needs
    no key); the openai_compatible provider still fails fast, here instead
    of on the first real request."""
    monkeypatch.setenv("CVA_LLM_BASE_URL", "https://api.openai.com/v1")
    settings = clean_settings(CVA_LLM_API_KEY=None)

    with pytest.raises(ValueError, match="CVA_LLM_API_KEY"):
        OpenAiCompatibleLlmClient.from_settings(settings)


def test_provider_swappable_via_env(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    captured: dict[str, object] = {}
    settings = _settings(
        monkeypatch,
        clean_settings,
        base_url="https://llm.example.com/v1",
        model="deepseek-chat",
    )
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(_capturing_handler(captured))
    )

    reply = asyncio.run(client.json_completion("sys", "user", Reply))

    assert reply.title == "ML Engineer"
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "deepseek-chat"


# --- Single bounded retry on HTTP 429 (transient provider rate limit) ---


class _CallCountingHandler:
    """Canned status sequence per call; counts attempts (zero network)."""

    def __init__(self, statuses: list[int], content: str = "") -> None:
        self._statuses = statuses
        self._content = content
        self.calls = 0

    def __call__(self, _request: httpx.Request) -> httpx.Response:
        status = self._statuses[min(self.calls, len(self._statuses) - 1)]
        self.calls += 1
        if status == 200:
            return _completion_response(self._content)
        return httpx.Response(
            status,
            json={"error": {"message": f"HTTP {status}"}},
            request=httpx.Request("POST", "https://unit.test/v1/chat/completions"),
        )


class _RecordingSleep:
    """Async sleep stand-in: records wait seconds, never actually sleeps."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _retry_client(
    handler: Callable[[httpx.Request], httpx.Response],
    sleep: _RecordingSleep,
    *,
    wait: float = 25.0,
    transient_retries: int = 2,
) -> OpenAiCompatibleLlmClient:
    return OpenAiCompatibleLlmClient(
        base_url="https://unit.test/v1",
        api_key="sk-test",
        model="test-model",
        transport=httpx.MockTransport(handler),
        retry_429_wait_seconds=wait,
        sleep=sleep,
        transient_retries=transient_retries,
        jitter=lambda: 0.0,
    )


def test_429_then_200_retries_once_with_injected_sleep() -> None:
    """Transient 429: one bounded retry after the injected wait → success."""
    handler = _CallCountingHandler(
        [429, 200], '{"title": "ML Engineer", "must_haves": ["python"]}'
    )
    sleep = _RecordingSleep()

    reply = asyncio.run(
        _retry_client(handler, sleep).json_completion("sys", "user", Reply)
    )

    assert reply == Reply(title="ML Engineer", must_haves=["python"])
    assert handler.calls == 2
    assert sleep.calls == [25.0]


def test_429_twice_raises_with_status_after_single_retry() -> None:
    """Second 429: no further attempts; the error carries the HTTP status."""
    handler = _CallCountingHandler([429, 429])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_retry_client(handler, sleep).json_completion("sys", "user", Reply))

    assert excinfo.value.status == 429
    assert handler.calls == 2
    assert sleep.calls == [25.0]


def test_400_is_single_attempt_no_retry() -> None:
    """Client errors other than 429 are never retried."""
    handler = _CallCountingHandler([400, 200])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_retry_client(handler, sleep).json_completion("sys", "user", Reply))

    assert excinfo.value.status == 400
    assert handler.calls == 1
    assert sleep.calls == []


def test_429_retry_disabled_when_wait_is_zero() -> None:
    """wait = 0 disables the retry entirely: fail on the first 429."""
    handler = _CallCountingHandler([429, 429])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError):
        asyncio.run(
            _retry_client(handler, sleep, wait=0).json_completion("sys", "user", Reply)
        )

    assert handler.calls == 1
    assert sleep.calls == []


# --- Bounded backoff for transient provider failures (5xx, timeouts) ---

_GOOD = '{"title": "ML Engineer", "must_haves": ["python"]}'
_HIGH_DEMAND = (
    "This model is currently experiencing high demand. Spikes in demand are "
    "usually temporary. Please try again later."
)


def _error_response(status: int, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"code": status, "message": message, "status": "X"}},
        request=httpx.Request("POST", "https://unit.test/v1/chat/completions"),
    )


class _SequenceHandler:
    """Plays a scripted sequence: an exception to raise or a response to return."""

    def __init__(self, steps: list[httpx.Response | Exception]) -> None:
        self._steps = steps
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        step = self._steps[min(self.calls, len(self._steps) - 1)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return step


def test_503_then_200_succeeds_after_backoff() -> None:
    handler = _SequenceHandler(
        [_error_response(503, _HIGH_DEMAND), _completion_response(_GOOD)]
    )
    sleep = _RecordingSleep()

    reply = asyncio.run(
        _retry_client(handler, sleep).json_completion("sys", "user", Reply)
    )

    assert reply == Reply(title="ML Engineer", must_haves=["python"])
    assert handler.calls == 2
    assert sleep.calls == [2.0]


@pytest.mark.parametrize("status", [500, 502, 503])
def test_persistent_5xx_raises_after_bounded_attempts_with_provider_message(
    status: int,
) -> None:
    handler = _SequenceHandler([_error_response(status, _HIGH_DEMAND)])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_retry_client(handler, sleep).json_completion("sys", "user", Reply))

    assert excinfo.value.status == status
    assert handler.calls == 3
    assert sleep.calls == [2.0, 5.0]
    message = str(excinfo.value)
    assert f"HTTP {status}" in message
    assert "high demand" in message


def test_timeout_then_200_succeeds_after_backoff() -> None:
    handler = _SequenceHandler(
        [httpx.ReadTimeout("read timed out"), _completion_response(_GOOD)]
    )
    sleep = _RecordingSleep()

    reply = asyncio.run(
        _retry_client(handler, sleep).json_completion("sys", "user", Reply)
    )

    assert reply.title == "ML Engineer"
    assert handler.calls == 2
    assert sleep.calls == [2.0]


def test_persistent_connection_error_raises_llm_error_without_status() -> None:
    handler = _SequenceHandler([httpx.ConnectError("connection refused")])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_retry_client(handler, sleep).json_completion("sys", "user", Reply))

    assert excinfo.value.status is None
    assert "ConnectError" in str(excinfo.value)
    assert handler.calls == 3
    assert sleep.calls == [2.0, 5.0]


def test_404_is_not_retried_and_carries_the_provider_message() -> None:
    gone = (
        "This model models/gemini-2.0-flash is no longer available to new users. "
        "Please update your code to use models/gemini-3.8-flash instead."
    )
    handler = _SequenceHandler([_error_response(404, gone)])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_retry_client(handler, sleep).json_completion("sys", "user", Reply))

    assert excinfo.value.status == 404
    assert handler.calls == 1
    assert sleep.calls == []
    assert str(excinfo.value) == f"LLM endpoint returned HTTP 404: {gone}"


def test_provider_message_is_flattened_truncated_and_never_echoes_the_key() -> None:
    handler = _SequenceHandler([_error_response(400, "bad\nrequest " + "x" * 500)])

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(
            _retry_client(handler, _RecordingSleep()).json_completion(
                "sys", "user", Reply
            )
        )

    message = str(excinfo.value)
    assert "\n" not in message
    assert "sk-test" not in message
    assert message.startswith("LLM endpoint returned HTTP 400: bad request x")
    assert len(message) <= len("LLM endpoint returned HTTP 400: ") + 201


def test_non_json_error_body_keeps_the_bare_status_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, text="<html>teapot</html>")

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(
            _retry_client(handler, _RecordingSleep()).json_completion(
                "sys", "user", Reply
            )
        )

    assert str(excinfo.value) == "LLM endpoint returned HTTP 418"


def test_transient_retries_zero_disables_the_backoff() -> None:
    handler = _SequenceHandler([_error_response(503, _HIGH_DEMAND)])
    sleep = _RecordingSleep()

    with pytest.raises(LlmResponseError):
        asyncio.run(
            _retry_client(handler, sleep, transient_retries=0).json_completion(
                "sys", "user", Reply
            )
        )

    assert handler.calls == 1
    assert sleep.calls == []


def test_429_then_503_then_200_keeps_the_single_429_retry() -> None:
    handler = _SequenceHandler(
        [
            _error_response(429, "slow down"),
            _error_response(503, _HIGH_DEMAND),
            _completion_response(_GOOD),
        ]
    )
    sleep = _RecordingSleep()

    reply = asyncio.run(
        _retry_client(handler, sleep).json_completion("sys", "user", Reply)
    )

    assert reply.title == "ML Engineer"
    assert handler.calls == 3
    assert sleep.calls == [25.0, 2.0]


def test_backoff_adds_jitter_to_each_wait() -> None:
    handler = _SequenceHandler([_error_response(503, _HIGH_DEMAND)])
    sleep = _RecordingSleep()
    client = OpenAiCompatibleLlmClient(
        base_url="https://unit.test/v1",
        api_key="sk-test",
        model="test-model",
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        jitter=lambda: 0.25,
    )

    with pytest.raises(LlmResponseError):
        asyncio.run(client.json_completion("sys", "user", Reply))

    assert sleep.calls == [2.25, 5.25]


def test_cancellation_during_backoff_stops_promptly() -> None:
    handler = _SequenceHandler([_error_response(503, _HIGH_DEMAND)])
    client = OpenAiCompatibleLlmClient(
        base_url="https://unit.test/v1",
        api_key="sk-test",
        model="test-model",
        transport=httpx.MockTransport(handler),
        transient_backoff_seconds=60.0,
    )

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(client.json_completion("sys", "user", Reply))
        while handler.calls == 0:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        started = loop.time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return loop.time() - started

    elapsed = asyncio.run(scenario())

    assert elapsed < 1.0
    assert handler.calls == 1


def test_from_settings_wires_transient_retries(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-test")
    assert clean_settings().llm_transient_retries == 2
    monkeypatch.setenv("CVA_LLM_TRANSIENT_RETRIES", "0")
    settings = clean_settings()
    handler = _SequenceHandler([_error_response(503, _HIGH_DEMAND)])
    client = OpenAiCompatibleLlmClient.from_settings(
        settings, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(LlmResponseError):
        asyncio.run(client.json_completion("sys", "user", Reply))

    assert handler.calls == 1


def test_parse_error_status_is_none() -> None:
    """Non-HTTP failures (malformed envelope) carry no HTTP status."""
    handler = _CallCountingHandler([200], "Lo siento, no hay JSON en esta respuesta.")

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(
            _retry_client(handler, _RecordingSleep()).json_completion(
                "sys", "user", Reply
            )
        )

    assert excinfo.value.status is None
    assert handler.calls == 1  # parse errors are never retried


# --- No-JSON failure diagnostics (live failure: Catalan offer → pure prose) ---

_MARKDOWN_PROSE = (
    "# Oferta de treball\n\n"
    "Busquem una persona apassionada per la tecnologia per unir-se al "
    "nostre equip de desenvolupament. "
)


def test_no_json_diagnostic_includes_finish_reason_and_flattened_head() -> None:
    message = _no_json_diagnostic(_MARKDOWN_PROSE, "stop")

    assert "no JSON object" in message
    assert "finish_reason='stop'" in message
    assert "Busquem" in message  # head fragment of the offending content
    assert "\n" not in message  # safe single line for chat cards


def test_no_json_diagnostic_truncates_head_and_never_leaks_newlines() -> None:
    long_prose = _MARKDOWN_PROSE * 6  # > 200 chars, with newlines
    tail_fragment = long_prose[240:280]

    message = _no_json_diagnostic(long_prose, "stop")

    assert long_prose[40:80] in message  # inside the first 200 chars
    assert tail_fragment not in message  # beyond the truncation window
    assert "\n" not in message


def test_no_json_diagnostic_without_finish_reason() -> None:
    message = _no_json_diagnostic("plain refusal prose", None)

    assert "finish_reason=None" in message
    assert "plain refusal prose" in message


def test_no_json_diagnostic_accepts_a_custom_label() -> None:
    """The claude-code provider has no OpenAI-style finish_reason — its
    diagnostic must not claim one (llm.py robustness follow-up: misleading
    finish_reason label at the claude-code parse site)."""
    message = _no_json_diagnostic(_MARKDOWN_PROSE, "success", label="envelope_type")

    assert "envelope_type='success'" in message
    assert "finish_reason" not in message


def test_no_json_error_carries_finish_reason_and_content_head(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
) -> None:
    """Live-verified failure shape: pure markdown prose, finish_reason=stop,
    zero braces — the raised error must name both for diagnosis."""
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(
            _static_handler(_MARKDOWN_PROSE, finish_reason="stop")
        ),
    )

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(client.json_completion("sys", "user", Reply))

    message = str(excinfo.value)
    assert "no JSON object" in message
    assert "finish_reason='stop'" in message
    assert "Busquem" in message
    assert excinfo.value.status is None


def test_no_json_failure_logs_debug_content_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    clean_settings: Callable[..., Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = ("alpha prose without braces. " * 20) + "omega tail marker"
    settings = _settings(monkeypatch, clean_settings)
    client = OpenAiCompatibleLlmClient.from_settings(
        settings,
        transport=httpx.MockTransport(_static_handler(content, finish_reason="stop")),
    )

    with (
        caplog.at_level(logging.DEBUG, logger="cva.llm"),
        pytest.raises(LlmResponseError),
    ):
        asyncio.run(client.json_completion("sys", "user", Reply))

    debug_records = [
        r for r in caplog.records if r.name == "cva.llm" and r.levelno == logging.DEBUG
    ]
    assert debug_records, "expected a DEBUG record from cva.llm"
    logged = " ".join(r.getMessage() for r in debug_records)
    assert "length=" in logged
    assert "alpha prose" in logged  # head
    assert "omega tail marker" in logged  # tail


# --- describe_llm_setup (shared-config T2: effective-LLM banner) -----------


def test_describe_llm_setup_claude_code_default_model(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    """No step model override: both extraction/judge fall back to
    claude_code_model, or the literal "default" when that is also unset."""
    from cva.llm import describe_llm_setup

    settings = clean_settings(llm_provider="claude_code")

    line = describe_llm_setup(settings)

    assert line == "LLM: proveedor=claude_code · extracción=default · juez=default"


def test_describe_llm_setup_claude_code_step_models_override(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    settings = clean_settings(
        llm_provider="claude_code",
        claude_code_model="sonnet",
        extraction_model="haiku",
    )

    from cva.llm import describe_llm_setup

    line = describe_llm_setup(settings)

    # extraction_model overrides the provider default; judge_model unset ->
    # falls back to claude_code_model.
    assert line == "LLM: proveedor=claude_code · extracción=haiku · juez=sonnet"


def test_describe_llm_setup_openai_compatible_includes_host_and_model(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    from cva.llm import describe_llm_setup

    settings = clean_settings(
        llm_provider="openai_compatible",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
    )

    line = describe_llm_setup(settings)

    assert line == (
        "LLM: proveedor=openai_compatible · endpoint=api.openai.com · "
        "extracción=gpt-4o-mini · juez=gpt-4o-mini"
    )


def test_describe_llm_setup_never_leaks_secrets(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    """Both secret fields carry an obviously-fake value in the fixture; the
    banner must never render either, whatever provider is configured."""
    from cva.llm import describe_llm_setup

    for provider in ("openai_compatible", "claude_code"):
        settings = clean_settings(llm_provider=provider)
        line = describe_llm_setup(settings)
        assert "123456:TEST" not in line
        assert "sk-test" not in line
