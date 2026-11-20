"""Markers and helpers for tests whose subject only exists on some platforms.

CI runs the suite on Linux, macOS and Windows. Use these only where the
behaviour itself is platform-specific, never to hide a portability bug.
"""

import os
from pathlib import Path

import pytest

posix_modes = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits; Windows only has a read-only flag",
)


def has_posix_modes() -> bool:
    """For tests that check more than the mode: assert the mode only here."""
    return os.name == "posix"


def symlink_or_skip(
    link: Path, target: Path, target_is_directory: bool = False
) -> None:
    """``link.symlink_to(target)``, skipping where symlinks need a privilege
    (Windows without Developer Mode or an elevated shell)."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:  # pragma: no cover - only without the privilege
        pytest.skip(f"symlinks unavailable here: {exc}")
