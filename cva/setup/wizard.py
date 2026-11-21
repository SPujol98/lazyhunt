"""Interactive, re-runnable first-run setup behind ``cli.py init`` (public-release S3).

The wizard asks every question first, shows a summary of the planned changes
and writes only after the user confirms, so declining (or Ctrl-C at any
prompt) leaves the installation untouched. It writes:

* secrets (``CVA_TELEGRAM_TOKEN``, ``CVA_BRIDGE_SECRET``) to ``.env`` only,
  updating keys in place, mode 600 on POSIX, and never prints their values
  (the bridge secret is shown once, and only on request, at the very end);
* the AI provider's API key, when the chosen provider needs one
  (``ANTHROPIC_API_KEY`` or ``CVA_LLM_API_KEY``), asked with hidden input and
  handled like the other secrets;
* non-secret settings (``ui_language``, ``llm_provider``, ``llm_base_url``,
  ``llm_model``, ``profile_dir``) to ``config.local.toml``. The Telegram
  owner id is NOT asked (beta3-ux T1): the bot pairs with its owner through a
  one-time code (``/start <code>``, :mod:`cva.setup.pairing`); an id passed
  with ``--owner-id`` is planned like any other key, and one already in the
  config is kept;
* a new profile directory: empty (recommended; the bot starts in setup mode
  and the guided ``/setup`` in Telegram fills it in), a skeleton with clearly
  marked placeholders, or a verbatim copy of the fictional demo profile. An
  existing profile directory is never modified. ``profile_dir`` is always
  written explicitly, so the bot never falls back to the legacy layout.

The language is asked first (in English and Spanish) and every later line
speaks it (catalog :mod:`cva.i18n.wizard`; ``auto`` follows the system
language). Answers stay the same in both languages (numbers, names, ``y``;
Spanish also accepts ``s``).

A key that already has a different value is replaced only after an explicit
"yes". Re-running with the same answers changes nothing.

Claude Code checks run the user's own ``claude`` binary: ``claude --version``
and ``claude auth status --json``, of which only the boolean ``loggedIn`` is
read. Claude credential files are never opened, and no network call is made
by the wizard itself. The Codex CLI, when installed, answers only
``--version`` and ``login status``, of which only the exit code is read
(see :mod:`cva.setup.prereq`); its credentials are never opened.

Every side channel is injectable (``WizardIO``, ``runner``, ``which``,
``demo_profile``) so tests drive the public entry ``run_init`` in a
temporary directory.
"""

import getpass as _getpass
import json
import re
import secrets
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from cva.config import system_ui_language
from cva.fsutil import atomic_write
from cva.i18n import Lang, t
from cva.setup.prereq import check_codex

Runner = Callable[[Sequence[str]], tuple[int, str]]
Which = Callable[[str], str | None]

ENV_FILE: Final[str] = ".env"
LOCAL_TOML: Final[str] = "config.local.toml"
TOKEN_KEY: Final[str] = "CVA_TELEGRAM_TOKEN"
SECRET_KEY: Final[str] = "CVA_BRIDGE_SECRET"
DEFAULT_PROFILE: Final[str] = "profiles/me"
DEFAULT_BRIDGE_PORT: Final[int] = 8765
UI_LANGUAGES: Final[tuple[str, ...]] = ("auto", "en", "es")
PROVIDERS: Final[tuple[str, ...]] = (
    "claude_code",
    "codex_cli",
    "anthropic",
    "openai_compatible",
)
_PROVIDER_KEY: Final[dict[str, str]] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible": "CVA_LLM_API_KEY",
}


@dataclass(frozen=True)
class _Preset:
    """A ready-made OpenAI-compatible endpoint offered by the wizard."""

    label: str
    base_url: str  # "" = typed by the user ("Other")
    model: str  # "" = typed by the user
    local: bool = False


