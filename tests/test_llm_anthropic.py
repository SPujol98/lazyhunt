"""Native Anthropic provider (safe-defaults T3): request shape, parsing,
error mapping, settings/factory wiring and the claude child-env guard.

No test touches the network: ``httpx.MockTransport`` answers every request.
"""

import asyncio
import functools
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field

from cva.config import Settings
from cva.llm import (
    ANTHROPIC_VERSION,
    AnthropicLlmClient,
    ClaudeCodeLlmClient,
    LlmResponseError,
    OpenAiCompatibleLlmClient,
    _child_env,
    build_llm_client,
    describe_llm_setup,
    supports_web_search,
)


def _sync(fn: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    """Run an async test body with ``asyncio.run`` (no pytest-asyncio here)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(fn(*args, **kwargs))

    return wrapper


class Reply(BaseModel):
    title: str = Field(min_length=1, max_length=50)
    score: int = Field(ge=0, le=100)
    tags: list[str]


class Nested(BaseModel):
    inner: Reply


def _message(
    text: str, *, stop_reason: str = "end_turn", status: int = 200
) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "type": "message",
            "role": "assistant",
            "stop_reason": stop_reason,
            "content": [{"type": "text", "text": text}],
        },
    )


GOOD = json.dumps({"title": "ML Engineer", "score": 80, "tags": ["python"]})


def _client(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: object
) -> AnthropicLlmClient:
    return AnthropicLlmClient(
        api_key="sk-ant-test",
        model="claude-test",
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


@_sync
async def test_request_shape_headers_body_and_no_sampling_params() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _message(GOOD)

    result = await _client(handler).json_completion("SYS", "USER", Reply)

    assert result == Reply(title="ML Engineer", score=80, tags=["python"])
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    assert "authorization" not in headers
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "claude-test"
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["max_tokens"] > 0
    for forbidden in ("temperature", "top_p", "top_k"):
        assert forbidden not in body
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["type"] == "object"


@_sync
async def test_schema_is_sanitised_for_structured_output() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _message(json.dumps({"inner": json.loads(GOOD)}))

    await _client(handler).json_completion("s", "u", Nested)

    body = seen["body"]
    assert isinstance(body, dict)
    schema = body["output_config"]["format"]["schema"]
    text = json.dumps(schema)
    for keyword in ("minLength", "maxLength", "minimum", "maximum", "multipleOf"):
        assert keyword not in text
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Reply"]["additionalProperties"] is False
    # Unrelated keywords survive.
    assert schema["$defs"]["Reply"]["properties"]["score"]["type"] == "integer"


@_sync
async def test_model_override_and_empty_override() -> None:
    models: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return _message(GOOD)

    client = _client(handler)
    await client.json_completion("s", "u", Reply, model="claude-other")
    await client.json_completion("s", "u", Reply, model="")

    assert models == ["claude-other", "claude-test"]


@_sync
async def test_invalid_payload_for_schema_raises() -> None:
    client = _client(lambda r: _message(json.dumps({"title": "x"})))
    with pytest.raises(LlmResponseError, match="Reply"):
        await client.json_completion("s", "u", Reply)


@_sync
async def test_non_json_text_raises() -> None:
    client = _client(lambda r: _message("sorry, no"))
    with pytest.raises(LlmResponseError):
        await client.json_completion("s", "u", Reply)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
@_sync
async def test_refusal_and_truncation_raise(stop_reason: str) -> None:
    client = _client(lambda r: _message(GOOD, stop_reason=stop_reason))
    with pytest.raises(LlmResponseError, match=stop_reason):
        await client.json_completion("s", "u", Reply)


@_sync
async def test_malformed_envelope_raises() -> None:
    client = _client(lambda r: httpx.Response(200, json={"content": []}))
    with pytest.raises(LlmResponseError, match="malformed"):
        await client.json_completion("s", "u", Reply)


@_sync
async def test_401_is_a_clear_auth_error() -> None:
    client = _client(lambda r: httpx.Response(401, json={"error": {"message": "bad"}}))
    with pytest.raises(LlmResponseError, match="ANTHROPIC_API_KEY") as info:
        await client.json_completion("s", "u", Reply)
    assert info.value.status == 401


@_sync
async def test_other_http_error_carries_status() -> None:
    client = _client(lambda r: httpx.Response(400, json={}))
    with pytest.raises(LlmResponseError) as info:
        await client.json_completion("s", "u", Reply)
    assert info.value.status == 400
    assert str(info.value) == "Anthropic API returned HTTP 400"


@_sync
async def test_overloaded_5xx_is_retried_then_carries_the_provider_message() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            529 if len(calls) == 1 else 503,
            json={"type": "error", "error": {"type": "x", "message": "Overloaded"}},
        )

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _client(handler, sleep=fake_sleep, jitter=lambda: 0.0)
    with pytest.raises(LlmResponseError) as info:
        await client.json_completion("s", "u", Reply)

    assert info.value.status == 503
    assert str(info.value) == "Anthropic API returned HTTP 503: Overloaded"
    assert len(calls) == 3
    assert slept == [2.0, 5.0]


@_sync
async def test_timeout_then_success_is_retried() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("connect timed out")
        return _message(GOOD)

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _client(handler, sleep=fake_sleep, jitter=lambda: 0.0)
    result = await client.json_completion("s", "u", Reply)

    assert result.title == "ML Engineer"
    assert slept == [2.0]


@_sync
async def test_429_retries_once_honouring_retry_after() -> None:
    calls: list[int] = []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, json={})
        return _message(GOOD)

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _client(handler, sleep=fake_sleep, retry_429_wait_seconds=25.0)
    result = await client.json_completion("s", "u", Reply)

    assert result.title == "ML Engineer"
    assert len(calls) == 2
    assert slept == [7.0]


@_sync
async def test_429_without_retry_after_uses_configured_wait_then_raises() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = _client(
        lambda r: httpx.Response(429, json={}),
        sleep=fake_sleep,
        retry_429_wait_seconds=25.0,
    )
    with pytest.raises(LlmResponseError) as info:
        await client.json_completion("s", "u", Reply)

    assert info.value.status == 429
    assert slept == [25.0]


@_sync
async def test_429_retry_disabled_when_wait_is_zero() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={})

    client = _client(handler, retry_429_wait_seconds=0.0)
    with pytest.raises(LlmResponseError):
        await client.json_completion("s", "u", Reply)
    assert len(calls) == 1


def test_client_does_not_support_web_search() -> None:
    assert supports_web_search(_client(lambda r: _message(GOOD))) is False


# --- settings / factory ------------------------------------------------------


def test_anthropic_is_an_allowed_provider_and_key_comes_from_env(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(llm_provider="anthropic", ANTHROPIC_API_KEY="sk-ant-x")
    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-x"


def test_factory_builds_anthropic_client(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(
        llm_provider="anthropic", ANTHROPIC_API_KEY="sk-ant-x", anthropic_model="m1"
    )
    client = build_llm_client(settings)
    assert isinstance(client, AnthropicLlmClient)
    assert client.model == "m1"


def test_factory_anthropic_without_key_fails_clearly(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(llm_provider="anthropic", ANTHROPIC_API_KEY=None)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_llm_client(settings)


def test_factory_keeps_other_providers(
    clean_settings: Callable[..., Settings],
) -> None:
    assert isinstance(
        build_llm_client(clean_settings(llm_provider="openai_compatible")),
        OpenAiCompatibleLlmClient,
    )
    assert isinstance(
        build_llm_client(clean_settings(llm_provider="claude_code")),
        ClaudeCodeLlmClient,
    )


def test_describe_setup_anthropic_names_models_never_the_key(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(
        llm_provider="anthropic",
        ANTHROPIC_API_KEY="sk-ant-SECRET",
        anthropic_model="m1",
        judge_model="m2",
    )
    line = describe_llm_setup(settings)
    assert line == "LLM: proveedor=anthropic · extracción=m1 · juez=m2"
    assert "SECRET" not in line


def test_describe_setup_openai_compatible_unchanged(
    clean_settings: Callable[..., Settings],
) -> None:
    line = describe_llm_setup(clean_settings(llm_provider="openai_compatible"))
    assert line.startswith("LLM: proveedor=openai_compatible · endpoint=api.openai.com")


def test_anthropic_key_is_never_read_from_toml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toml = tmp_path / "c.toml"
    toml.write_text(
        'anthropic_api_key = "from-toml"\nANTHROPIC_API_KEY = "from-toml"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(
        CVA_TELEGRAM_TOKEN="t",  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[call-arg]
        toml_file=str(toml),  # type: ignore[call-arg]
    )
    assert settings.anthropic_api_key is None


# --- claude child env --------------------------------------------------------


def test_child_env_strips_anthropic_credentials_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("HOME", "/home/owner")
    monkeypatch.setenv("CVA_LLM_API_KEY", "x")

    env = _child_env()

    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CVA_LLM_API_KEY" not in env
    assert env["HOME"] == "/home/owner"


def test_child_env_strips_anthropic_credentials_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("anthropic_api_key", "sk-ant-1")
    assert not any(k.upper() == "ANTHROPIC_API_KEY" for k in _child_env())


def test_child_env_keeps_anthropic_credentials_with_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("CVA_LLM_API_KEY", "x")

    env = _child_env(pass_api_key=True)

    assert env["ANTHROPIC_API_KEY"] == "sk-ant-1"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "tok"
    assert "CVA_LLM_API_KEY" not in env  # opt-in never leaks app secrets


def test_claude_code_pass_api_key_defaults_false_and_reaches_client(
    clean_settings: Callable[..., Settings],
) -> None:
    assert clean_settings().claude_code_pass_api_key is False
    client = ClaudeCodeLlmClient.from_settings(
        clean_settings(llm_provider="claude_code", claude_code_pass_api_key=True)
    )
    assert client.pass_api_key is True


@_sync
async def test_claude_code_runner_receives_filtered_env_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default runner builds its env via ``_child_env(pass_api_key)``."""
    from cva import llm

    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            return b"{}", b""

    async def fake_exec(*argv: str, **kwargs: object) -> _Proc:
        captured["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-1")
    monkeypatch.setattr(llm.asyncio, "create_subprocess_exec", fake_exec)

    await llm._run_claude_subprocess(["claude"], b"", 5.0)
    env = captured["env"]
    assert isinstance(env, dict)
    assert "ANTHROPIC_API_KEY" not in env

    await llm._run_claude_subprocess(["claude"], b"", 5.0, pass_api_key=True)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-1"
