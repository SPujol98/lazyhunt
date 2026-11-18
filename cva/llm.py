"""LLM seam: ``LlmClient`` protocol + three implementations.

``OpenAiCompatibleLlmClient`` makes one HTTP attempt per call (temperature 0,
JSON-schema response format) with a single bounded retry for transient HTTP
429 only; every other retry policy belongs to callers. The endpoint is
provider-swappable via Settings (``CVA_LLM_BASE_URL`` / ``CVA_LLM_MODEL``).
Content parsing extracts the first balanced JSON object, tolerating trailing
chat prose and code fences; ``CVA_LLM_DISABLE_THINKING`` sends
``"thinking": {"type": "disabled"}`` for GLM-style endpoints.

``ClaudeCodeLlmClient`` (claude-code-llm-provider) instead shells out to the
local `claude` CLI in headless mode (`claude -p --output-format json`),
billed against the owner's Claude subscription instead of API usage.
``AnthropicLlmClient`` talks to the Anthropic Messages API over raw httpx
(structured output via ``output_config.format``; no SDK dependency).
``build_llm_client`` selects between them from ``Settings.llm_provider``.
"""

import asyncio
import functools
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from cva.config import DEFAULT_ANTHROPIC_MODEL, Settings

logger = logging.getLogger(__name__)


def describe_llm_setup(settings: Settings) -> str:
    """One Spanish line naming the effective LLM setup — never a secret.

    Printed at the start of ``cli.py rejudge``/``judge-eval`` and logged at
    bot startup (shared-config T2), so a wrong provider (e.g. a stray
    ``CVA_LLM_PROVIDER`` in the environment) is visible before any call
    fails confusingly downstream.
    """
    if settings.llm_provider == "claude_code":
        default = settings.claude_code_model or "default"
        extraction = settings.extraction_model or default
        judge = settings.judge_model or default
        return f"LLM: proveedor=claude_code · extracción={extraction} · juez={judge}"
    if settings.llm_provider == "anthropic":
        default = settings.anthropic_model or DEFAULT_ANTHROPIC_MODEL
        extraction = settings.extraction_model or default
        judge = settings.judge_model or default
        return f"LLM: proveedor=anthropic · extracción={extraction} · juez={judge}"
    host = urlparse(settings.llm_base_url).hostname or settings.llm_base_url
    extraction = settings.extraction_model or settings.llm_model
    judge = settings.judge_model or settings.llm_model
    return (
        f"LLM: proveedor=openai_compatible · endpoint={host} · "
        f"extracción={extraction} · juez={judge}"
    )


# Credentials of the Anthropic API that would silently move the `claude` CLI
# from the user's subscription to API billing.
_ANTHROPIC_CREDENTIAL_VARS = frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"})