_PRESETS: Final[dict[str, _Preset]] = {
    "1": _Preset("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "2": _Preset(
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-3.5-flash-lite",
    ),
    "3": _Preset("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "4": _Preset("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "5": _Preset("Mistral", "https://api.mistral.ai/v1", "mistral-small-latest"),
    "6": _Preset("Ollama (local)", "http://localhost:11434/v1", "", local=True),
    "7": _Preset("LM Studio (local)", "http://localhost:1234/v1", "", local=True),
    "8": _Preset("Other", "", ""),  # label: wizard.preset_other
}
_OTHER_PRESET: Final[str] = "8"
# Local servers ignore the key, but the client always sends one.
LOCAL_KEY_PLACEHOLDER: Final[str] = "local"
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEMO_PROFILE: Final[Path] = _REPO_ROOT / "profiles" / "demo"
_MIN_PYTHON: Final[tuple[int, int]] = (3, 12)
_TOKEN_SHAPE: Final[re.Pattern[str]] = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_UNSET: Final[tuple[object, ...]] = (None, "", 0)
# Answer of the profile-kind question -> what ``_create_profile`` makes.
_PROFILE_KINDS: Final[dict[str, str]] = {"1": "skeleton", "2": "demo", "3": "empty"}
_TOTAL_STEPS: Final[int] = 7


@dataclass(frozen=True)
class WizardIO:
    """The wizard's whole conversation surface (injected by tests)."""

    input: Callable[[str], str]
    print: Callable[[str], None]
    getpass: Callable[[str], str]


def default_io() -> WizardIO:
    """Terminal I/O: ``input``, ``print`` and a hidden ``getpass`` prompt.

    Printing flushes so explanations always appear before the ``getpass``
    prompt, which is written to the terminal (or stderr) directly.
    """

    def say(text: str) -> None:
        print(text, flush=True)

    return WizardIO(input=input, print=say, getpass=_getpass.getpass)


def default_runner(argv: Sequence[str]) -> tuple[int, str]:
    """Run a local command; return ``(exit code, stdout)``, ``(-1, "")`` on failure."""
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return proc.returncode, proc.stdout


@dataclass
class _Plan:
    """Everything the wizard intends to write, collected before confirmation."""

    env: dict[str, str] = field(default_factory=dict)
    env_replaced: set[str] = field(default_factory=set)
    toml: dict[str, str | int] = field(default_factory=dict)
    profile_dest: Path | None = None
    profile_label: str = ""
    profile_kind: str = ""  # "empty", "skeleton" or "demo"

    def is_empty(self) -> bool:
        return not self.env and not self.toml and self.profile_dest is None


class _Cancelled(Exception):
    """The user interrupted the wizard before anything was written."""


# --- small prompt helpers -------------------------------------------------

# Accepted as "yes" in both languages (the machine tokens work everywhere).
_YES_WORDS: Final[frozenset[str]] = frozenset({"y", "yes", "s", "si", "sí"})


def _ask(io: WizardIO, prompt: str, default: str = "") -> str:
    try:
        answer = io.input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _Cancelled from exc
    return answer or default


def _yes(io: WizardIO, prompt: str, lang: Lang = "en") -> bool:
    return _ask(io, f"{prompt} {t('wizard.yes_no', lang)}: ").lower() in _YES_WORDS


def _ask_secret(io: WizardIO, prompt: str) -> str:
    try:
        return io.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _Cancelled from exc


def _step(io: WizardIO, lang: Lang, n: int, title: str) -> None:
    io.print("")
    io.print(
        t(
            "wizard.step",
            lang,
            n=n,
            total=_TOTAL_STEPS,
            title=t(f"wizard.title.{title}", lang),
        )
    )


# --- reading the current configuration ------------------------------------


def _read_env(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs of a dotenv file (comments and ``export`` tolerated)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = _env_line(line)
        if key:
            values[key] = value
    return values


def _env_line(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return "", ""
    key, _, value = stripped.partition("=")
    key = key.removeprefix("export ").strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return key, value


def _read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


# --- steps ----------------------------------------------------------------


def _check_prerequisites(
    io: WizardIO, runner: Runner, which: Which, lang: Lang
) -> None:
    _step(io, lang, 2, "prereq")
    found = sys.version_info[:2]
    version = ".".join(str(part) for part in sys.version_info[:3])
    if found >= _MIN_PYTHON:
        io.print(t("wizard.python_ok", lang, version=version))
    else:
        io.print(t("wizard.python_old", lang, version=version))
    if which("uv") is None:
        io.print(t("wizard.uv_missing", lang))
    # Codex is optional: when installed, only its version and a yes/no
    # "logged in" are read (``check_codex`` discards the status output).
    if which("codex") is None:
        io.print(t("wizard.codex_not_installed", lang))
    else:
        for line in check_codex(runner, which, lang=lang):
            io.print(line)
    claude = which("claude")
    if claude is None:
        io.print(t("wizard.claude_missing", lang))
        return
    code, out = runner([claude, "--version"])
    first_line = out.strip().splitlines()[0][:80] if code == 0 and out.strip() else ""
    if first_line:
        io.print(t("wizard.claude_version", lang, line=first_line))
    else:
        io.print(t("wizard.claude_no_version", lang))
    logged_in = _parse_logged_in(*runner([claude, "auth", "status", "--json"]))
    if logged_in is True:
        io.print(t("wizard.claude_logged_in", lang))
    elif logged_in is False:
        io.print(t("wizard.claude_logged_out", lang))
    else:
        io.print(t("wizard.claude_unknown", lang))


def _parse_logged_in(code: int, out: str) -> bool | None:
    """Only the boolean ``loggedIn`` is read; account details are discarded."""
    del code  # a logged-out CLI may exit non-zero and still print valid JSON
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
        return bool(data["loggedIn"])
    return None


def _propose(
    io: WizardIO,
    plan_values: dict[str, str] | dict[str, str | int],
    existing: dict[str, str] | dict[str, object],
    key: str,
    value: str | int,
    *,
    where: str,
    secret: bool,
    lang: Lang = "en",
) -> bool:
    """Plan ``key = value``; an existing different value needs a "yes".

    Returns True when the key replaces an existing value.
    """
    current = existing.get(key)
    if current == value:
        return False
    if current in _UNSET:
        plan_values[key] = value  # type: ignore[assignment]
        return False
    shown = "" if secret else t("wizard.current_value", lang, value=current)
    if _yes(io, t("wizard.replace", lang, key=key, where=where, shown=shown), lang):
        plan_values[key] = value  # type: ignore[assignment]
        return True
    io.print(t("wizard.keeping", lang, key=key))
    return False


def _resolve_lang(language: str) -> Lang:
    """The language the wizard speaks for a ``ui_language`` answer."""
    if language == "es":
        return "es"
    if language == "en":
        return "en"
    return system_ui_language()


def _step_language(io: WizardIO, plan: _Plan, existing_toml: dict[str, object]) -> Lang:
    """Step 1, asked in both languages; the wizard speaks the answer after it."""
    current = existing_toml.get("ui_language")
    default = (
        current if isinstance(current, str) and current in UI_LANGUAGES else "auto"
    )
    choices = ", ".join(UI_LANGUAGES)
    io.print(t("wizard.lang_intro", "en"))
    io.print("")
    while True:
        language = _ask(
            io, t("wizard.lang_prompt", "en", choices=choices, default=default), default
        ).lower()
        if language in UI_LANGUAGES:
            break
        io.print(t("wizard.lang_retry", "en", choices=choices))
    lang = _resolve_lang(language)
    io.print("")
    io.print(t("wizard.intro1", lang))
    io.print(t("wizard.intro2", lang))
    _step(io, lang, 1, "language")
    io.print(t("wizard.lang_explain", lang))
    _propose(
        io,
        plan.toml,
        existing_toml,
        "ui_language",
        language,
        where=LOCAL_TOML,
        secret=False,
        lang=lang,
    )
    return lang


def _step_provider(
    io: WizardIO,
    plan: _Plan,
    existing_env: dict[str, str],
    existing_toml: dict[str, object],
    lang: Lang,
) -> str:
    _step(io, lang, 3, "provider")
    current = existing_toml.get("llm_provider")
    default = (
        current if isinstance(current, str) and current in PROVIDERS else PROVIDERS[0]
    )
    io.print(t("wizard.provider_intro", lang))
    for number, name in enumerate(PROVIDERS, start=1):
        io.print(f"  [{number}] {name}: {t(f'wizard.provider.{name}', lang)}")
    io.print(t("wizard.provider_web", lang))
    by_number = {str(n): name for n, name in enumerate(PROVIDERS, start=1)}
    while True:
        answer = _ask(io, t("wizard.provider_prompt", lang, default=default), default)
        provider = by_number.get(answer, answer)
        if provider in PROVIDERS:
            break
        io.print(t("wizard.provider_retry", lang, choices=", ".join(PROVIDERS)))
    _propose(
        io,
        plan.toml,
        existing_toml,
        "llm_provider",
        provider,
        where=LOCAL_TOML,
        secret=False,
        lang=lang,
    )
    if provider == "claude_code":
        io.print(t("wizard.claude_code_note", lang))
    elif provider == "codex_cli":
        io.print(t("wizard.codex_note", lang))
    elif provider == "anthropic":
        _ask_api_key(
            io, plan, existing_env, "ANTHROPIC_API_KEY", local=False, lang=lang
        )
    else:
        local = _ask_endpoint(io, plan, existing_toml, lang)
        _ask_api_key(io, plan, existing_env, "CVA_LLM_API_KEY", local=local, lang=lang)
    return provider


def _ask_endpoint(
    io: WizardIO, plan: _Plan, existing_toml: dict[str, object], lang: Lang
) -> bool:
    """Plan ``llm_base_url`` and ``llm_model``; True for a local server."""
    current_url = existing_toml.get("llm_base_url")
    current_url = current_url if isinstance(current_url, str) else ""
    current_model = existing_toml.get("llm_model")
    current_model = current_model if isinstance(current_model, str) else ""
    default = next(
        (n for n, p in _PRESETS.items() if current_url and p.base_url == current_url),
        _OTHER_PRESET if current_url else "1",
    )
    io.print(t("wizard.endpoint_intro", lang))
    for number, preset in _PRESETS.items():
        shown = f" - {preset.base_url}" if preset.base_url else ""
        label = (
            t("wizard.preset_other", lang) if number == _OTHER_PRESET else preset.label
        )
        io.print(f"  [{number}] {label}{shown}")
    while True:
        choice = _ask(io, t("wizard.endpoint_prompt", lang, default=default), default)
        if choice in _PRESETS:
            break
        io.print(t("wizard.endpoint_retry", lang, count=len(_PRESETS)))
    preset = _PRESETS[choice]
    url = preset.base_url
    while not url:
        shown = f" [{current_url}]" if current_url else ""
        answer = _ask(io, t("wizard.base_url_prompt", lang, shown=shown), current_url)
        if answer.startswith(("http://", "https://")):
            url = answer.rstrip("/")
        else:
            io.print(t("wizard.base_url_retry", lang))
    model_default = current_model if current_url == url and current_model else ""
    model_default = model_default or preset.model
    if preset.local and not model_default:
        io.print(t("wizard.local_model_hint", lang))
    while True:
        shown = f" [{model_default}]" if model_default else ""
        model = _ask(io, t("wizard.model_prompt", lang, shown=shown), model_default)
        if model:
            break
        io.print(t("wizard.model_retry", lang))
    for key, value in (("llm_base_url", url), ("llm_model", model)):
        _propose(
            io,
            plan.toml,
            existing_toml,
            key,
            value,
            where=LOCAL_TOML,
            secret=False,
            lang=lang,
        )
    return preset.local


def _ask_api_key(
    io: WizardIO,
    plan: _Plan,
    existing_env: dict[str, str],
    key: str,
    *,
    local: bool,
    lang: Lang,
) -> None:
    """Plan the provider's API key in .env (hidden input, never printed)."""
    has_key = bool(existing_env.get(key))
    if local:
        io.print(
            t("wizard.local_key_note", lang, placeholder=repr(LOCAL_KEY_PLACEHOLDER))
        )
    if has_key:
        hint = t("wizard.hint_keep_key", lang)
    elif local:
        hint = t("wizard.hint_placeholder", lang)
    else:
        hint = t("wizard.hint_skip", lang)
    value = _ask_secret(io, t("wizard.api_key_prompt", lang, key=key, hint=hint))
    if not value and local and not has_key:
        value = LOCAL_KEY_PLACEHOLDER
    if value:
        if _propose(
            io,
            plan.env,
            existing_env,
            key,
            value,
            where=ENV_FILE,
            secret=True,
            lang=lang,
        ):
            plan.env_replaced.add(key)
    elif has_key:
        io.print(t("wizard.key_kept", lang, key=key))
    else:
        io.print(t("wizard.key_skipped", lang, key=key))


def _step_telegram(
    io: WizardIO,
    plan: _Plan,
    existing_env: dict[str, str],
    existing_toml: dict[str, object],
    lang: Lang,
    owner_id: int | None = None,
) -> None:
    """The bot token (with BotFather steps). The owner id is no longer asked:
    the bot pairs with its owner through a one-time code (``/start <code>``);
    an id passed with ``--owner-id`` or already in the config is kept."""
    _step(io, lang, 4, "telegram")
    io.print(t("wizard.botfather", lang))
    token = _ask_secret(io, t("wizard.token_prompt", lang))
    if token:
        if not _TOKEN_SHAPE.match(token):
            io.print(t("wizard.token_shape", lang))
        if _propose(
            io,
            plan.env,
            existing_env,
            TOKEN_KEY,
            token,
            where=ENV_FILE,
            secret=True,
            lang=lang,
        ):
            plan.env_replaced.add(TOKEN_KEY)
    elif existing_env.get(TOKEN_KEY):
        io.print(t("wizard.token_kept", lang))
    else:
        io.print(t("wizard.token_skipped", lang, key=TOKEN_KEY))
    if owner_id is not None:
        _propose(
            io,
            plan.toml,
            existing_toml,
            "owner_telegram_id",
            owner_id,
            where=LOCAL_TOML,
            secret=False,
            lang=lang,
        )
        return
    current = existing_toml.get("owner_telegram_id")
    if current not in _UNSET:
        io.print(t("wizard.owner_kept", lang, owner=current))
    else:
        io.print(t("wizard.owner_claim", lang))


def _step_bridge_secret(
    io: WizardIO, plan: _Plan, existing_env: dict[str, str], lang: Lang
) -> None:
    _step(io, lang, 5, "bridge")
    io.print(t("wizard.bridge_intro", lang))
    if existing_env.get(SECRET_KEY):
        if _yes(io, t("wizard.bridge_replace", lang), lang):
            plan.env[SECRET_KEY] = secrets.token_urlsafe(32)
            plan.env_replaced.add(SECRET_KEY)
        else:
            io.print(t("wizard.bridge_kept", lang))
        return
    plan.env[SECRET_KEY] = secrets.token_urlsafe(32)
    io.print(t("wizard.bridge_generated", lang, key=SECRET_KEY))


def _step_profile(
    io: WizardIO,
    plan: _Plan,
    base_dir: Path,
    existing_toml: dict[str, object],
    demo_profile: Path,
    lang: Lang,
) -> None:
    _step(io, lang, 6, "profile")
    io.print(t("wizard.profile_intro", lang))
    current = existing_toml.get("profile_dir")
    default = (
        current if isinstance(current, str) and current.strip() else DEFAULT_PROFILE
    )
    while True:
        answer = _ask(io, t("wizard.profile_prompt", lang, default=default), default)
        dest = _profile_destination(answer, base_dir, demo_profile)
        if dest is not None:
            break
        io.print(t("wizard.profile_retry", lang))
    if dest.exists():
        if not dest.is_dir():
            io.print(t("wizard.profile_not_dir", lang, path=answer))
            return
        io.print(t("wizard.profile_exists", lang, path=answer))
    elif not demo_profile.is_dir():
        io.print(t("wizard.profile_no_demo", lang, demo=demo_profile, path=answer))
    else:
        io.print(t("wizard.kind_skeleton", lang))
        io.print(t("wizard.kind_demo", lang))
        io.print(t("wizard.kind_empty", lang))
        while True:
            kind = _ask(io, t("wizard.kind_prompt", lang), "3")
            if kind in _PROFILE_KINDS:
                break
            io.print(t("wizard.kind_retry", lang))
        plan.profile_dest = dest
        plan.profile_label = answer
        plan.profile_kind = _PROFILE_KINDS[kind]
    _propose(
        io,
        plan.toml,
        existing_toml,
        "profile_dir",
        answer,
        where=LOCAL_TOML,
        secret=False,
        lang=lang,
    )


def _step_gmail(io: WizardIO, lang: Lang) -> None:
    _step(io, lang, 7, "gmail")
    io.print(t("wizard.gmail", lang))


# --- summary and writing --------------------------------------------------


def _print_summary(
    io: WizardIO,
    plan: _Plan,
    existing_toml: dict[str, object],
    missing_key: str = "",
    lang: Lang = "en",
) -> None:
    io.print("")
    io.print(t("wizard.planned", lang))
    for key in plan.env:
        verb = "wizard.env_replace" if key in plan.env_replaced else "wizard.env_set"
        io.print(t(verb, lang, file=ENV_FILE, key=key))
    if missing_key:
        io.print(t("wizard.env_missing", lang, file=ENV_FILE, key=missing_key))
    for key, value in plan.toml.items():
        before = existing_toml.get(key)
        if before in _UNSET:
            io.print(f"  {LOCAL_TOML}: + {key} = {_toml_value(value)}")
        else:
            io.print(f"  {LOCAL_TOML}: ~ {key}: {before!r} -> {_toml_value(value)}")
    if plan.profile_dest is not None:
        kind = t(f"wizard.kind.{plan.profile_kind}", lang)
        io.print(t("wizard.create_profile", lang, path=plan.profile_label, kind=kind))


def _toml_value(value: str | int | list[str]) -> str:
    if isinstance(value, list):  # an array of strings, on one line
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, int):
        return str(value)
    # A JSON string without ASCII escaping is a valid TOML basic string.
    return json.dumps(value, ensure_ascii=False)


def _open_brackets(line: str) -> int:
    """``[`` minus ``]`` in ``line``, outside strings and before a comment."""
    depth, quote = 0, ""
    for char in line:
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#":
            break
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
    return depth


def _trailing_comment(line: str) -> str:
    """The comment ending ``line`` (with the blanks before it, without the
    line ending), outside strings; ``""`` when there is none."""
    quote = ""
    body = line.rstrip("\r\n")
    for i, char in enumerate(body):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#":
            start = len(body[:i].rstrip(" \t"))
            return body[start:]
    return ""


def toml_set_key(base_text: str, key: str, value: str | int | list[str]) -> str:
    """``base_text`` with the top-level ``key`` set to ``value``.

    The line that assigns ``key`` before the first table (with the following
    lines of a multi-line array) becomes ``key = <value>``; without one, that
    line is added right before the first table (at the end without a
    table); a comment that ended the replaced value is kept after the new
    one. Every other line keeps its exact bytes, line endings included.
    The result is parsed back (``ValueError`` when it is not TOML or does not
    hold ``value``), so a file the bot could not read is never returned.
    """
    lines = base_text.splitlines(keepends=True)
    first_table = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")),
        len(lines),
    )
    eol = "\r\n" if "\r\n" in base_text else "\n"
    assign = re.compile(rf"^\s*{re.escape(key)}\s*=")
    start = next((i for i in range(first_table) if assign.match(lines[i])), None)
    if start is None:
        if first_table == len(lines) and lines and not lines[-1].endswith("\n"):
            lines[-1] += eol
        lines.insert(first_table, f"{key} = {_toml_value(value)}{eol}")
    else:
        end, depth = start, _open_brackets(lines[start].split("=", 1)[1])
        while depth > 0 and end + 1 < len(lines):
            end += 1
            depth += _open_brackets(lines[end])
        last = lines[end]
        ending = last[len(last.rstrip("\r\n")) :]
        tail = last.split("=", 1)[1] if end == start else last
        comment = _trailing_comment(tail)
        lines[start : end + 1] = [f"{key} = {_toml_value(value)}{comment}{ending}"]
    text = "".join(lines)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"the result is not valid TOML: {exc}") from exc
    if parsed.get(key) != value:
        raise ValueError(f"{key} could not be set")
    return text


