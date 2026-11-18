"""Public-suite simulation: the owner's private persona data, made unreadable.

The public repository ships without the owner's claims, masters, PDFs, offers
and judge text. ``CVA_PUBLIC_SUITE=1`` reproduces that on the owner's machine:
every open/stat/listing of those files fails exactly like a missing file, and
tests marked ``owner`` are skipped. Without the variable the suite is
unchanged, and the owner tests run whenever the files are present.
"""

import builtins
import io
import os
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pytest

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Files and directories that hold the owner's private data.
OWNER_PATHS: Final[tuple[Path, ...]] = (
    REPO_ROOT / "cva" / "claimsdb" / "claims.json",
    REPO_ROOT / "cva" / "match" / "synonyms.json",
    REPO_ROOT / "masters",
    REPO_ROOT / "cv_base_pdfs",
    REPO_ROOT / "data" / "cover_letter_voice.md",
    REPO_ROOT / "data" / "cover_letter_base.toml",
    REPO_ROOT / "data" / "salary_reference.toml",
    REPO_ROOT / "docs" / "honest_gaps.md",
    REPO_ROOT / "eval",
)
_WRAPPED_MARK: Final[str] = "_cva_public_suite_original"
# (module, attribute) of every path-taking call that could reveal an owner file.
# Predicates answer "no" for a hidden path, exactly as for a missing one; the
# rest raise ``FileNotFoundError``.
_PREDICATES: Final[frozenset[str]] = frozenset({"access", "lexists"})
_PATCHED: Final[tuple[tuple[Any, str], ...]] = (
    (builtins, "open"),
    (os, "stat"),
    (os, "lstat"),
    (os, "open"),
    (os, "access"),
    (os, "scandir"),
    (os, "listdir"),
    (os.path, "lexists"),
)
_state = threading.local()
_roots: tuple[str, ...] = ()
_installed = False


def public_suite_requested() -> bool:
    return os.environ.get("CVA_PUBLIC_SUITE") == "1"


def owner_data_present() -> bool:
    """True when the owner's files exist and are not being hidden."""
    if public_suite_requested():
        return False
    return all(path.exists() for path in OWNER_PATHS)


def _root_spellings(paths: Iterable[Path]) -> tuple[str, ...]:
    """Each root as given and fully resolved (a symlinked prefix still matches)."""
    spellings: dict[str, None] = {}
    for path in paths:
        spellings[os.path.normpath(str(path))] = None
        spellings[os.path.realpath(path)] = None
    return tuple(spellings)


def _dir_fd_base(dir_fd: int | None) -> str | None:
    if dir_fd is None:
        return None
    try:
        return os.readlink(f"/proc/self/fd/{dir_fd}")
    except OSError:
        return None


def _is_hidden(
    target: object, base: str | None = None, dir_fd: int | None = None
) -> bool:
    """Whether ``target`` (relative to ``base``/``dir_fd``/cwd) is an owner path."""
    if isinstance(target, int) or target is None or getattr(_state, "busy", False):
        return False
    try:
        raw = os.fspath(target)  # type: ignore[call-overload]
    except TypeError:
        return False
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    _state.busy = True  # realpath stats through the patched calls
    try:
        if not os.path.isabs(raw):
            raw = os.path.join(base or _dir_fd_base(dir_fd) or os.getcwd(), raw)
        candidates = {os.path.normpath(raw), os.path.realpath(raw)}
    finally:
        _state.busy = False
    return any(
        cand == root or cand.startswith(root + os.sep)
        for cand in candidates
        for root in _roots
    )


def _blocked(target: object) -> FileNotFoundError:
    return FileNotFoundError(2, "owner data hidden (CVA_PUBLIC_SUITE=1)", str(target))


def _wrap_first_arg(
    real: Callable[..., Any], *, predicate: bool = False
) -> Callable[..., Any]:
    def guarded(target: Any = None, *args: Any, **kwargs: Any) -> Any:
        # ``scandir()`` / ``listdir()`` without an argument list the cwd.
        probe = "." if target is None else target
        if _is_hidden(probe, dir_fd=kwargs.get("dir_fd")):
            if predicate:
                return False
            raise _blocked(target)
        if target is None and not args:
            return real(**kwargs)
        return real(target, *args, **kwargs)

    setattr(guarded, _WRAPPED_MARK, real)
    return guarded


def install_hiding(paths: Iterable[Path] = OWNER_PATHS) -> None:
    """Hide ``paths`` (default: the owner's data); idempotent, never double-wraps."""
    global _installed, _roots
    _roots = _root_spellings(paths)
    if _installed:
        return
    for module, name in _PATCHED:
        current = getattr(module, name)
        if not hasattr(current, _WRAPPED_MARK):
            setattr(
                module, name, _wrap_first_arg(current, predicate=name in _PREDICATES)
            )
    # pathlib reads through io.open: keep it the same guarded object.
    io.open = builtins.open
    _installed = True


def uninstall_hiding() -> None:
    """Undo ``install_hiding`` (the real calls come back)."""
    global _installed, _roots
    for module, name in _PATCHED:
        current = getattr(module, name)
        original = getattr(current, _WRAPPED_MARK, None)
        if original is not None:
            setattr(module, name, original)
    io.open = builtins.open
    _installed = False
    _roots = ()


@contextmanager
def hiding(paths: Iterable[Path]) -> Iterator[None]:
    """Hide ``paths`` inside the block, then restore the previous state."""
    global _roots
    was_installed, previous = _installed, _roots
    install_hiding(paths)
    try:
        yield
    finally:
        if was_installed:
            _roots = previous
        else:
            uninstall_hiding()


def install_if_requested() -> None:
    if public_suite_requested():
        install_hiding()


def skip_owner_items(items: list[pytest.Item]) -> None:
    if owner_data_present():
        return
    reason = (
        "owner data hidden (CVA_PUBLIC_SUITE=1)"
        if public_suite_requested()
        else "owner's private persona data not present"
    )
    marker = pytest.mark.skip(reason=reason)
    for item in items:
        if item.get_closest_marker("owner") is not None:
            item.add_marker(marker)