def _child_env(pass_api_key: bool = False) -> dict[str, str]:
    """Environment for the `claude` subprocess: the parent's, minus secrets.

    Inherits every OS variable (the CLI may need ones nobody listed in
    advance: Windows USERNAME/HOMEDRIVE/ProgramFiles, proxies, CA bundles...)
    and drops every ``CVA_*`` variable (``CVA_LLM_API_KEY``,
    ``CVA_TELEGRAM_TOKEN``, ``CVA_BRIDGE_SECRET``, ...) — the child never
    needs this app's own secrets. It also drops ``ANTHROPIC_API_KEY`` /
    ``ANTHROPIC_AUTH_TOKEN`` unless ``pass_api_key`` (setting
    ``claude_code_pass_api_key``) opts in, so the CLI keeps using the user's
    own login instead of silently switching to API billing. Name checks are
    case-insensitive because Windows environment names are.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("CVA_")
        and (pass_api_key or key.upper() not in _ANTHROPIC_CREDENTIAL_VARS)
    }


class LlmClient(Protocol):
    """Structured-completion seam: the caller supplies the response schema.

    Method-generic (not class-generic): one client instance answers ANY
    schema per call (extraction's ``OfferExtraction``, the judge's
    ``OfferJudgment``, ...) — matching both concrete implementations below,
    whose ``json_completion`` is itself generic over the schema argument.
    """

    model: str  # part of the extraction cache key (sha256(text+model+prompt))

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        """Return a parsed model instance for the requested schema.

        ``model`` overrides the client's configured default for this call
        only (claude-code-llm-provider per-step model selection): ``None``
        keeps the configured default (``self.model`` / ``claude_code_model``).
        """
        ...


def _resolve_model_override(override: str | None, default: str | None) -> str | None:
    """Empty-string override means "no override" — treated exactly like
    ``None`` in both LlmClient implementations (robustness follow-up: they
    used to disagree — ``ClaudeCodeLlmClient`` already fell back via a
    plain ``or``, ``OpenAiCompatibleLlmClient`` sent the empty string
    through as a real model name).
    """
    return override if override else default


class LlmResponseError(RuntimeError):
    """Raised when the endpoint reply is not a usable structured completion.

    ``status`` carries the HTTP status code for HTTP failures so callers can
    distinguish them (e.g. a 429 rate limit); it is ``None`` for non-HTTP
    failures such as malformed payloads.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def supports_web_search(client: object) -> bool:
    """True when the provider can run a completion with web search enabled.

    Only the Claude Code CLI provider can (``WebSearch``/``WebFetch`` tools);
    the OpenAI-compatible endpoint has no such capability here, so callers
    must say "not available" instead of asking it to guess.
    """
    return getattr(client, "supports_web_search", False) is True


class OpenAiCompatibleLlmClient:
    """OpenAI-compatible chat-completions client built from Settings."""

    supports_web_search = False

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        disable_thinking: bool = False,
        retry_429_wait_seconds: float = 25.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._disable_thinking = disable_thinking
        self._retry_wait_seconds = retry_429_wait_seconds
        self._sleep = sleep if sleep is not None else asyncio.sleep

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "OpenAiCompatibleLlmClient":
        """Wire the client from Settings (base_url/api_key/model).

        ``llm_api_key`` is optional at the Settings layer (the claude_code
        provider never needs it), so this is the fail-fast boundary for the
        openai_compatible provider instead: a missing key raises immediately
        with an actionable message rather than surfacing as a confusing 401
        on the first real request.
        """
        if settings.llm_api_key is None:
            raise ValueError(
                "CVA_LLM_API_KEY is required when llm_provider='openai_compatible' "
                "(set llm_provider='claude_code' to use the Claude Code CLI instead)"
            )
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            transport=transport,
            disable_thinking=settings.llm_disable_thinking,
            retry_429_wait_seconds=float(settings.llm_429_retry_wait_seconds),
        )

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        payload: dict[str, object] = {
            "model": _resolve_model_override(model, self.model),
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._disable_thinking:
            # GLM/OpenAI-compatible extension (no effect on endpoints that
            # ignore the extra body field).
            payload["thinking"] = {"type": "disabled"}
        response = await self._send(payload)
        if response.status_code == 429 and self._retry_wait_seconds > 0:
            # ONE bounded retry for transient HTTP 429 only; the D3 no-retry
            # rule covers LinkedIn walls, not the LLM endpoint.
            await self._sleep(self._retry_wait_seconds)
            response = await self._send(payload)
        if response.status_code != 200:
            raise LlmResponseError(
                f"LLM endpoint returned HTTP {response.status_code}",
                status=response.status_code,
            )
        return _parse_completion(response, schema)

    async def _send(self, payload: dict[str, object]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            return await client.post("/chat/completions", json=payload)


# Messages API version header. 2023-06-01 is the stable version Anthropic
# documents; re-check https://docs.anthropic.com/en/api/versioning before
# bumping it (the structured-output fields below are sent under it).
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_MAX_TOKENS = 4096
_RETRY_AFTER_CAP_SECONDS = 120.0

# JSON-schema keywords the structured-output grammar rejects; pydantic emits
# them for Field(min_length=..., ge=..., ...). The pydantic validation of the
# parsed reply still enforces them.
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
    }
)