def _profile_destination(
    answer: str, base_dir: Path, demo_profile: Path
) -> Path | None:
    """The profile directory for ``answer``, or ``None`` when it would land
    outside ``base_dir`` or inside the tracked demo profile."""
    dest = Path(answer).expanduser()
    if not dest.is_absolute():
        dest = base_dir / dest
    resolved = dest.resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        return None
    for demo in (base_dir / "profiles" / "demo", demo_profile):
        if resolved.is_relative_to(demo.resolve()):
            return None
    return dest


def _env_text(changes: dict[str, str], base_text: str) -> str:
    lines = base_text.splitlines()
    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        key, _ = _env_line(line)
        if key in changes:
            # dotenv keeps the last occurrence: write the new value once and
            # drop any later duplicate of the same key.
            if key not in written:
                out.append(f"{key}={changes[key]}")
                written.add(key)
            continue
        out.append(line)
    out.extend(f"{key}={value}" for key, value in changes.items() if key not in written)
    return "\n".join(out) + "\n"


def _toml_text(changes: dict[str, str | int], base_text: str) -> str:
    lines = base_text.splitlines()
    first_table = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines)
    )
    pending = dict(changes)
    for index in range(first_table):
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", lines[index])
        if match and match.group(1) in pending:
            key = match.group(1)
            lines[index] = f"{key} = {_toml_value(pending.pop(key))}"
    new_lines = [f"{key} = {_toml_value(value)}" for key, value in pending.items()]
    lines[first_table:first_table] = new_lines
    text = "\n".join(lines) + "\n"
    tomllib.loads(text)  # never write a file the bot could not read
    return text


