"""LLM seam: ``LlmClient`` protocol + three implementations.

``OpenAiCompatibleLlmClient`` makes one HTTP call per completion (temperature 0,
JSON-schema response format) with a single bounded retry for HTTP 429 and a
small bounded exponential backoff with jitter for transient failures (HTTP
500/502/503/529, httpx timeouts and connection errors); other 4xx are never
retried and every other retry policy belongs to callers. The endpoint is
provider-swappable via Settings (``CVA_LLM_BASE_URL`` / ``CVA_LLM_MODEL``).
Content parsing extracts the first balanced JSON object, tolerating trailing
chat prose and code fences; ``CVA_LLM_DISABLE_THINKING`` sends
``"thinking": {"type": "disabled"}`` for GLM-style endpoints.

``ClaudeCodeLlmClient`` (claude-code-llm-provider) instead shells out to the
local `claude` CLI in headless mode (`claude -p --output-format json`),
billed against the owner's Claude subscription instead of API usage.
``CodexCliLlmClient`` runs the user's own `codex exec` CLI the same way
(read-only sandbox, output schema file, final message file), on their own
ChatGPT login. ``AnthropicLlmClient`` talks to the Anthropic Messages API over raw httpx
(structured output via ``output_config.format``; no SDK dependency).
``build_llm_client`` selects between them from ``Settings.llm_provider``.
"""

import asyncio
import functools
import json
import logging
import os
import random
import re
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
    if settings.llm_provider == "codex_cli":
        default = settings.codex_cli_model or "default"
        extraction = settings.extraction_model or default
        judge = settings.judge_model or default
        return f"LLM: proveedor=codex_cli · extracción={extraction} · juez={judge}"
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
# Credentials that would move the `codex` CLI from the user's ChatGPT login to
# API billing (`codex exec` reads CODEX_API_KEY; `codex login --with-api-key`
# / `--with-access-token` document OPENAI_API_KEY / CODEX_ACCESS_TOKEN).
_CODEX_CREDENTIAL_VARS = frozenset(
    {"OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"}
)