def _anthropic_schema(node: object) -> object:
    """Copy of a pydantic JSON schema made acceptable to structured output:
    unsupported keywords dropped, ``additionalProperties: false`` on every
    object."""
    if isinstance(node, list):
        return [_anthropic_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {
        key: _anthropic_schema(value)
        for key, value in node.items()
        if key not in _UNSUPPORTED_SCHEMA_KEYWORDS
    }
    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned["additionalProperties"] = False
    return cleaned


class AnthropicLlmClient:
    """Native Anthropic Messages API client (raw httpx, no SDK).

    Structured output uses ``output_config.format`` (``json_schema``). No
    sampling parameters are sent (newer models reject them). Failure mapping
    mirrors ``OpenAiCompatibleLlmClient``: one bounded retry on HTTP 429
    (``retry-after`` honoured, capped), a clear message on 401, and
    ``LlmResponseError`` for refusals/truncation/malformed replies.
    """

    supports_web_search = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = ANTHROPIC_BASE_URL,
        max_tokens: int = ANTHROPIC_MAX_TOKENS,
        timeout_seconds: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_429_wait_seconds: float = 25.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._retry_wait_seconds = retry_429_wait_seconds
        self._sleep = sleep if sleep is not None else asyncio.sleep

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "AnthropicLlmClient":
        """Wire the client from Settings; a missing key fails fast here."""
        if settings.anthropic_api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when llm_provider='anthropic' "
                "(set llm_provider='claude_code' to use the Claude Code CLI instead)"
            )
        return cls(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.anthropic_model or DEFAULT_ANTHROPIC_MODEL,
            transport=transport,
            retry_429_wait_seconds=float(settings.llm_429_retry_wait_seconds),
        )

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        payload: dict[str, object] = {
            "model": _resolve_model_override(model, self.model),
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _anthropic_schema(schema.model_json_schema()),
                }
            },
        }
        response = await self._send(payload)
        if response.status_code == 429 and self._retry_wait_seconds > 0:
            await self._sleep(self._retry_delay(response))
            response = await self._send(payload)
        if response.status_code == 401:
            raise LlmResponseError(
                "Anthropic API rejected the credentials (HTTP 401): "
                "check ANTHROPIC_API_KEY",
                status=401,
            )
        if response.status_code != 200:
            raise LlmResponseError(
                f"Anthropic API returned HTTP {response.status_code}",
                status=response.status_code,
            )
        return _parse_anthropic(response, schema)

    def _retry_delay(self, response: httpx.Response) -> float:
        header = response.headers.get("retry-after")
        try:
            seconds = float(header) if header is not None else None
        except ValueError:
            seconds = None
        if seconds is None or seconds < 0:
            return self._retry_wait_seconds
        return min(seconds, _RETRY_AFTER_CAP_SECONDS)

    async def _send(self, payload: dict[str, object]) -> httpx.Response:
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            return await client.post("/v1/messages", json=payload)


def _parse_anthropic[CompletionT: BaseModel](
    response: httpx.Response, schema: type[CompletionT]
) -> CompletionT:
    try:
        envelope = response.json()
        stop_reason: object = envelope.get("stop_reason")
        blocks: object = envelope["content"]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LlmResponseError("malformed Anthropic message envelope") from exc
    if stop_reason in ("refusal", "max_tokens"):
        raise LlmResponseError(f"Anthropic reply not usable: stop_reason={stop_reason}")
    text = ""
    if isinstance(blocks, list):
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    if not text:
        raise LlmResponseError("malformed Anthropic message envelope: no text content")
    data = _extract_json_object(text, finish_reason=str(stop_reason))
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LlmResponseError(
            f"completion does not satisfy {schema.__name__}"
        ) from exc


# Runner seam for ClaudeCodeLlmClient: argv, stdin bytes, timeout seconds ->
# (exit code, stdout bytes, stderr bytes). Tests inject a fake; the default
# implementation below is the only one that ever spawns a real subprocess.
_WEB_TOOLS = "WebSearch,WebFetch"
WEB_TIMEOUT_FLOOR_SECONDS: float = 300.0  # minimum timeout for web-tool calls

