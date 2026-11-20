"""Undo what a failed ``/setup`` Confirm wrote (onboarding-v2 T5, S5).

Confirm writes several files one after another (``preferences.toml``,
``profile.toml`` and their ``.bak``, ``onboarding_answers.json``, the base
CV PDFs and their manifest, then the minimal profile). A :class:`Snapshot`
taken before the first write keeps the bytes and permissions of every one of
those paths, and of every file directly inside the base PDFs directory, so
:meth:`Snapshot.restore` puts the profile back exactly as it was: changed
files get their old bytes, new files are removed, a directory Confirm created
goes away again. A symlinked file is put back through its link (the
target gets the old bytes, the link stays).
"""

import os
import shutil
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _Kept:
    data: bytes
    mode: int


def _read(path: Path) -> _Kept | None:
    if not path.is_file():
        return None
    return _Kept(path.read_bytes(), stat.S_IMODE(path.stat().st_mode))


def write_target(path: Path) -> Path:
    """The file a write to ``path`` must replace: a symlink's target, so
    the link survives (a dotfiles-managed ``config.local.toml``), else
    ``path`` itself."""
    return path.resolve() if path.is_symlink() else path


def _write(path: Path, kept: _Kept) -> None:
    path = write_target(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(kept.data)
        os.chmod(name, kept.mode)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def _put_back(path: Path, kept: _Kept | None) -> None:
    if kept is None:
        path.unlink(missing_ok=True)
    elif _read(path) != kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, kept)


@dataclass(frozen=True)
class Snapshot:
    """The state of some files and of the files directly inside some dirs."""

    files: dict[Path, _Kept | None]
    dirs: dict[Path, dict[str, _Kept] | None]

    @classmethod
    def take(cls, files: Iterable[Path], dirs: Iterable[Path]) -> "Snapshot":
        """Read everything now (:class:`OSError` when something is unreadable)."""
        kept_dirs: dict[Path, dict[str, _Kept] | None] = {}
        for folder in dirs:
            if not folder.is_dir():
                kept_dirs[folder] = None
                continue
            kept_dirs[folder] = {
                child.name: kept
                for child in folder.iterdir()
                if (kept := _read(child)) is not None
            }
        return cls({path: _read(path) for path in files}, kept_dirs)

    def restore(self) -> None:
        """Put every recorded path back (raises the first :class:`OSError`)."""
        for folder, before in self.dirs.items():
            if before is None:  # created by what is being undone
                if folder.is_dir():
                    shutil.rmtree(folder)
                continue
            if folder.is_dir():
                for child in folder.iterdir():
                    if child.is_file() and child.name not in before:
                        child.unlink()
            for name, kept_file in before.items():
                _put_back(folder / name, kept_file)
        for path, kept in self.files.items():
            _put_back(path, kept)
