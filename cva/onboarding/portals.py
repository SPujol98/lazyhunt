"""Portals are enabled only in the browser extension (portals-extension-only).

Older ``/setup`` runs had a portals step that wrote ``enabled_portals`` into
``config.local.toml``, so a portal had to be enabled in ``/setup``, in the
extension's Options › Portals and in each search. Now the extension alone
decides (its terms warning, then the per-search portal toggles) and an unset
``enabled_portals`` lets the bridge accept every registered portal.

:func:`remove_enabled_portals` drops that key once, on the bot's next start
(:meth:`cva.service.JobService.migrate_portals_config` records that it ran,
so a list the owner sets by hand later is honoured: ``enabled_portals``
stays a valid manual advanced setting).
"""

import re
import stat
import tomllib
from pathlib import Path
from typing import Final

from cva.fsutil import atomic_write
from cva.onboarding.snapshot import write_target
from cva.setup.wizard import _open_brackets

ENABLED_PORTALS_KEY: Final[str] = "enabled_portals"


def toml_remove_key(base_text: str, key: str) -> str:
    """``base_text`` without the top-level ``key``.

    The line that assigns ``key`` before the first table (with the following
    lines of a multi-line array, and the comment ending the value) is
    removed; every other line keeps its exact bytes, line endings included,
    and a key of the same name inside a table is left alone. ``base_text``
    is returned unchanged when it has no such key. The result is parsed back
    (``ValueError`` when it is not TOML or still holds ``key``), so a file
    the bot could not read is never returned (the counterpart of
    :func:`cva.setup.wizard.toml_set_key`)."""
    lines = base_text.splitlines(keepends=True)
    first_table = next(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("[")),
        len(lines),
    )
    assign = re.compile(rf"^\s*{re.escape(key)}\s*=")
    start = next((i for i in range(first_table) if assign.match(lines[i])), None)
    if start is None:
        return base_text
    end, depth = start, _open_brackets(lines[start].split("=", 1)[1])
    while depth > 0 and end + 1 < len(lines):
        end += 1
        depth += _open_brackets(lines[end])
    del lines[start : end + 1]
    text = "".join(lines)
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"the result is not valid TOML: {exc}") from exc
    if key in parsed:
        raise ValueError(f"{key} could not be removed")
    return text


def remove_enabled_portals(config_path: Path) -> bool:
    """Remove ``enabled_portals`` from ``config_path`` (``config.local.toml``);
    ``True`` when the key was there and the file was rewritten.

    A missing file or a file without the key is not touched (not even
    rewritten). Otherwise only that key's lines go (every other byte and
    comment kept, :func:`toml_remove_key`) and the file is replaced
    atomically, its mode kept and a symlinked file written through its link,
    like the ``/setup`` writer. ``ValueError`` / ``OSError`` leave the file
    as it was."""
    if not config_path.is_file():
        return False
    # Bytes as they are (``read_text`` would turn CRLF into LF).
    base = config_path.read_bytes().decode("utf-8")
    text = toml_remove_key(base, ENABLED_PORTALS_KEY)
    if text == base:
        return False
    mode = stat.S_IMODE(config_path.stat().st_mode)
    atomic_write(write_target(config_path), text, mode=mode)
    return True
