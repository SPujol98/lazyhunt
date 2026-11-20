"""Small filesystem helpers shared by the setup wizard and the profile writer."""

import os
from pathlib import Path


def atomic_write(
    path: Path, text: str, mode: int | None = None, newline: str | None = None
) -> None:
    """Write ``text`` to a temporary file next to ``path`` and rename it over
    ``path``, so a crash never leaves a truncated file.

    ``mode`` sets the permissions of the new file (e.g. ``0o600`` for secrets);
    by default it is created ``0o644``. ``newline`` is :func:`open`'s
    (``""`` writes ``text`` with its line endings exactly as given).
    """
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if mode else 0o644)
    try:
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