ClaudeCodeRunner = Callable[
    [list[str], bytes, float], Awaitable[tuple[int, bytes, bytes]]
]


class _ProcessLike(Protocol):
    """The slice of ``asyncio.subprocess.Process`` this module depends on —
    lets tests exercise ``_communicate_with_timeout`` with a plain fake
    instead of a real subprocess."""

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


async def _communicate_with_timeout(
    process: _ProcessLike, stdin_data: bytes, timeout_seconds: float
) -> tuple[bytes, bytes]:
    """Await ``process.communicate()`` with a bound; kill + await exit on
    EITHER a timeout OR the awaiting task being cancelled, then re-raise.

    Without the ``CancelledError`` branch, a cancelled caller (e.g. the bot
    process shutting down mid-analysis) would abandon this ``await`` and
    leave the `claude` child running as an orphan — never billed against a
    timeout, never reaped (advisory follow-up).
    """
    try:
        return await asyncio.wait_for(
            process.communicate(stdin_data), timeout=timeout_seconds
        )
    except (TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise


async def _run_claude_subprocess(
    argv: list[str],
    stdin_data: bytes,
    timeout_seconds: float,
    *,
    pass_api_key: bool = False,
) -> tuple[int, bytes, bytes]:
    """Real `claude` CLI invocation: no shell, isolated cwd, bounded wait.

    The subprocess runs in a fresh temporary directory (never the repo
    working directory) so it never picks up this project's CLAUDE.md or
    project-scoped settings, on top of the CLI-side isolation flags the
    caller already passes (``--setting-sources ""``, ``--strict-mcp-config``,
    ``--tools ""``), and a filtered environment (see ``_child_env``) that
    never carries this app's own ``CVA_*`` secrets.
    """
    with tempfile.TemporaryDirectory(prefix="cva-claude-code-") as neutral_cwd:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=neutral_cwd,
            env=_child_env(pass_api_key),
        )
        stdout, stderr = await _communicate_with_timeout(
            process, stdin_data, timeout_seconds
        )
        return process.returncode or 0, stdout, stderr


def _trim(text: bytes | str, *, limit: int = 300) -> str:
    """Flatten to one line and truncate — safe to embed in error messages."""
    decoded = (
        text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    )
    flattened = decoded.replace("\n", " ").replace("\r", " ").strip()
    return flattened[:limit]


