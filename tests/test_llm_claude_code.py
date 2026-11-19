"""ClaudeCodeLlmClient tests: headless `claude -p` CLI provider.

No real subprocess is ever spawned here (owner subscription safety): every
test injects a fake ``run`` callable matching the runner seam
``Callable[[list[str], bytes, float], Awaitable[tuple[int, bytes, bytes]]]``
and asserts on the argv/stdin shape the client builds, plus how it parses the
`--output-format json` envelope back into the requested Pydantic model.

CLI JSON output shape assumption (unconfirmed by a real invocation — the
owner's T3 smoke test on the bot PC must confirm this): a top-level JSON
object, optionally ``is_error: true`` for failures, and either a
``structured_output`` field carrying the already-parsed object or a
``result`` string that itself contains the JSON payload (parsed with the
same balanced-object scanner the OpenAI-compatible client uses).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import pytest
from pydantic import BaseModel

from cva.config import Settings
from cva.llm import (
    ClaudeCodeLlmClient,
    LlmResponseError,
    OpenAiCompatibleLlmClient,
    _child_env,
    _communicate_with_timeout,
    build_llm_client,
)


class Reply(BaseModel):
    title: str
    must_haves: list[str]


class _FakeRun:
    """Records every call; returns (or raises) a canned outcome."""

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        raises: BaseException | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self.calls: list[tuple[list[str], bytes, float]] = []

    async def __call__(
        self, argv: list[str], stdin_data: bytes, timeout_seconds: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append((argv, stdin_data, timeout_seconds))
        if self.raises is not None:
            raise self.raises
        return self.exit_code, self.stdout, self.stderr


def _envelope(**fields: object) -> bytes:
    return json.dumps(fields).encode("utf-8")


def _success_result_string() -> bytes:
    return _envelope(
        type="result",
        is_error=False,
        result=json.dumps({"title": "ML Engineer", "must_haves": ["python"]}),
    )


def _client(
    run: Callable[[list[str], bytes, float], Awaitable[tuple[int, bytes, bytes]]],
    *,
    claude_code_path: str = "claude",
    model: str | None = None,
    timeout_seconds: float = 180.0,
) -> ClaudeCodeLlmClient:
    return ClaudeCodeLlmClient(
        claude_code_path=claude_code_path,
        model=model,
        timeout_seconds=timeout_seconds,
        run=run,
    )


# --- argv / stdin shape -------------------------------------------------


def test_argv_has_headless_isolated_flags_and_offer_text_goes_via_stdin() -> None:
    run = _FakeRun(stdout=_success_result_string())
    client = _client(run, claude_code_path="/opt/claude/claude")

    asyncio.run(client.json_completion("sys prompt", "confidential offer text", Reply))

    assert len(run.calls) == 1
    argv, stdin_data, _timeout = run.calls[0]
    assert argv[0] == "/opt/claude/claude"
    assert "-p" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--json-schema" in argv
    schema_arg = argv[argv.index("--json-schema") + 1]
    assert json.loads(schema_arg) == Reply.model_json_schema()
    assert "--no-session-persistence" in argv
    assert "--system-prompt" in argv
    assert argv[argv.index("--system-prompt") + 1] == "sys prompt"
    assert stdin_data == b"confidential offer text"
    # The offer text must never leak into argv (process listings are visible
    # to other local users/tools).
    assert "confidential offer text" not in argv


def test_model_flag_present_only_when_configured() -> None:
    run_no_model = _FakeRun(stdout=_success_result_string())
    asyncio.run(_client(run_no_model, model=None).json_completion("sys", "text", Reply))
    argv_no_model, _stdin, _timeout = run_no_model.calls[0]
    assert "--model" not in argv_no_model

    run_with_model = _FakeRun(stdout=_success_result_string())
    asyncio.run(
        _client(run_with_model, model="haiku").json_completion("sys", "text", Reply)
    )
    argv_with_model, _stdin2, _timeout2 = run_with_model.calls[0]
    assert "--model" in argv_with_model
    assert argv_with_model[argv_with_model.index("--model") + 1] == "haiku"


def test_timeout_seconds_forwarded_to_runner() -> None:
    run = _FakeRun(stdout=_success_result_string())
    asyncio.run(_client(run, timeout_seconds=42.0).json_completion("sys", "text", Reply))

    _argv, _stdin, timeout_seconds = run.calls[0]
    assert timeout_seconds == 42.0


# --- per-call model override (llm-offer-judge per-step model selection) ---


def test_model_override_takes_precedence_over_configured_model() -> None:
    run = _FakeRun(stdout=_success_result_string())
    client = _client(run, model="sonnet")  # configured default

    asyncio.run(client.json_completion("sys", "text", Reply, model="haiku"))

    argv, _stdin, _timeout = run.calls[0]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "haiku"


def test_no_override_falls_back_to_configured_model() -> None:
    run = _FakeRun(stdout=_success_result_string())
    client = _client(run, model="sonnet")

    asyncio.run(client.json_completion("sys", "text", Reply))

    argv, _stdin, _timeout = run.calls[0]
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_no_override_and_no_configured_model_omits_the_flag() -> None:
    run = _FakeRun(stdout=_success_result_string())
    client = _client(run, model=None)

    asyncio.run(client.json_completion("sys", "text", Reply))

    argv, _stdin, _timeout = run.calls[0]
    assert "--model" not in argv


# --- success parsing: both output-shape variants ------------------------


def test_success_parses_result_string_variant() -> None:
    run = _FakeRun(stdout=_success_result_string())

    reply = asyncio.run(_client(run).json_completion("sys", "text", Reply))

    assert reply == Reply(title="ML Engineer", must_haves=["python"])


def test_success_parses_structured_output_field_variant() -> None:
    run = _FakeRun(
        stdout=_envelope(
            type="result",
            is_error=False,
            structured_output={"title": "Data Scientist", "must_haves": ["sql"]},
            result="",
        )
    )

    reply = asyncio.run(_client(run).json_completion("sys", "text", Reply))

    assert reply == Reply(title="Data Scientist", must_haves=["sql"])


def test_success_result_string_with_trailing_chat_prose() -> None:
    """Reuses the same balanced-JSON extraction as the OpenAI-compatible
    client for a `result` string that carries prose after the payload."""
    run = _FakeRun(
        stdout=_envelope(
            type="result",
            is_error=False,
            result='{"title": "ML Engineer", "must_haves": ["python"]}\n\nDone.',
        )
    )

    reply = asyncio.run(_client(run).json_completion("sys", "text", Reply))

    assert reply == Reply(title="ML Engineer", must_haves=["python"])


# --- failure modes -------------------------------------------------------


def test_is_error_true_raises_with_result_text() -> None:
    run = _FakeRun(
        stdout=_envelope(
            type="result",
            is_error=True,
            result="Error: usage limit reached, upgrade your plan",
        )
    )

    with pytest.raises(LlmResponseError, match="usage limit reached"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_non_zero_exit_raises_with_trimmed_stderr() -> None:
    run = _FakeRun(exit_code=1, stdout=b"", stderr=b"error: not logged in\n")

    with pytest.raises(LlmResponseError, match="not logged in"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_non_zero_exit_with_json_envelope_surfaces_its_result_text() -> None:
    """A non-zero exit still prints the `--output-format json` envelope on
    stdout (usage-limit/rate-limit wording only shows up there, never in
    stderr) — it must be surfaced over the generic stderr text (robustness
    follow-up)."""
    run = _FakeRun(
        exit_code=1,
        stdout=_envelope(
            type="result",
            is_error=True,
            result="Claude AI usage limit reached, try again later",
        ),
        stderr=b"error: exit status 1\n",
    )

    with pytest.raises(LlmResponseError, match="usage limit reached"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_non_zero_exit_with_json_envelope_includes_api_error_status() -> None:
    run = _FakeRun(
        exit_code=1,
        stdout=_envelope(
            type="result",
            is_error=True,
            result="rate limit exceeded",
            api_error_status="rate_limit_error",
        ),
        stderr=b"",
    )

    with pytest.raises(LlmResponseError, match="rate_limit_error"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_non_zero_exit_with_unparseable_stdout_falls_back_to_stderr() -> None:
    """When stdout is not a JSON envelope at all (crash before the CLI could
    even print one), stderr remains the only signal."""
    run = _FakeRun(
        exit_code=1, stdout=b"not json output", stderr=b"error: not logged in\n"
    )

    with pytest.raises(LlmResponseError, match="not logged in"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_timeout_raises_llm_response_error_naming_the_budget() -> None:
    run = _FakeRun(raises=TimeoutError())

    with pytest.raises(LlmResponseError, match="180"):
        asyncio.run(_client(run, timeout_seconds=180.0).json_completion("sys", "text", Reply))


def test_file_not_found_raises_actionable_error_naming_claude_code_path() -> None:
    run = _FakeRun(raises=FileNotFoundError("no such file"))

    with pytest.raises(LlmResponseError, match="/does/not/exist/claude"):
        asyncio.run(
            _client(run, claude_code_path="/does/not/exist/claude").json_completion(
                "sys", "text", Reply
            )
        )


def test_invalid_json_stdout_raises() -> None:
    run = _FakeRun(stdout=b"not json at all")

    with pytest.raises(LlmResponseError):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_result_string_without_any_json_object_raises() -> None:
    run = _FakeRun(
        stdout=_envelope(
            type="result", is_error=False, result="Lo siento, no puedo ayudar con eso."
        )
    )

    with pytest.raises(LlmResponseError, match="JSON"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


def test_result_string_without_json_labels_envelope_type_not_finish_reason() -> None:
    """Claude Code has no OpenAI-style finish_reason; the diagnostic must
    name the envelope's own ``type`` field under its own label instead of
    the misleading "finish_reason" (llm-provider robustness follow-up)."""
    run = _FakeRun(
        stdout=_envelope(
            type="result", is_error=False, result="Lo siento, no puedo ayudar con eso."
        )
    )

    with pytest.raises(LlmResponseError) as excinfo:
        asyncio.run(_client(run).json_completion("sys", "text", Reply))

    assert "envelope_type='result'" in str(excinfo.value)
    assert "finish_reason" not in str(excinfo.value)


def test_schema_mismatch_raises() -> None:
    run = _FakeRun(
        stdout=_envelope(
            type="result",
            is_error=False,
            result=json.dumps({"title": "ML Engineer"}),  # missing must_haves
        )
    )

    with pytest.raises(LlmResponseError, match="Reply"):
        asyncio.run(_client(run).json_completion("sys", "text", Reply))


# --- from_settings + model cache key -------------------------------------


def test_from_settings_wires_path_model_and_timeout(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("CVA_LLM_PROVIDER", "claude_code")
    monkeypatch.setenv("CVA_CLAUDE_CODE_PATH", "/usr/local/bin/claude")
    monkeypatch.setenv("CVA_CLAUDE_CODE_MODEL", "sonnet")
    monkeypatch.setenv("CVA_CLAUDE_CODE_TIMEOUT_SECONDS", "60")
    settings = clean_settings(CVA_LLM_API_KEY=None)

    client = ClaudeCodeLlmClient.from_settings(settings)

    assert client.claude_code_path == "/usr/local/bin/claude"
    assert client.claude_code_model == "sonnet"
    assert client.timeout_seconds == 60.0
    assert client.model == "claude-code:sonnet"


def test_model_cache_key_defaults_when_no_model_configured() -> None:
    run = _FakeRun(stdout=_success_result_string())
    client = _client(run, model=None)

    assert client.model == "claude-code:default"


# --- build_llm_client factory ---------------------------------------------


def test_factory_default_provider_returns_claude_code_client(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(CVA_LLM_API_KEY=None)

    client = build_llm_client(settings)

    assert isinstance(client, ClaudeCodeLlmClient)


def test_factory_openai_compatible_provider_returns_openai_compatible_client(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(llm_provider="openai_compatible")

    client = build_llm_client(settings)

    assert isinstance(client, OpenAiCompatibleLlmClient)


def test_factory_claude_code_provider_returns_claude_code_client(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    monkeypatch.setenv("CVA_LLM_PROVIDER", "claude_code")
    settings = clean_settings(CVA_LLM_API_KEY=None)

    client = build_llm_client(settings)

    assert isinstance(client, ClaudeCodeLlmClient)


# --- subprocess cancellation: never leave an orphan `claude` process -------


class _FakeProcess:
    """Process-like stand-in for ``_communicate_with_timeout`` — no real
    subprocess is ever spawned; this proves the kill/wait sequence runs on
    cancellation exactly like it already does on timeout."""

    def __init__(self, *, communicate_error: BaseException | None = None) -> None:
        self._communicate_error = communicate_error
        self.killed = False
        self.waited = False

    async def communicate(self, stdin_data: bytes) -> tuple[bytes, bytes]:
        if self._communicate_error is not None:
            raise self._communicate_error
        return b"out", b"err"

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return 0


def test_communicate_with_timeout_kills_and_waits_on_cancellation() -> None:
    process = _FakeProcess(communicate_error=asyncio.CancelledError())

    async def _run() -> None:
        await _communicate_with_timeout(process, b"in", 30.0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run())

    assert process.killed is True
    assert process.waited is True


def test_communicate_with_timeout_kills_and_waits_on_timeout() -> None:
    process = _FakeProcess(communicate_error=TimeoutError())

    async def _run() -> None:
        await _communicate_with_timeout(process, b"in", 30.0)

    with pytest.raises(TimeoutError):
        asyncio.run(_run())

    assert process.killed is True
    assert process.waited is True


def test_communicate_with_timeout_returns_stdout_stderr_on_success() -> None:
    process = _FakeProcess()

    stdout, stderr = asyncio.run(_communicate_with_timeout(process, b"in", 30.0))

    assert (stdout, stderr) == (b"out", b"err")
    assert process.killed is False


# --- child env: inherits the OS env, never leaks CVA_* secrets --------------


def test_child_env_inherits_os_vars_and_excludes_cva_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Denylist, not allowlist: the real CLI may need OS variables nobody
    listed in advance (Windows USERNAME/HOMEDRIVE/ProgramFiles, proxy and CA
    bundle settings...), so everything is inherited except this app's own
    CVA_* secrets."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/owner")
    monkeypatch.setenv("USERNAME", "owner")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.local:3128")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/corp.pem")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk-super-secret")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok-secret")
    monkeypatch.setenv("CVA_BRIDGE_SECRET", "bridge-secret")

    env = _child_env()

    assert env.get("PATH") == "/usr/bin:/bin"
    assert env.get("HOME") == "/home/owner"
    assert env.get("USERNAME") == "owner"
    assert env.get("HTTPS_PROXY") == "http://proxy.local:3128"
    assert env.get("NODE_EXTRA_CA_CERTS") == "/etc/ssl/corp.pem"
    assert all(not key.upper().startswith("CVA_") for key in env)


def test_child_env_excludes_cva_prefix_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows environment names are case-insensitive: a lower/mixed-case
    CVA_ key is the same secret and must not leak either."""
    monkeypatch.setenv("cva_llm_api_key", "sk-lower")
    monkeypatch.setenv("Cva_Bridge_Secret", "mixed")

    env = _child_env()

    assert not any(key.upper().startswith("CVA_") for key in env)