def _child_env(
    pass_api_key: bool = False,
    credential_vars: frozenset[str] = _ANTHROPIC_CREDENTIAL_VARS,
) -> dict[str, str]:
    """Environment for the `claude` subprocess: the parent's, minus secrets.

    Inherits every OS variable (the CLI may need ones nobody listed in
    advance: Windows USERNAME/HOMEDRIVE/ProgramFiles, proxies, CA bundles...)
    and drops every ``CVA_*`` variable (``CVA_LLM_API_KEY``,
    ``CVA_TELEGRAM_TOKEN``, ``CVA_BRIDGE_SECRET``, ...) — the child never
    needs this app's own secrets. It also drops ``ANTHROPIC_API_KEY`` /
    ``ANTHROPIC_AUTH_TOKEN`` unless ``pass_api_key`` (setting
    ``claude_code_pass_api_key``) opts in, so the CLI keeps using the user's
    own login instead of silently switching to API billing. The Codex
    provider passes ``_CODEX_CREDENTIAL_VARS`` instead. Name checks are
    case-insensitive because Windows environment names are.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("CVA_")
        and (pass_api_key or key.upper() not in credential_vars)
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


# Transient provider failures worth another attempt (529 is Anthropic's
# "overloaded"). Other 4xx are caller errors and are never retried.
_TRANSIENT_STATUSES = frozenset({500, 502, 503, 529})
_TRANSIENT_BACKOFF_FACTOR = 2.5
_PROVIDER_MESSAGE_LIMIT = 200


def _default_jitter() -> float:
    return random.uniform(0.0, 1.0)


def _provider_error_detail(response: httpx.Response) -> str:
    """The provider's own short error text (``error.message``), flattened and
    truncated; empty when the body is not the usual JSON error envelope.

    Only the response body is read, never the request, so no header or key
    can leak into the message.
    """
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if not isinstance(message, str):
        return ""
    flat = " ".join(message.split())
    if len(flat) > _PROVIDER_MESSAGE_LIMIT:
        flat = flat[:_PROVIDER_MESSAGE_LIMIT] + "…"
    return flat


def _http_error(prefix: str, response: httpx.Response) -> "LlmResponseError":
    """``<prefix> HTTP <code>[: <provider message>]``; the ``HTTP <code>``
    wording is parsed by ``notifier.failure_reason``, keep it."""
    detail = _provider_error_detail(response)
    message = f"{prefix} HTTP {response.status_code}"
    return LlmResponseError(
        f"{message}: {detail}" if detail else message, status=response.status_code
    )


async def _send_with_retries(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    retry_429_delay: Callable[[httpx.Response], float] | None,
    transient_retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
    jitter: Callable[[], float],
    label: str,
) -> httpx.Response:
    """Send with ONE retry on HTTP 429 (when ``retry_429_delay`` is given) and
    up to ``transient_retries`` extra attempts on transient failures, waiting
    ``backoff_seconds * 2.5**n + jitter()`` before each (≈2 s, ≈5 s).

    Returns the final response (any status) for the caller to map; raises
    ``LlmResponseError`` when the transport keeps failing. Cancellation is
    never caught: a cancelled caller aborts at once, even mid-backoff.
    """
    retried_429 = False
    transient_used = 0
    while True:
        try:
            response = await send()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if transient_used >= transient_retries:
                raise LlmResponseError(
                    f"{label} unreachable after {transient_used + 1} attempt(s): "
                    f"{type(exc).__name__}"
                ) from exc
        else:
            if response.status_code == 429 and retry_429_delay and not retried_429:
                retried_429 = True
                await sleep(retry_429_delay(response))
                continue
            if (
                response.status_code not in _TRANSIENT_STATUSES
                or transient_used >= transient_retries
            ):
                return response
        await sleep(
            backoff_seconds * _TRANSIENT_BACKOFF_FACTOR**transient_used + jitter()
        )
        transient_used += 1


def supports_web_search(client: object) -> bool:
    """True when the provider can run a completion with web search enabled.

    Only the CLI providers can: Claude Code (``WebSearch``/``WebFetch``
    tools) and Codex (``--search``). The HTTP providers have no such
    capability here, so callers must say "not available" instead of asking
    them to guess.
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
        transient_retries: int = 2,
        transient_backoff_seconds: float = 2.0,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._disable_thinking = disable_thinking
        self._retry_wait_seconds = retry_429_wait_seconds
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._transient_retries = transient_retries
        self._transient_backoff_seconds = transient_backoff_seconds
        self._jitter = jitter if jitter is not None else _default_jitter

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
            transient_retries=settings.llm_transient_retries,
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
        # ONE bounded retry for HTTP 429 plus a short backoff for transient
        # 5xx/timeouts; the D3 no-retry rule covers LinkedIn walls, not the
        # LLM endpoint.
        response = await _send_with_retries(
            lambda: self._send(payload),
            retry_429_delay=(
                (lambda _r: self._retry_wait_seconds)
                if self._retry_wait_seconds > 0
                else None
            ),
            transient_retries=self._transient_retries,
            backoff_seconds=self._transient_backoff_seconds,
            sleep=self._sleep,
            jitter=self._jitter,
            label="LLM endpoint",
        )
        if response.status_code != 200:
            raise _http_error("LLM endpoint returned", response)
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
    (``retry-after`` honoured, capped), the same transient backoff (5xx incl.
    529 overloaded, timeouts), a clear message on 401, and
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
        transient_retries: int = 2,
        transient_backoff_seconds: float = 2.0,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._retry_wait_seconds = retry_429_wait_seconds
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._transient_retries = transient_retries
        self._transient_backoff_seconds = transient_backoff_seconds
        self._jitter = jitter if jitter is not None else _default_jitter

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
            transient_retries=settings.llm_transient_retries,
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
        response = await _send_with_retries(
            lambda: self._send(payload),
            retry_429_delay=self._retry_delay if self._retry_wait_seconds > 0 else None,
            transient_retries=self._transient_retries,
            backoff_seconds=self._transient_backoff_seconds,
            sleep=self._sleep,
            jitter=self._jitter,
            label="Anthropic API",
        )
        if response.status_code == 401:
            raise LlmResponseError(
                "Anthropic API rejected the credentials (HTTP 401): "
                "check ANTHROPIC_API_KEY",
                status=401,
            )
        if response.status_code != 200:
            raise _http_error("Anthropic API returned", response)
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
        return schema.model_validate(_unwrap_double_encoded(data))
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
    oauth_token: str | None = None,
) -> tuple[int, bytes, bytes]:
    """Real `claude` CLI invocation: no shell, isolated cwd, bounded wait.

    The subprocess runs in a fresh temporary directory (never the repo
    working directory) so it never picks up this project's CLAUDE.md or
    project-scoped settings, on top of the CLI-side isolation flags the
    caller already passes (``--setting-sources ""``, ``--strict-mcp-config``,
    ``--tools ""``), and a filtered environment (see ``_child_env``) that
    never carries this app's own ``CVA_*`` secrets.
    """
    env = _child_env(pass_api_key)
    if oauth_token:
        # `claude setup-token` token from .env (unattended bots, llm-outage).
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    with tempfile.TemporaryDirectory(prefix="cva-claude-code-") as neutral_cwd:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=neutral_cwd,
            env=env,
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
        oauth_token: str | None = None,
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
            else functools.partial(
                _run_claude_subprocess,
                pass_api_key=pass_api_key,
                oauth_token=oauth_token,
            )
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeCodeLlmClient":
        """Wire the client from Settings (path/model/timeout)."""
        return cls(
            claude_code_path=settings.claude_code_path,
            model=settings.claude_code_model,
            timeout_seconds=float(settings.claude_code_timeout_seconds),
            pass_api_key=settings.claude_code_pass_api_key,
            oauth_token=(
                settings.claude_code_oauth_token.get_secret_value()
                if settings.claude_code_oauth_token is not None
                else None
            ),
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
        return schema.model_validate(_unwrap_double_encoded(data))
    except ValidationError as exc:
        raise LlmResponseError(
            f"claude CLI output does not satisfy {schema.__name__}"
        ) from exc


# --- Codex CLI provider ---------------------------------------------------

# Schema keywords dropped from the --output-schema file: Codex sends the
# schema as a strict structured-output format, which rejects defaults and
# requires every property to be listed in "required". Pydantic validation of
# the parsed reply still applies the real constraints and defaults.
_CODEX_DROPPED_SCHEMA_KEYWORDS = frozenset({"default"})

# Wording in a failed `codex exec` run's output that means "no usable login".
_CODEX_AUTH_PATTERN = re.compile(
    r"not logged in|codex login|unauthori[sz]ed|\b401\b|authentication"
)


def _codex_schema(node: object) -> object:
    """Strict copy of a pydantic JSON schema for ``codex exec --output-schema``:
    the Anthropic clean-up (no unsupported keywords, ``additionalProperties:
    false``) plus no ``default`` and every property required."""
    cleaned = _anthropic_schema(node)
    return _strict_required(cleaned)


def _strict_required(node: object) -> object:
    if isinstance(node, list):
        return [_strict_required(item) for item in node]
    if not isinstance(node, dict):
        return node
    strict = {
        key: _strict_required(value)
        for key, value in node.items()
        if key not in _CODEX_DROPPED_SCHEMA_KEYWORDS
    }
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["required"] = list(properties)
    return strict


CodexRunner = Callable[
    [list[str], bytes, float, str], Awaitable[tuple[int, bytes, bytes]]
]


async def _run_codex_subprocess(
    argv: list[str],
    stdin_data: bytes,
    timeout_seconds: float,
    cwd: str,
    *,
    pass_api_key: bool = False,
) -> tuple[int, bytes, bytes]:
    """Real `codex` CLI invocation: no shell, empty cwd, bounded wait, child
    killed on timeout or cancellation, filtered environment (no ``CVA_*``
    secrets, no OpenAI/Codex API keys unless ``pass_api_key``)."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_child_env(pass_api_key, _CODEX_CREDENTIAL_VARS),
    )
    stdout, stderr = await _communicate_with_timeout(
        process, stdin_data, timeout_seconds
    )
    return process.returncode or 0, stdout, stderr


class CodexCliLlmClient:
    """Non-interactive `codex exec` CLI provider, on the user's own login.

    Every call runs in a fresh, empty temporary working directory with a
    read-only sandbox, no approval prompts, no persisted session and no user
    ``config.toml`` (so no user MCP servers or hooks), outside any git repo.
    The prompt (system instructions + the ``user`` text) goes via stdin, never
    argv. The response shape is enforced with ``--output-schema`` and the
    final message is read from the ``--output-last-message`` file, then
    parsed with the same balanced-JSON scanner the other providers use.

    Web research (``web_json_completion``) passes the root ``--search`` flag
    (Codex's native live ``web_search`` tool); every other call sets
    ``-c web_search="disabled"`` so plain completions never browse.
    """

    model: str  # cache key, e.g. "codex-cli:gpt-5" / "codex-cli:default"
    supports_web_search = True  # opt-in per call via web_json_completion

    def __init__(
        self,
        *,
        codex_cli_path: str = "codex",
        model: str | None = None,
        timeout_seconds: float = 180.0,
        run: CodexRunner | None = None,
        pass_api_key: bool = False,
    ) -> None:
        self.codex_cli_path = codex_cli_path
        self.codex_cli_model = model
        self.timeout_seconds = timeout_seconds
        self.web_timeout_seconds = max(timeout_seconds, WEB_TIMEOUT_FLOOR_SECONDS)
        self.pass_api_key = pass_api_key
        self.model = f"codex-cli:{model or 'default'}"
        self._run: CodexRunner = (
            run
            if run is not None
            else functools.partial(_run_codex_subprocess, pass_api_key=pass_api_key)
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "CodexCliLlmClient":
        """Wire the client from Settings (path/model/timeout/api-key opt-in)."""
        return cls(
            codex_cli_path=settings.codex_cli_path,
            model=settings.codex_cli_model,
            timeout_seconds=float(settings.codex_cli_timeout_seconds),
            pass_api_key=settings.codex_cli_pass_api_key,
        )

    def _build_argv(
        self,
        *,
        workdir: str,
        schema_path: str,
        output_path: str,
        model: str | None,
        web: bool,
    ) -> list[str]:
        argv = [self.codex_cli_path]
        if web:
            argv.append("--search")  # root-level flag; `exec --search` is rejected
        argv += [
            "-a",
            "never",
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--color",
            "never",
            "-C",
            workdir,
            "--output-schema",
            schema_path,
            "-o",
            output_path,
        ]
        if not web:
            argv += ["-c", 'web_search="disabled"']
        if model:
            argv += ["-m", model]
        argv.append("-")  # read the prompt from stdin
        return argv

    async def web_json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        """Like :meth:`json_completion` with Codex live web search enabled
        (salary / company-facts research only)."""
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
        resolved_model = _resolve_model_override(model, self.codex_cli_model)
        timeout = self.web_timeout_seconds if web else self.timeout_seconds
        prompt = (
            f"{system}\n\nAnswer with only the JSON object the output schema "
            f"describes. Input:\n\n{user}"
        )
        with tempfile.TemporaryDirectory(prefix="cva-codex-cli-") as scratch:
            workdir = os.path.join(scratch, "work")
            os.mkdir(workdir)
            schema_path = os.path.join(scratch, "schema.json")
            output_path = os.path.join(scratch, "last-message.txt")
            _write_text(
                schema_path, json.dumps(_codex_schema(schema.model_json_schema()))
            )
            argv = self._build_argv(
                workdir=workdir,
                schema_path=schema_path,
                output_path=output_path,
                model=resolved_model,
                web=web,
            )
            try:
                exit_code, stdout, stderr = await self._run(
                    argv, prompt.encode("utf-8"), timeout, workdir
                )
            except FileNotFoundError as exc:
                raise LlmResponseError(
                    f"codex executable not found at {self.codex_cli_path!r} "
                    "(check codex_cli_path in config.toml / CVA_CODEX_CLI_PATH)"
                ) from exc
            except TimeoutError as exc:
                raise LlmResponseError(
                    f"codex CLI timed out after {timeout}s "
                    "(check codex_cli_timeout_seconds)"
                ) from exc
            if exit_code != 0:
                raise LlmResponseError(_codex_exit_message(exit_code, stdout, stderr))
            final_message = _read_text_or_empty(output_path)
        if not final_message.strip():
            raise LlmResponseError(
                f"codex CLI wrote no final message: {_trim(stderr) or 'no output'}"
            )
        data = _extract_json_object(final_message, finish_reason=None)
        try:
            return schema.model_validate(_unwrap_double_encoded(data))
        except ValidationError as exc:
            raise LlmResponseError(
                f"codex CLI output does not satisfy {schema.__name__}"
            ) from exc


def _write_text(path: str, text: str) -> None:
    """Small local temp-file write (sync helper: a few KB, no event-loop risk)."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _read_text_or_empty(path: str) -> str:
    """The CLI's final-message file, or "" when it is missing/unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _codex_exit_message(exit_code: int, stdout: bytes, stderr: bytes) -> str:
    """Failed run: a missing/expired login gets an actionable `codex login`
    hint (and no raw output that could read like an API-key problem);
    anything else names the exit status and the trimmed stderr."""
    combined = (stderr + b"\n" + stdout).decode("utf-8", errors="replace").lower()
    if _CODEX_AUTH_PATTERN.search(combined):
        return (
            f"codex CLI is not logged in (exit status {exit_code}): run "
            "`codex login` with your ChatGPT account, then try again"
        )
    return f"codex CLI exited with status {exit_code}: {_trim(stderr or stdout)}"


def build_llm_client(
    settings: Settings,
) -> (
    "OpenAiCompatibleLlmClient | ClaudeCodeLlmClient | AnthropicLlmClient"
    " | CodexCliLlmClient"
):
    """Single factory used at every construction site (service/bot/cli).

    Selects the provider from ``settings.llm_provider`` (default
    ``"claude_code"``).
    """
    if settings.llm_provider == "claude_code":
        return ClaudeCodeLlmClient.from_settings(settings)
    if settings.llm_provider == "codex_cli":
        return CodexCliLlmClient.from_settings(settings)
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
        return schema.model_validate(_unwrap_double_encoded(data))
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


def _unwrap_double_encoded(data: object) -> object:
    """Undo a model answer whose string field holds the whole JSON answer again.

    Observed live (2026-11-19, follow-up drafts): ``{"text": "{\\"text\\":
    \\"Hola,\\n\\n...\\"}"}`` reached Telegram as raw JSON with literal
    ``\\n``. A string field is replaced by the same key of the JSON object it
    encodes, only when the whole string parses as an object that has that key
    with a string value (repeated, for deeper nesting). Any other text, even
    one that starts with a brace, is kept as is.
    """
    if not isinstance(data, dict):
        return data
    fixed: dict[object, object] = {}
    for key, value in data.items():
        for _ in range(3):
            if not (isinstance(value, str) and value.strip().startswith("{")):
                break
            try:
                inner = json.loads(value)
            except json.JSONDecodeError:
                break
            if not (isinstance(inner, dict) and isinstance(inner.get(key), str)):
                break
            value = inner[key]
        fixed[key] = value
    return fixed


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