class ClaudeCodeLlmClient:
    """Headless `claude -p` CLI provider (claude-code-llm-provider).

    Billed against the owner's Claude subscription instead of API usage.
    Every call is isolated: no tools, no MCP servers, no session
    persistence, no project/user settings or CLAUDE.md — see
    ``_build_argv`` and ``_run_claude_subprocess``. The offer text (the
    ``user`` argument) always goes via stdin, never argv, both to dodge
    Windows command-line length limits and to keep it out of process
    listings.

    CLI JSON output shape (confirmed against a real invocation 2026-09-25):
    ``--output-format json`` prints one top-level JSON object with
    ``is_error: bool`` and either a ``structured_output`` field (the
    already-parsed object, preferred when present) or a ``result`` string
    that itself contains the JSON payload. On a non-zero exit the same
    envelope is still printed to stdout when the CLI got far enough to
    build one (usage-limit/rate-limit wording lives there, not in stderr)
    — see :func:`_exit_error_message` below.
    """

    model: str  # cache key, e.g. "claude-code:sonnet" / "claude-code:default"
    supports_web_search = True  # opt-in per call via web_json_completion

    def __init__(
        self,
        *,
        claude_code_path: str = "claude",
        model: str | None = None,
        timeout_seconds: float = 180.0,
        run: ClaudeCodeRunner | None = None,
        pass_api_key: bool = False,
    ) -> None:
        self.claude_code_path = claude_code_path
        self.pass_api_key = pass_api_key
        self.claude_code_model = model
        self.timeout_seconds = timeout_seconds
        # WebSearch/WebFetch runs fetch several pages; the tool-less timeout
        # is too tight for them, so web calls get at least this floor.
        self.web_timeout_seconds = max(timeout_seconds, WEB_TIMEOUT_FLOOR_SECONDS)
        self.model = f"claude-code:{model or 'default'}"
        self._run: ClaudeCodeRunner = (
            run
            if run is not None
            else functools.partial(_run_claude_subprocess, pass_api_key=pass_api_key)
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeCodeLlmClient":
        """Wire the client from Settings (path/model/timeout)."""
        return cls(
            claude_code_path=settings.claude_code_path,
            model=settings.claude_code_model,
            timeout_seconds=float(settings.claude_code_timeout_seconds),
            pass_api_key=settings.claude_code_pass_api_key,
        )

    def _build_argv(
        self,
        system: str,
        schema: type[BaseModel],
        *,
        model: str | None,
        web: bool = False,
    ) -> list[str]:
        schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        argv = [
            self.claude_code_path,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--no-session-persistence",
            # Isolation: no user/project/local settings or CLAUDE.md, no MCP
            # servers beyond an (unset) --mcp-config, no tool use at all.
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--tools",
            _WEB_TOOLS if web else "",
            "--system-prompt",
            system,
        ]
        if web:
            # Headless mode cannot prompt for permission: pre-approve ONLY
            # the two read-only web tools (never Bash/Edit/MCP).
            argv += ["--allowedTools", _WEB_TOOLS]
        if model:
            argv += ["--model", model]
        return argv

    async def web_json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        """Like :meth:`json_completion` but with WebSearch/WebFetch enabled
        (salary research, explicit owner button press only)."""
        return await self._complete(system, user, schema, model=model, web=True)

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        return await self._complete(system, user, schema, model=model, web=False)

    async def _complete[ModelT: BaseModel](
        self,
        system: str,
        user: str,
        schema: type[ModelT],
        *,
        model: str | None,
        web: bool,
    ) -> ModelT:
        # Per-call override wins over the client's configured default model
        # (claude-code-llm-provider per-step model selection); "" is treated
        # exactly like None (_resolve_model_override), consistently with
        # OpenAiCompatibleLlmClient.
        resolved_model = _resolve_model_override(model, self.claude_code_model)
        argv = self._build_argv(system, schema, model=resolved_model, web=web)
        try:
            exit_code, stdout, stderr = await self._run(
                argv,
                user.encode("utf-8"),
                self.web_timeout_seconds if web else self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise LlmResponseError(
                f"claude executable not found at {self.claude_code_path!r} "
                "(check claude_code_path in config.toml / CVA_CLAUDE_CODE_PATH)"
            ) from exc
        except TimeoutError as exc:
            raise LlmResponseError(
                f"claude CLI timed out after {self.timeout_seconds}s "
                "(check claude_code_timeout_seconds)"
            ) from exc
        if exit_code != 0:
            raise LlmResponseError(_exit_error_message(exit_code, stdout, stderr))
        return _parse_claude_output(stdout, schema)


def _exit_error_message(exit_code: int, stdout: bytes, stderr: bytes) -> str:
    """Non-zero exit: prefer the stdout `--output-format json` envelope's
    own error text over stderr — usage-limit/rate-limit wording (and the
    dedicated ``api_error_status`` field, when present) only ever shows up
    there, never in stderr (robustness follow-up). Falls back to trimmed
    stderr when stdout is empty or not a JSON object at all (e.g. the CLI
    crashed before it could print an envelope).
    """
    try:
        envelope = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        envelope = None
    if isinstance(envelope, dict):
        parts: list[str] = []
        result = envelope.get("result")
        if isinstance(result, str) and result.strip():
            parts.append(_trim(result))
        api_error_status = envelope.get("api_error_status")
        if api_error_status:
            parts.append(f"api_error_status={api_error_status}")
        if parts:
            return f"claude CLI exited with status {exit_code}: {'; '.join(parts)}"
    return f"claude CLI exited with status {exit_code}: {_trim(stderr)}"


def _parse_claude_output[CompletionT: BaseModel](
    stdout: bytes, schema: type[CompletionT]
) -> CompletionT:
    try:
        envelope = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LlmResponseError(
            f"claude CLI stdout is not valid JSON: {_trim(stdout)}"
        ) from exc
    if not isinstance(envelope, dict):
        raise LlmResponseError("claude CLI stdout is not a JSON object")
    if envelope.get("is_error"):
        detail = envelope.get("result")
        raise LlmResponseError(
            f"claude CLI reported an error: {_trim(detail) if detail else 'unknown error'}"
        )
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        data: object = structured
    else:
        result = envelope.get("result")
        if not isinstance(result, str):
            raise LlmResponseError(
                "claude CLI output has neither structured_output nor a result string"
            )
        data = _extract_json_object(
            result, finish_reason=envelope.get("type"), label="envelope_type"
        )
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LlmResponseError(
            f"claude CLI output does not satisfy {schema.__name__}"
        ) from exc


def build_llm_client(
    settings: Settings,
) -> "OpenAiCompatibleLlmClient | ClaudeCodeLlmClient | AnthropicLlmClient":
    """Single factory used at every construction site (service/bot/cli).

    Selects the provider from ``settings.llm_provider`` (default
    ``"claude_code"``).
    """
    if settings.llm_provider == "claude_code":
        return ClaudeCodeLlmClient.from_settings(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicLlmClient.from_settings(settings)
    return OpenAiCompatibleLlmClient.from_settings(settings)


def _parse_completion[CompletionT: BaseModel](
    response: httpx.Response, schema: type[CompletionT]
) -> CompletionT:
    try:
        envelope = response.json()
        content: object = envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmResponseError("malformed chat-completion envelope") from exc
    if not isinstance(content, str):
        raise LlmResponseError("completion content is not a string")
    choice: object = envelope["choices"][0]
    finish_reason: str | None = (
        choice.get("finish_reason") if isinstance(choice, dict) else None
    )
    data = _extract_json_object(content, finish_reason=finish_reason)
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise LlmResponseError(
            f"completion does not satisfy {schema.__name__}"
        ) from exc


def _no_json_diagnostic(
    content: str, finish_reason: str | None, *, label: str = "finish_reason"
) -> str:
    """Single-line diagnostic for a no-JSON completion (observed live: a
    Catalan offer came back as pure markdown prose, finish_reason=stop).

    ``label`` names what ``finish_reason`` actually holds for THIS caller —
    the OpenAI-compatible client passes a real finish_reason (the default
    label), while the claude-code client has no such concept and passes its
    envelope's own ``type`` field under the label ``"envelope_type"``
    instead, so the message never claims a finish_reason that was never
    there (robustness follow-up: misleading finish_reason label).

    The head is flattened to one line and truncated so the message is safe
    to embed in chat-facing failure cards.
    """
    head = content[:200].replace("\n", " ").replace("\r", " ")
    return (
        "completion content contains no JSON object "
        f"({label}={finish_reason!r}, content head: {head!r})"
    )


def _extract_json_object(
    content: str, *, finish_reason: str | None = None, label: str = "finish_reason"
) -> object:
    """Parse the first balanced JSON object out of a completion string.

    Real endpoints append chat prose after the JSON payload (observed live:
    ``'{"ok": true, ...}\\n\\n¿Necesitas ayuda con algo más...?'``), and some
    wrap it in code fences or pad it with whitespace. A strict
    ``json.loads`` on the whole content crashes, so this scans for the
    first ``{`` and tracks string/escape state until its matching ``}``.
    """
    start = content.find("{")
    if start == -1:
        logger.debug(
            "no JSON object in completion: length=%d head=%r tail=%r",
            len(content),
            content[:200],
            content[-200:],
        )
        raise LlmResponseError(_no_json_diagnostic(content, finish_reason, label=label))
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(content[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise LlmResponseError(
                        "completion content is not valid JSON"
                    ) from exc
    raise LlmResponseError("completion content has an unbalanced JSON object")