def test_child_env_excludes_cva_prefixed_keys_regardless_of_which_ones_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVA_CLAUDE_CODE_PATH", "/usr/local/bin/claude")
    monkeypatch.setenv("CVA_MATCH_MODE", "hybrid")

    env = _child_env()

    assert not any(key.startswith("CVA_") for key in env)


class _Draft(BaseModel):
    text: str


def test_double_encoded_text_field_is_unwrapped() -> None:
    """Live follow-up bug (2026-11-19): the model put the whole JSON answer
    inside the ``text`` field, so Telegram showed ``{"text": "Hola,\\n\\n..."}``."""
    inner = json.dumps({"text": "Hola,\n\nSigo interesado.\n\nUn saludo"})
    run = _FakeRun(
        stdout=_envelope(
            type="result", is_error=False, structured_output={"text": inner}, result=""
        )
    )

    reply = asyncio.run(_client(run).json_completion("sys", "text", _Draft))

    assert reply.text == "Hola,\n\nSigo interesado.\n\nUn saludo"


def test_text_that_merely_starts_with_a_brace_is_kept() -> None:
    run = _FakeRun(
        stdout=_envelope(
            type="result",
            is_error=False,
            structured_output={"text": '{"other": "x"} es un ejemplo'},
            result="",
        )
    )

    reply = asyncio.run(_client(run).json_completion("sys", "text", _Draft))

    assert reply.text == '{"other": "x"} es un ejemplo'
