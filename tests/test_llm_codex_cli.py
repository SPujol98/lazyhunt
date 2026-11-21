"""CodexCliLlmClient tests: the user's own `codex exec` CLI as the provider.

No real `codex` binary is ever run here (owner login and quota safety): each
test points ``codex_cli_path`` at a small fake executable written into
``tmp_path``. The fake records its argv, stdin, cwd and environment into a
JSON log and behaves according to ``FAKE_CODEX_MODE``, so the tests go
through the public provider interface and the real subprocess path
(temporary directories, ``--output-last-message`` file, timeout kill).
"""

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel

from cva.config import Settings
from cva.llm import (
    ClaudeCodeLlmClient,
    CodexCliLlmClient,
    LlmResponseError,
    build_llm_client,
    describe_llm_setup,
    supports_web_search,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake codex is a POSIX shebang script"
)

_FAKE_CODEX = """\
#!{python}
import json, os, sys, time

argv = sys.argv[1:]
mode = os.environ.get("FAKE_CODEX_MODE", "success")
log = {{
    "argv": argv,
    "stdin": sys.stdin.read(),
    "cwd": os.getcwd(),
    "cwd_entries": sorted(os.listdir(os.getcwd())),
    "env": dict(os.environ),
    "pid": os.getpid(),
}}
schema_path = argv[argv.index("--output-schema") + 1] if "--output-schema" in argv else None
log["schema"] = json.load(open(schema_path)) if schema_path else None
with open(os.environ["FAKE_CODEX_LOG"], "w") as handle:
    json.dump(log, handle)

out = argv[argv.index("-o") + 1] if "-o" in argv else None
answer = {{"title": "ML Engineer", "must_haves": ["python"]}}
if mode == "success":
    open(out, "w").write(json.dumps(answer))
elif mode == "prose":
    open(out, "w").write("Here you go:\\n" + json.dumps(answer) + "\\nAnything else?")
elif mode == "malformed":
    open(out, "w").write("I could not produce JSON, sorry.")
elif mode == "no_file":
    pass
elif mode == "not_logged_in":
    sys.stderr.write("Error: Not logged in. Run `codex login` to sign in.\\n")
    sys.exit(1)
elif mode == "other_error":
    sys.stderr.write("stream error: model overloaded\\n")
    sys.exit(2)
elif mode == "sleep":
    time.sleep(60)
"""


class Reply(BaseModel):
    title: str
    must_haves: list[str]


class OptionalReply(BaseModel):
    title: str
    note: str | None = None
    tags: list[str] = []


@pytest.fixture()
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "bin" / "codex"
    script.parent.mkdir()
    script.write_text(_FAKE_CODEX.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(tmp_path / "log.json"))
    return script


def _log(tmp_path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((tmp_path / "log.json").read_text())
    return data


def _argv(tmp_path: Path) -> list[str]:
    argv = _log(tmp_path)["argv"]
    assert isinstance(argv, list)
    return [str(item) for item in argv]


def _client(path: Path, **kwargs: object) -> CodexCliLlmClient:
    return CodexCliLlmClient(codex_cli_path=str(path), **kwargs)  # type: ignore[arg-type]


# --- success, invocation shape --------------------------------------------


def test_success_returns_parsed_model_from_last_message_file(
    fake_codex: Path, tmp_path: Path
) -> None:
    reply = asyncio.run(
        _client(fake_codex).json_completion("SYSTEM RULES", "OFFER TEXT", Reply)
    )

    assert reply == Reply(title="ML Engineer", must_haves=["python"])


def test_invocation_is_non_interactive_read_only_and_isolated(
    fake_codex: Path, tmp_path: Path
) -> None:
    asyncio.run(
        _client(fake_codex).json_completion("SYSTEM RULES", "OFFER TEXT", Reply)
    )

    argv = _argv(tmp_path)
    log = _log(tmp_path)
    assert argv[argv.index("exec") - 2 : argv.index("exec")] == ["-a", "never"]
    for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-user-config"):
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--color") + 1] == "never"
    assert argv[-1] == "-"  # prompt read from stdin
    # The prompt (system + offer) travels via stdin only, never argv.
    assert "OFFER TEXT" not in " ".join(argv)
    assert "SYSTEM RULES" not in " ".join(argv)
    stdin = str(log["stdin"])
    assert "SYSTEM RULES" in stdin and "OFFER TEXT" in stdin
    # Runs in an empty temporary directory, never the repository.
    assert log["cwd_entries"] == []
    assert Path(str(log["cwd"])).resolve() != Path.cwd().resolve()
    # macOS resolves /var to /private/var: compare the folders, not the text.
    assert Path(argv[argv.index("-C") + 1]).resolve() == Path(str(log["cwd"])).resolve()
    # Temporary files are cleaned up after the call.
    assert not Path(str(log["cwd"])).exists()


def test_output_schema_is_strict_for_every_object(
    fake_codex: Path, tmp_path: Path
) -> None:
    asyncio.run(_client(fake_codex).json_completion("s", "u", OptionalReply))

    schema = _log(tmp_path)["schema"]
    assert isinstance(schema, dict)
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["note", "tags", "title"]
    assert "default" not in json.dumps(schema)


def test_model_flag_only_when_configured_and_per_call_override_wins(
    fake_codex: Path, tmp_path: Path
) -> None:
    asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))
    assert "-m" not in _argv(tmp_path)

    asyncio.run(_client(fake_codex, model="gpt-5").json_completion("s", "u", Reply))
    argv = _argv(tmp_path)
    assert argv[argv.index("-m") + 1] == "gpt-5"

    asyncio.run(
        _client(fake_codex, model="gpt-5").json_completion(
            "s", "u", Reply, model="gpt-5-mini"
        )
    )
    argv = _argv(tmp_path)
    assert argv[argv.index("-m") + 1] == "gpt-5-mini"


