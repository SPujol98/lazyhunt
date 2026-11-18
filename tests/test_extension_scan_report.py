"""Extension side of beacon health (offer-gates-and-health T5, T5b).

``extension/scan_report.js`` is pure and loaded by a Node harness (skipped
without ``node``, same policy as the other extension tests). The wiring in
``background.js`` runs in a second harness over stubbed ``chrome.*``/``fetch``
(what a report says, that the error-report guard holds, that a hung report
cannot stall the round) and is also checked structurally.
"""

import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest

from cva.tracker.repo import ScanStatus

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HARNESS = FIXTURES / "extension_scan_report_harness.js"
BACKGROUND_HARNESS = FIXTURES / "extension_background_harness.js"

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _run_harness(script: Path) -> None:
    result = subprocess.run(
        ["node", str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@requires_node
def test_scan_report_js_behavior_via_node_harness() -> None:
    _run_harness(HARNESS)


@requires_node
def test_background_scan_report_wiring_via_node_harness() -> None:
    _run_harness(BACKGROUND_HARNESS)


def _background() -> str:
    return (EXTENSION / "background.js").read_text(encoding="utf-8")


def _round_block() -> str:
    background = _background()
    start = background.index("async function runCheckRound(")
    return background[start : background.index("\nchrome.runtime.onMessage", start)]


def test_scan_report_js_mentions_every_status_the_bot_accepts() -> None:
    source = (EXTENSION / "scan_report.js").read_text(encoding="utf-8")
    for status in get_args(ScanStatus):
        assert f"'{status}'" in source, f"scan_report.js never emits {status!r}"