def _base_text(path: Path, example: Path, fallback: str) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if example.is_file():
        return example.read_text(encoding="utf-8")
    return fallback


# --- profile skeleton -----------------------------------------------------

_MD_PURPOSE: Final[dict[str, str]] = {
    "voice.md": "a sample cover letter in your own words; the bot imitates its "
    "rhythm and tone, never its sentences",
    "honest_gaps.md": "requirements you cannot honestly claim and how to answer "
    "them; the bot never invents experience to cover them",
    "judge/facts.md": "short facts about you the AI judge must know (location, "
    "languages, work permit, years of experience)",
    "judge/anchors.md": "a few example offers with the score you would give them, "
    "to anchor the judge's scale",
    "judge/rubric_persona.md": "how the judge should weigh requirements for you "
    "(what is a minor penalty, what is a wall)",
}

_PROFILE_README: Final[str] = """\
# Your lazyhunt profile

Created by `uv run cli.py init`. Everything here describes you, so it is
personal data: keep this directory out of version control.

The layout matches `profiles/demo` (the fictional persona Alex Rivera), which
stays in the repository as a complete working example. Before the bot can
judge offers for you, replace every placeholder:

- [ ] `profile.toml`, `preferences.toml`, `salary_reference.toml`,
      `cover_letter_base.toml`: they still hold the demo values as a format
      example (see the TODO comment at the top of each file).
- [ ] `claims.json`: your verified facts (identity, skills, projects,
      education). Only claims listed here can ever appear in a CV or letter.
- [ ] `synonyms.json`: how offer keywords map to your claims.
- [ ] `masters/*.json`: the master CV content per role and language.
- [ ] `cv_base_pdfs/`: your own base CV PDFs plus `manifest.json` (see the
      demo's manifest for the format).
- [ ] `voice.md`, `honest_gaps.md`, `judge/*.md`: replace the TODO text.
- [ ] `eval/labels.json` and `eval/offers/`: optional labelled offers for
      `uv run cli.py judge-eval` (see `eval/README.md`).

Point `profile_dir` in `config.local.toml` at this directory (the wizard
already did if you created it there).
"""


