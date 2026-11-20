"""Interactive, re-runnable first-run setup behind ``cli.py init`` (public-release S3).

The wizard asks every question first, shows a summary of the planned changes
and writes only after the user confirms, so declining (or Ctrl-C at any
prompt) leaves the installation untouched. It writes:

* secrets (``CVA_TELEGRAM_TOKEN``, ``CVA_BRIDGE_SECRET``) to ``.env`` only,
  updating keys in place, mode 600 on POSIX, and never prints their values
  (the bridge secret is shown once, and only on request, at the very end);
* non-secret settings (``llm_provider``, ``owner_telegram_id``,
  ``profile_dir``) to ``config.local.toml``;
* a new profile directory: empty (recommended; the bot starts in setup mode
  and the guided ``/setup`` in Telegram fills it in), a skeleton with clearly
  marked placeholders, or a verbatim copy of the fictional demo profile. An
  existing profile directory is never modified. ``profile_dir`` is always
  written explicitly, so the bot never falls back to the legacy layout.

A key that already has a different value is replaced only after an explicit
"yes". Re-running with the same answers changes nothing.

Claude Code checks run the user's own ``claude`` binary: ``claude --version``
and ``claude auth status --json``, of which only the boolean ``loggedIn`` is
read. Claude credential files are never opened, and no network call is made
by the wizard itself.

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

from cva.fsutil import atomic_write

Runner = Callable[[Sequence[str]], tuple[int, str]]
Which = Callable[[str], str | None]

ENV_FILE: Final[str] = ".env"
LOCAL_TOML: Final[str] = "config.local.toml"
TOKEN_KEY: Final[str] = "CVA_TELEGRAM_TOKEN"
SECRET_KEY: Final[str] = "CVA_BRIDGE_SECRET"
DEFAULT_PROFILE: Final[str] = "profiles/me"
DEFAULT_BRIDGE_PORT: Final[int] = 8765
UI_LANGUAGES: Final[tuple[str, ...]] = ("auto", "en", "es")
PROVIDERS: Final[tuple[str, ...]] = ("claude_code", "anthropic", "openai_compatible")
_PROVIDER_KEY: Final[dict[str, str]] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible": "CVA_LLM_API_KEY",
}
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DEMO_PROFILE: Final[Path] = _REPO_ROOT / "profiles" / "demo"
_MIN_PYTHON: Final[tuple[int, int]] = (3, 12)
_TOKEN_SHAPE: Final[re.Pattern[str]] = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_UNSET: Final[tuple[object, ...]] = (None, "", 0)
# Answer of the profile-kind question -> what ``_create_profile`` makes.
_PROFILE_KINDS: Final[dict[str, str]] = {"1": "skeleton", "2": "demo", "3": "empty"}


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
            list(argv), capture_output=True, text=True, timeout=30, check=False
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


def _ask(io: WizardIO, prompt: str, default: str = "") -> str:
    try:
        answer = io.input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _Cancelled from exc
    return answer or default


def _yes(io: WizardIO, prompt: str) -> bool:
    return _ask(io, f"{prompt} [y/N]: ").lower() in ("y", "yes")


def _ask_secret(io: WizardIO, prompt: str) -> str:
    try:
        return io.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise _Cancelled from exc


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


def _check_prerequisites(io: WizardIO, runner: Runner, which: Which) -> None:
    io.print("Step 1/7 - Prerequisites")
    found = sys.version_info[:2]
    version = ".".join(str(part) for part in sys.version_info[:3])
    if found >= _MIN_PYTHON:
        io.print(f"  Python {version}: OK")
    else:
        io.print(f"  Python {version}: lazyhunt needs Python 3.12 or newer.")
    if which("uv") is None:
        io.print(
            "  uv was not found on PATH: install it from https://docs.astral.sh/uv/"
        )
    claude = which("claude")
    if claude is None:
        io.print(
            "  `claude` was not found on PATH. The default AI provider is your own "
            "Claude Code CLI: install it from "
            "https://docs.anthropic.com/en/docs/claude-code and log in, or choose "
            "another provider in the next step. Continuing."
        )
        return
    code, out = runner([claude, "--version"])
    first_line = out.strip().splitlines()[0][:80] if code == 0 and out.strip() else ""
    if first_line:
        io.print(f"  Claude Code: {first_line}")
    else:
        io.print("  Claude Code: found, but `claude --version` did not answer.")
    logged_in = _parse_logged_in(*runner([claude, "auth", "status", "--json"]))
    if logged_in is True:
        io.print("  Claude Code: logged in.")
    elif logged_in is False:
        io.print(
            "  Claude Code is installed but not logged in: run `claude auth login` "
            "(or start `claude` and use /login), then re-run this wizard if you like."
        )
    else:
        io.print(
            "  Could not tell whether Claude Code is logged in; check it with "
            "`claude auth status`."
        )


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
    shown = "" if secret else f" (current value: {current})"
    if _yes(io, f"  {key} is already set in {where}{shown}. Replace it?"):
        plan_values[key] = value  # type: ignore[assignment]
        return True
    io.print(f"  Keeping the existing {key}.")
    return False


def _step_language(io: WizardIO, plan: _Plan, existing_toml: dict[str, object]) -> str:
    io.print("")
    io.print("Step 2/7 - Bot language")
    current = existing_toml.get("ui_language")
    default = (
        current if isinstance(current, str) and current in UI_LANGUAGES else "auto"
    )
    io.print(
        "  The language of the Telegram bot, the command line and the dashboard. "
        "CVs and cover letters keep following each offer's language. "
        "auto = your system language; choose en or es to force one."
    )
    while True:
        language = _ask(
            io, f"  Language ({', '.join(UI_LANGUAGES)}) [{default}]: ", default
        ).lower()
        if language in UI_LANGUAGES:
            break
        io.print(f"  Please choose one of: {', '.join(UI_LANGUAGES)}.")
    _propose(
        io,
        plan.toml,
        existing_toml,
        "ui_language",
        language,
        where=LOCAL_TOML,
        secret=False,
    )
    return language


def _step_provider(io: WizardIO, plan: _Plan, existing_toml: dict[str, object]) -> str:
    io.print("")
    io.print("Step 3/7 - AI provider")
    current = existing_toml.get("llm_provider")
    default = (
        current if isinstance(current, str) and current in PROVIDERS else PROVIDERS[0]
    )
    io.print(
        "  claude_code (default): your own installed and logged-in Claude Code CLI; "
        "no API key involved. anthropic: an Anthropic API key. openai_compatible: "
        "any OpenAI-compatible endpoint. See docs/providers.md."
    )
    while True:
        provider = _ask(io, f"  Provider [{default}]: ", default)
        if provider in PROVIDERS:
            break
        io.print(f"  Please choose one of: {', '.join(PROVIDERS)}.")
    _propose(
        io,
        plan.toml,
        existing_toml,
        "llm_provider",
        provider,
        where=LOCAL_TOML,
        secret=False,
    )
    if provider in _PROVIDER_KEY:
        io.print(
            f"  Add {_PROVIDER_KEY[provider]}=<your key> to .env yourself; the wizard "
            "does not ask for API keys."
        )
    return provider


def _step_telegram(
    io: WizardIO,
    plan: _Plan,
    existing_env: dict[str, str],
    existing_toml: dict[str, object],
) -> None:
    io.print("")
    io.print("Step 4/7 - Telegram")
    io.print(
        "  Bot token: in Telegram, open @BotFather, send /newbot and follow the "
        "steps; it replies with a token like 123456789:AA... Keep it private."
    )
    io.print(
        "  Your numeric user id: send any message to @userinfobot; it replies with "
        "your id. The bot answers only this id."
    )
    token = _ask_secret(io, "  Bot token (hidden; Enter to skip): ")
    if token:
        if not _TOKEN_SHAPE.match(token):
            io.print(
                "  Note: that does not look like a BotFather token; saving it anyway."
            )
        if _propose(
            io, plan.env, existing_env, TOKEN_KEY, token, where=ENV_FILE, secret=True
        ):
            plan.env_replaced.add(TOKEN_KEY)
    elif existing_env.get(TOKEN_KEY):
        io.print("  Keeping the token already in .env.")
    else:
        io.print(f"  Skipped: set {TOKEN_KEY} in .env before starting the bot.")

    while True:
        answer = _ask(io, "  Your numeric Telegram user id (Enter to skip): ")
        if not answer:
            break
        if answer.isdigit() and int(answer) > 0:
            _propose(
                io,
                plan.toml,
                existing_toml,
                "owner_telegram_id",
                int(answer),
                where=LOCAL_TOML,
                secret=False,
            )
            break
        io.print("  Please enter digits only, for example 123456789.")


def _step_bridge_secret(
    io: WizardIO, plan: _Plan, existing_env: dict[str, str]
) -> None:
    io.print("")
    io.print("Step 5/7 - Extension bridge secret")
    io.print(
        "  The browser extension sends offers to the bot over 127.0.0.1 with a "
        "shared secret."
    )
    if existing_env.get(SECRET_KEY):
        if _yes(
            io,
            "  A bridge secret is already set in .env. Replace it with a new "
            "random one (the extension must then be updated)?",
        ):
            plan.env[SECRET_KEY] = secrets.token_urlsafe(32)
            plan.env_replaced.add(SECRET_KEY)
        else:
            io.print("  Keeping the existing bridge secret.")
        return
    plan.env[SECRET_KEY] = secrets.token_urlsafe(32)
    io.print(
        f"  Generated a new random secret; it will be stored in .env as {SECRET_KEY}."
    )


def _step_profile(
    io: WizardIO,
    plan: _Plan,
    base_dir: Path,
    existing_toml: dict[str, object],
    demo_profile: Path,
) -> None:
    io.print("")
    io.print("Step 6/7 - Profile directory")
    io.print(
        "  Everything lazyhunt knows about you (claims, base CVs, voice, "
        "preferences) lives in one directory. It holds personal data: keep it out "
        "of version control (profiles/* is already gitignored, except the demo)."
    )
    current = existing_toml.get("profile_dir")
    default = (
        current if isinstance(current, str) and current.strip() else DEFAULT_PROFILE
    )
    while True:
        answer = _ask(io, f"  Profile directory [{default}]: ", default)
        dest = _profile_destination(answer, base_dir, demo_profile)
        if dest is not None:
            break
        io.print(
            "  Choose a directory inside this directory and outside profiles/demo "
            "(for example profiles/me). To keep your profile somewhere else, set "
            "profile_dir in config.local.toml by hand."
        )
    if dest.exists():
        if not dest.is_dir():
            io.print(f"  {answer} exists and is not a directory; skipped.")
            return
        io.print(f"  {answer} already exists; it is used as is and never modified.")
    elif not demo_profile.is_dir():
        io.print(
            f"  The demo profile was not found at {demo_profile}; create {answer} "
            "yourself (see profiles/demo in the repository for the layout)."
        )
    else:
        io.print(
            "  [1] a skeleton with the demo's layout and clearly marked placeholders"
        )
        io.print("  [2] a copy of the fictional demo profile, to try lazyhunt first")
        io.print(
            "  [3] an empty profile: start the bot and answer its guided /setup "
            "in Telegram (recommended)"
        )
        while True:
            kind = _ask(io, "  Start from [3]: ", "3")
            if kind in _PROFILE_KINDS:
                break
            io.print("  Please answer 1, 2 or 3.")
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
    )


def _step_gmail(io: WizardIO) -> None:
    io.print("")
    io.print("Step 7/7 - Gmail (optional, off by default)")
    io.print(
        "  Gmail sync is read-only and uses an OAuth client you create in your own "
        "Google Cloud project. To enable it later, follow docs/gmail.md, run "
        "`uv run cli.py gmail-auth` and set gmail_enabled = true in "
        "config.local.toml. Nothing is configured now."
    )


# --- summary and writing --------------------------------------------------


def _print_summary(io: WizardIO, plan: _Plan, existing_toml: dict[str, object]) -> None:
    io.print("")
    io.print("Planned changes:")
    for key in plan.env:
        verb = "replace" if key in plan.env_replaced else "set"
        io.print(f"  {ENV_FILE}: {verb} {key} (value hidden)")
    for key, value in plan.toml.items():
        before = existing_toml.get(key)
        if before in _UNSET:
            io.print(f"  {LOCAL_TOML}: + {key} = {_toml_value(value)}")
        else:
            io.print(f"  {LOCAL_TOML}: ~ {key}: {before!r} -> {_toml_value(value)}")
    if plan.profile_dest is not None:
        io.print(f"  create {plan.profile_label}/ ({plan.profile_kind})")


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
            path.write_text(_md_placeholder(rel), encoding="utf-8")
        elif path.suffix == ".toml":
            original = path.read_text(encoding="utf-8")
            path.write_text(_toml_header(rel) + original, encoding="utf-8")
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
        )
    (dest / "README.md").write_text(_PROFILE_README, encoding="utf-8")


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
) -> None:
    port = existing_toml.get("bridge_port", DEFAULT_BRIDGE_PORT)
    io.print("")
    io.print("Next steps:")
    io.print(
        "  1. Load the extension: open chrome://extensions, turn on Developer mode, "
        "click Load unpacked and select the extension/ directory."
    )
    io.print(
        f"  2. In the extension's Options set the bridge URL to "
        f"http://127.0.0.1:{port}/bridge and paste the bridge secret "
        f"({SECRET_KEY} in .env)."
    )
    io.print(
        "  3. In Options > Portals enable only the portals whose terms you have read "
        "and accept; each one shows its terms warning and asks the browser for "
        "permission to that single domain. Some portals (notably LinkedIn) prohibit "
        "automated access and your account could be restricted."
    )
    if empty_profile:
        io.print(
            "  4. Your profile is empty: once the bot runs, send /setup to it in "
            "Telegram to set up your profile."
        )
    elif isinstance(profile_dir, str) and profile_dir:
        io.print(f"  4. Fill in your profile in {profile_dir} (see its README.md).")
    else:
        io.print(
            "  4. Create your profile directory and set profile_dir (see docs/getting-started.md)."
        )
    io.print(
        "  5. Start the bot with `uv run bot.py` and send /help to it in Telegram."
    )
    io.print("  Full guide: docs/getting-started.md")


def run_init(
    base_dir: Path,
    io: WizardIO | None = None,
    *,
    runner: Runner = default_runner,
    which: Which = shutil.which,
    demo_profile: Path = DEMO_PROFILE,
) -> int:
    """Run the wizard against the installation in ``base_dir``; return an exit code."""
    io = io or default_io()
    try:
        return _run(base_dir, io, runner, which, demo_profile)
    except _Cancelled:
        io.print("")
        io.print("Setup cancelled. Nothing was written.")
        return 130


def _run(
    base_dir: Path, io: WizardIO, runner: Runner, which: Which, demo_profile: Path
) -> int:
    io.print("lazyhunt setup. Every step can be skipped with Enter, and nothing is")
    io.print("written until you confirm the summary at the end. Safe to re-run.")
    io.print("")
    try:
        existing_toml = _read_toml(base_dir / LOCAL_TOML)
    except tomllib.TOMLDecodeError as exc:
        io.print(f"{LOCAL_TOML} is not valid TOML ({exc}); fix it and run init again.")
        return 1
    existing_env = _read_env(base_dir / ENV_FILE)
    plan = _Plan()

    _check_prerequisites(io, runner, which)
    _step_language(io, plan, existing_toml)
    _step_provider(io, plan, existing_toml)
    _step_telegram(io, plan, existing_env, existing_toml)
    _step_bridge_secret(io, plan, existing_env)
    _step_profile(io, plan, base_dir, existing_toml, demo_profile)
    _step_gmail(io)

    if plan.is_empty():
        io.print("")
        io.print("Nothing to change: your configuration already matches these answers.")
    else:
        _print_summary(io, plan, existing_toml)
        if not _yes(io, "Write these changes?"):
            io.print("Nothing was written.")
            return 0
        _apply(plan, base_dir, demo_profile)
        io.print(
            "Saved. Secrets went to .env only (mode 600), settings to config.local.toml."
        )

    profile_dir = plan.toml.get("profile_dir", existing_toml.get("profile_dir"))
    _print_next_steps(
        io, existing_toml, profile_dir, empty_profile=plan.profile_kind == "empty"
    )
    secret = plan.env.get(SECRET_KEY) or existing_env.get(SECRET_KEY)
    if secret and _yes(
        io, "Show the bridge secret now to paste it into the extension?"
    ):
        io.print(f"  {SECRET_KEY}: {secret}")
    return 0
