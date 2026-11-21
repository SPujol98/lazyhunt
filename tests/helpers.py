"""Small helpers shared by several test modules (identical copies hoisted here)."""

import asyncio
import subprocess
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from cva.tracker import OfferInput

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"


def read_extension(name: str) -> str:
    """Text of a file inside the browser-extension folder."""
    return (EXTENSION / name).read_text(encoding="utf-8")


def run_coro(coro: Coroutine[Any, Any, object]) -> None:
    """Drive one coroutine to completion on a fresh event loop."""
    asyncio.run(coro)


def run_node_harness(harness: Path, area: str) -> None:
    """Run a node test harness for one area; fail with its output otherwise."""
    result = subprocess.run(
        ["node", str(harness), area],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def numbered_offer(n: int = 1) -> OfferInput:
    """Deterministic link-sourced offer whose fields all derive from ``n``."""
    return OfferInput(
        job_id=str(n),
        title=f"T{n}",
        company=f"C{n}",
        url=f"https://x/{n}",
        text=f"text {n}",
        source="link",
    )