def test_trailing_prose_around_json_is_tolerated(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "prose")

    reply = asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))

    assert reply.title == "ML Engineer"


# --- web research ---------------------------------------------------------


def test_research_calls_enable_live_web_search_and_plain_calls_disable_it(
    fake_codex: Path, tmp_path: Path
) -> None:
    client = _client(fake_codex)
    assert supports_web_search(client)

    asyncio.run(client.web_json_completion("s", "u", Reply))
    argv = _argv(tmp_path)
    assert "--search" in argv[: argv.index("exec")]

    asyncio.run(client.json_completion("s", "u", Reply))
    argv = _argv(tmp_path)
    assert "--search" not in argv
    assert argv[argv.index("-c") + 1] == 'web_search="disabled"'


# --- errors ---------------------------------------------------------------


def test_not_logged_in_tells_the_user_to_run_codex_login(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "not_logged_in")

    with pytest.raises(LlmResponseError, match="codex login") as excinfo:
        asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))
    assert "HTTP" not in str(excinfo.value)  # no misleading API-key hint


def test_other_non_zero_exit_surfaces_status_and_stderr(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "other_error")

    with pytest.raises(LlmResponseError, match="status 2: stream error"):
        asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))


def test_timeout_raises_and_kills_the_child(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "sleep")

    started = time.monotonic()
    with pytest.raises(LlmResponseError, match="timed out after 1.5s"):
        asyncio.run(
            _client(fake_codex, timeout_seconds=1.5).json_completion("s", "u", Reply)
        )
    assert time.monotonic() - started < 20
    pid = _log(tmp_path)["pid"]
    assert isinstance(pid, int)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_malformed_last_message_raises(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "malformed")

    with pytest.raises(LlmResponseError, match="no JSON object"):
        asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))


def test_missing_last_message_raises(
    fake_codex: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "no_file")

    with pytest.raises(LlmResponseError, match="no final message"):
        asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))


def test_missing_binary_names_the_setting(tmp_path: Path) -> None:
    with pytest.raises(LlmResponseError, match="codex_cli_path"):
        asyncio.run(
            _client(tmp_path / "nope" / "codex").json_completion("s", "u", Reply)
        )


# --- environment scrubbing ------------------------------------------------

_KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")


def test_child_never_gets_api_keys_or_cva_secrets_by_default(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in _KEYS:
        monkeypatch.setenv(key, "secret")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tg-secret")
    monkeypatch.setenv("KEEP_ME", "1")

    asyncio.run(_client(fake_codex).json_completion("s", "u", Reply))

    env = _log(tmp_path)["env"]
    assert isinstance(env, dict)
    assert env.get("KEEP_ME") == "1"
    for key in (*_KEYS, "CVA_TELEGRAM_TOKEN"):
        assert key not in env


def test_pass_api_key_opts_in_to_api_keys_but_never_cva_secrets(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in _KEYS:
        monkeypatch.setenv(key, "secret")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tg-secret")

    asyncio.run(_client(fake_codex, pass_api_key=True).json_completion("s", "u", Reply))

    env = _log(tmp_path)["env"]
    assert isinstance(env, dict)
    for key in _KEYS:
        assert env.get(key) == "secret"
    assert "CVA_TELEGRAM_TOKEN" not in env


# --- settings, factory, startup line ---------------------------------------


def test_settings_defaults_and_from_settings_wiring(
    monkeypatch: pytest.MonkeyPatch, clean_settings: Callable[..., Settings]
) -> None:
    defaults = clean_settings(CVA_LLM_API_KEY=None)
    assert defaults.codex_cli_path == "codex"
    assert defaults.codex_cli_model is None
    assert defaults.codex_cli_timeout_seconds == 180
    assert defaults.codex_cli_pass_api_key is False

    monkeypatch.setenv("CVA_LLM_PROVIDER", "codex_cli")
    monkeypatch.setenv("CVA_CODEX_CLI_PATH", "/opt/codex")
    monkeypatch.setenv("CVA_CODEX_CLI_MODEL", "gpt-5")
    monkeypatch.setenv("CVA_CODEX_CLI_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("CVA_CODEX_CLI_PASS_API_KEY", "true")
    settings = clean_settings(CVA_LLM_API_KEY=None)

    client = build_llm_client(settings)

    assert isinstance(client, CodexCliLlmClient)
    assert client.codex_cli_path == "/opt/codex"
    assert client.codex_cli_model == "gpt-5"
    assert client.timeout_seconds == 60.0
    assert client.pass_api_key is True
    assert client.model == "codex-cli:gpt-5"
    assert describe_llm_setup(settings) == (
        "LLM: proveedor=codex_cli · extracción=gpt-5 · juez=gpt-5"
    )


def test_default_provider_is_still_claude_code(
    clean_settings: Callable[..., Settings],
) -> None:
    settings = clean_settings(CVA_LLM_API_KEY=None)

    assert isinstance(build_llm_client(settings), ClaudeCodeLlmClient)
    assert describe_llm_setup(settings).startswith("LLM: proveedor=claude_code")
