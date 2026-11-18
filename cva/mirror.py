"""CV PDF mirror (auto-triage D5/D6): best-effort ``nuevos/`` → ``enviados/``.

A write-only projection of generated CVs onto a synced folder (Syncthing /
Drive pair the folders OUTSIDE the app): ``generate_cv`` copies the delivered
PDF into ``<root>/nuevos/``, ``mark_sent`` moves it to ``<root>/enviados/``,
``discard`` removes it from ``nuevos/``. ``adaptadas/`` stays canonical.

Every operation is best-effort per the cv-mirror spec: any ``OSError`` is
logged (WARNING) and the operation returns ``False`` — it NEVER raises, so a
broken mirror can never fail a service action. A disabled mirror
(``None``/blank root) does zero filesystem activity and logs zero warnings.
The root itself being unwritable warns at most once per root per session
(module-level ``_WARNED_ROOTS``) to keep a read-only share from flooding the
log.
"""

import errno
import logging
import os
import shutil
from pathlib import Path

_LOG = logging.getLogger(__name__)

# Roots whose unwritable warning was already emitted this session (D5).
_WARNED_ROOTS: set[Path] = set()

_PERMISSION_ERRNOS: frozenset[int] = frozenset({errno.EACCES, errno.EROFS})


def mirror_root(configured: str | Path | None) -> Path | None:
    """D8: ``None``/blank → disabled (``None``); otherwise the root path."""
    if configured is None:
        return None
    text = str(configured).strip()
    return Path(text) if text else None


class MirrorPaths:
    """The ``nuevos``/``enviados`` folder pair under one mirror root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @property
    def nuevos(self) -> Path:
        return self.root / "nuevos"

    @property
    def enviados(self) -> Path:
        return self.root / "enviados"


def _relative_name(pdf_path: Path) -> Path:
    """``<offer folder>/<file>``: generic file names repeat across offers, so
    the per-offer folder (``adaptadas/<id>_<company>/``) is part of the key."""
    return Path(pdf_path.parent.name) / pdf_path.name


def _prune_empty(folder: Path, stop: Path) -> None:
    """Best-effort removal of an emptied per-offer folder (never ``stop``)."""
    if folder != stop:
        try:
            folder.rmdir()
        except OSError:
            pass


def _warn_once_per_root(root: Path, exc: OSError) -> None:
    """WARNING per failure — but at most ONE for an unwritable root (D5)."""
    unwritable = isinstance(exc, PermissionError) or exc.errno in _PERMISSION_ERRNOS
    if unwritable:
        key = Path(os.path.abspath(root))
        if key in _WARNED_ROOTS:
            return
        _WARNED_ROOTS.add(key)
    _LOG.warning("mirror operation skipped (%s: %s)", type(exc).__name__, exc)


def on_generated(mirror: Path | None, pdf_path: Path) -> bool:
    """Copy the delivered PDF into ``<mirror>/nuevos/`` (overwrite silently)."""
    root = mirror_root(mirror)
    if root is None:
        return False
    try:
        target = MirrorPaths(root).nuevos / _relative_name(pdf_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, target)
    except OSError as exc:
        _warn_once_per_root(root, exc)
        return False
    return True


def on_sent(mirror: Path | None, pdf_path: Path) -> bool:
    """Move ``nuevos/<folder>/<name>`` → ``enviados/<folder>/<name>``
    (D6: EXDEV → copy+unlink)."""
    root = mirror_root(mirror)
    if root is None:
        return False
    folders = MirrorPaths(root)
    rel = _relative_name(pdf_path)
    source = folders.nuevos / rel
    dest = folders.enviados / rel
    if not source.is_file():
        _LOG.warning(
            "mirror on_sent: %s not present in nuevos/; nothing to move", source
        )
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copy2(source, dest)
            source.unlink()
        _prune_empty(source.parent, folders.nuevos)
    except OSError as exc:
        _warn_once_per_root(root, exc)
        return False
    return True


def on_discarded(mirror: Path | None, pdf_path: Path | None) -> bool:
    """Remove ``nuevos/<folder>/<name>``; absent file is a SILENT no-op (spec)."""
    root = mirror_root(mirror)
    if root is None or pdf_path is None:
        return False
    nuevos = MirrorPaths(root).nuevos
    target = nuevos / _relative_name(pdf_path)
    try:
        target.unlink()
        _prune_empty(target.parent, nuevos)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _warn_once_per_root(root, exc)
        return False
    return True
