"""Small filesystem helpers shared by the setup wizard and the profile writer."""

import os
from pathlib import Path, PurePath


def atomic_write(path: Path, data: str | bytes, mode: int | None = None) -> None:
    """Write ``data`` to a temporary file next to ``path`` and rename it over
    ``path``, so a crash never leaves a truncated file.

    ``data`` is written exactly as given (text as UTF-8): line endings are
    never translated, so a file reads the same bytes on every OS and a CRLF
    file handed back keeps its CRLF. ``mode`` sets the permissions of the new
    file (e.g. ``0o600`` for secrets); by default it is created ``0o644``.
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if mode else 0o644)
    try:
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def display_path(path: PurePath | str) -> str:
    """``path`` with ``/`` separators: the one form lazyhunt shows, compares
    and stores, so messages, keys and saved values read the same on every OS
    (``C:\\x\\cv.pdf`` -> ``C:/x/cv.pdf``; Windows accepts both)."""
    return PurePath(path).as_posix()
