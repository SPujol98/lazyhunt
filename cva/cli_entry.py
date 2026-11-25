"""The ``lazyhunt`` console command (``uv run lazyhunt <verb>``).

``pyproject.toml`` maps ``lazyhunt`` to :func:`main`. It runs the same
argparse surface as ``uv run cli.py`` (every verb, plus ``bot`` to start the
Telegram bot), shown as ``lazyhunt`` in ``--help``. ``cli.py`` and ``bot.py``
live at the repository root, outside the ``cva`` package, so the root is put
on ``sys.path`` first when the install did not already do it.
"""

import importlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

PROG: Final[str] = "lazyhunt"
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]


def _ensure_repo_root_importable() -> None:
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def main(argv: Sequence[str] | None = None) -> None:
    """Run ``cli.py``'s ``main`` with ``argv`` (default: the real arguments)."""
    _ensure_repo_root_importable()
    cli = importlib.import_module("cli")
    cli.main(argv, prog=PROG)
