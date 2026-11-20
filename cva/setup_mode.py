"""Setup mode (onboarding-v2 T1): is the configured profile still unconfigured?

``cli.py init`` can create an EMPTY profile directory that the guided setup in
Telegram fills in later. Until every file the strict loader
(``JobService.from_settings``) cannot do without exists, the bot starts in setup
mode instead of crashing.

The check is explicit and cheap (file existence only, nothing is parsed):

* ``profile_dir`` unset is never setup mode: the owner's legacy layout keeps
  starting exactly as before;
* a missing required file means setup mode;
* every required file present means "complete": the strict loader runs and a
  broken file (malformed ``claims.json``...) still fails loudly.

While the bot is already in setup mode, the switch to the full app first runs
that strict loader; :func:`describe_load_error` turns its failure into a short
message for the owner (relative path, no absolute paths).
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Final

from cva.config import Settings
from cva.profile_persona import JUDGE_FILES

# The files whose absence makes ``JobService.from_settings`` fail; every other
# profile file is optional (the loaders fall back to their defaults).
REQUIRED_PROFILE_FILES: Final[tuple[str, ...]] = (
    "claims.json",
    "synonyms.json",
    *(f"judge/{name}" for name in JUDGE_FILES),
)


def missing_profile_files(profile_dir: Path | None) -> tuple[str, ...]:
    """The required files ``profile_dir`` lacks (``()`` for the legacy layout)."""
    if profile_dir is None:
        return ()
    return tuple(
        rel for rel in REQUIRED_PROFILE_FILES if not (profile_dir / rel).is_file()
    )


def needs_setup(settings: Settings) -> bool:
    """True while the configured profile directory is not set up yet."""
    return bool(missing_profile_files(settings.profile_dir))


_MAX_ERROR_CHARS: Final[int] = 300
# Absolute paths (POSIX with at least two segments, or Windows) left in an
# error message after the profile directory itself was made relative; a
# command such as "/setup" or "ES/EN" is not one.
_ABSOLUTE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.:/])(?:/[^\s/:'\"()]+){2,}|\b[A-Za-z]:\\[^\s:'\"()]+"
)


def _syntax_error(profile_dir: Path) -> str | None:
    """``"<file>: <error>"`` for the first top-level JSON/TOML file of the
    profile that does not parse (the loaders' own errors do not say which)."""
    try:
        paths = sorted(profile_dir.iterdir())
    except OSError:
        return None
    for path in paths:
        if not path.is_file() or path.suffix not in (".json", ".toml"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                json.loads(text)
            else:
                tomllib.loads(text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return f"{path.name}: {exc}"
    return None


def describe_load_error(profile_dir: Path, exc: BaseException) -> str:
    """A short owner-facing description of why the complete profile in
    ``profile_dir`` failed to load: the malformed file when one does not
    parse, else the loader's message; paths relative, never absolute."""
    text = _syntax_error(profile_dir) or f"{type(exc).__name__}: {exc}"
    for prefix in (f"{profile_dir}/", f"{profile_dir}\\", str(profile_dir)):
        text = text.replace(prefix, "")
    text = _ABSOLUTE_PATH_RE.sub(lambda m: re.split(r"[/\\]", m.group(0))[-1], text)
    text = text.strip()
    if len(text) > _MAX_ERROR_CHARS:
        text = text[: _MAX_ERROR_CHARS - 1] + "…"
    return text