def _md_placeholder(rel: str) -> str:
    purpose = _MD_PURPOSE.get(rel, "your own content for this file")
    return (
        f"<!-- TODO (lazyhunt init): replace this file with {purpose}. -->\n"
        f"# TODO: {rel}\n\n"
        f"Write {purpose}.\n\n"
        f"The fictional example is `profiles/demo/{rel}` in the repository.\n"
    )


def _toml_header(rel: str) -> str:
    return (
        "# TODO (lazyhunt init): this file still holds the fictional demo values\n"
        "# (Alex Rivera) as a format example. Replace every value with your own;\n"
        f"# the original is profiles/demo/{rel} in the repository.\n\n"
    )


def _skeleton_ignore(directory: str, names: list[str]) -> set[str]:
    if Path(directory).name == "offers":
        return set(names)
    return {n for n in names if n.endswith(".pdf") or n == "manifest.json"}


def _create_profile(dest: Path, kind: str, demo_profile: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "empty":
        # Nothing to copy: the bot starts in setup mode (cva/setup_mode.py).
        dest.mkdir()
        return
    if kind == "demo":
        shutil.copytree(demo_profile, dest)
        return
    shutil.copytree(demo_profile, dest, ignore=_skeleton_ignore)
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(dest).as_posix()
        if path.suffix == ".md" and rel != "eval/README.md":
            path.write_text(_md_placeholder(rel), encoding="utf-8", newline="")
        elif path.suffix == ".toml":
            original = path.read_text(encoding="utf-8")
            path.write_text(_toml_header(rel) + original, encoding="utf-8", newline="")
    labels = dest / "eval" / "labels.json"
    if labels.parent.is_dir():
        demo_labels = json.loads(
            (demo_profile / "eval" / "labels.json").read_text(encoding="utf-8")
        )
        labels.write_text(
            json.dumps(
                {
                    "_about": "TODO: your own labelled offers (see eval/README.md).",
                    "tolerance": demo_labels.get("tolerance", 10),
                    "offers": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )
    (dest / "README.md").write_text(_PROFILE_README, encoding="utf-8", newline="")


def _apply(plan: _Plan, base_dir: Path, demo_profile: Path) -> None:
    # Prepare and validate both files first: a config that cannot be rewritten
    # must not leave .env half-updated.
    env_path = base_dir / ENV_FILE
    toml_path = base_dir / LOCAL_TOML
    env_text = (
        _env_text(plan.env, _base_text(env_path, base_dir / ".env.example", ""))
        if plan.env
        else None
    )
    toml_text = (
        _toml_text(
            plan.toml,
            _base_text(
                toml_path,
                base_dir / "config.local.toml.example",
                "# Personal settings (gitignored). Written by `cli.py init`.\n",
            ),
        )
        if plan.toml
        else None
    )
    if env_text is not None:
        atomic_write(env_path, env_text, mode=0o600)
    if toml_text is not None:
        atomic_write(toml_path, toml_text)
    if plan.profile_dest is not None:
        _create_profile(plan.profile_dest, plan.profile_kind, demo_profile)


def _print_next_steps(
    io: WizardIO,
    existing_toml: dict[str, object],
    profile_dir: object,
    *,
    empty_profile: bool = False,
    owner_set: bool = False,
    lang: Lang = "en",
) -> None:
    port = existing_toml.get("bridge_port", DEFAULT_BRIDGE_PORT)
    connect = "/connect" if lang == "en" else "/conectar"
    setup = "/setup" if lang == "en" else "/configurar"
    io.print("")
    io.print(t("wizard.next", lang))
    io.print(t("wizard.next_start" if owner_set else "wizard.next_start_claim", lang))
    io.print(t("wizard.next_extension", lang))
    io.print(t("wizard.next_pair", lang, connect=connect, port=port))
    io.print(t("wizard.next_portals", lang))
    if empty_profile:
        io.print(t("wizard.next_profile_empty", lang, setup=setup))
    elif isinstance(profile_dir, str) and profile_dir:
        io.print(t("wizard.next_profile_fill", lang, path=profile_dir))
    else:
        io.print(t("wizard.next_profile_create", lang))
    io.print(t("wizard.next_guide", lang))


def run_init(
    base_dir: Path,
    io: WizardIO | None = None,
    *,
    runner: Runner = default_runner,
    which: Which = shutil.which,
    demo_profile: Path = DEMO_PROFILE,
    owner_id: int | None = None,
) -> int:
    """Run the wizard against the installation in ``base_dir``; return an exit code.

    ``owner_id`` (``cli.py init --owner-id``) plans ``owner_telegram_id`` for
    a user who already knows it; otherwise the owner is paired by the bot.
    """
    io = io or default_io()
    lang: list[Lang] = ["en"]  # what the cancel message speaks
    try:
        return _run(base_dir, io, runner, which, demo_profile, owner_id, lang)
    except _Cancelled:
        io.print("")
        io.print(t("wizard.cancelled", lang[0]))
        return 130


def _run(
    base_dir: Path,
    io: WizardIO,
    runner: Runner,
    which: Which,
    demo_profile: Path,
    owner_id: int | None,
    spoken: list[Lang],
) -> int:
    try:
        existing_toml = _read_toml(base_dir / LOCAL_TOML)
    except tomllib.TOMLDecodeError as exc:
        io.print(t("wizard.invalid_toml", "en", file=LOCAL_TOML, error=exc))
        return 1
    existing_env = _read_env(base_dir / ENV_FILE)
    plan = _Plan()

    lang = _step_language(io, plan, existing_toml)
    spoken[0] = lang
    _check_prerequisites(io, runner, which, lang)
    provider = _step_provider(io, plan, existing_env, existing_toml, lang)
    _step_telegram(io, plan, existing_env, existing_toml, lang, owner_id)
    _step_bridge_secret(io, plan, existing_env, lang)
    _step_profile(io, plan, base_dir, existing_toml, demo_profile, lang)
    _step_gmail(io, lang)

    if plan.is_empty():
        io.print("")
        io.print(t("wizard.nothing_to_change", lang))
    else:
        key = _PROVIDER_KEY.get(provider, "")
        missing = (
            key if key and key not in plan.env and not existing_env.get(key) else ""
        )
        _print_summary(io, plan, existing_toml, missing, lang)
        if not _yes(io, t("wizard.write_q", lang), lang):
            io.print(t("wizard.nothing_written", lang))
            return 0
        _apply(plan, base_dir, demo_profile)
        io.print(t("wizard.saved", lang))

    profile_dir = plan.toml.get("profile_dir", existing_toml.get("profile_dir"))
    owner = plan.toml.get("owner_telegram_id", existing_toml.get("owner_telegram_id"))
    _print_next_steps(
        io,
        existing_toml,
        profile_dir,
        empty_profile=plan.profile_kind == "empty",
        owner_set=owner not in _UNSET,
        lang=lang,
    )
    secret = plan.env.get(SECRET_KEY) or existing_env.get(SECRET_KEY)
    if secret and _yes(io, t("wizard.show_secret", lang), lang):
        io.print(f"  {SECRET_KEY}: {secret}")
    return 0
