"""Characterization tests of the browser extension (extension-rewrite T1).

They pin what the extension DOES, not how: the service worker, popup and
options page named by ``extension/manifest.json`` run in Node over a fake
browser (``tests/fixtures/extension_characterization_harness_lib.js``) and are
driven only through Chrome events, runtime messages and the page controls'
click handlers. Assertions read storage, message responses, ``chrome.*`` calls,
fetches to the bot and exported/imported config files. They were written
against the vendor-derived extension and must stay green on its clean-room
rewrite; the behaviour they pin is described in ``docs/extension-spec.md``.
Skipped without ``node``, like the other extension tests.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
WORKER_HARNESS = FIXTURES / "extension_characterization_harness.js"
PAGES_HARNESS = FIXTURES / "extension_characterization_harness_pages.js"

pytestmark = [
    pytest.mark.skipif(not EXTENSION.is_dir(), reason="extension/ not present"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not available"),
]


def _run(harness: Path, area: str) -> None:
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


def test_alarm_setup_interval_and_firing() -> None:
    """Install/startup/settings-updated schedule ``check-jobs``; pause clears it."""
    _run(WORKER_HARNESS, "alarm")


def test_scrape_tab_lifecycle() -> None:
    """One background tab per round, reused when stored, always disposed."""
    _run(WORKER_HARNESS, "tab")


def test_legacy_storage_migrations() -> None:
    """``searches``->``beacons``, ``notifyOnFirstRun``, title filter fallbacks."""
    _run(WORKER_HARNESS, "migrate")


def test_seen_jobs_dedup_across_rounds() -> None:
    """First scan, repeat rounds, filtered-out offers, the 3000 cap, outages."""
    _run(WORKER_HARNESS, "seen")


def test_runtime_message_handlers() -> None:
    """run-now, concurrency guard, set-paused, test-scrape, clear-history, unknown."""
    _run(WORKER_HARNESS, "msg")


def test_round_contracts() -> None:
    """Bridge payload, lastCheck, badge, stats, session expiry, permissions."""
    _run(WORKER_HARNESS, "round")


def test_popup_run_now_and_pause_messages() -> None:
    """The popup's controls send run-now and set-paused (local pause only)."""
    _run(PAGES_HARNESS, "popup")


def test_options_export_config_format() -> None:
    """The exported JSON config: format, fields, never the bridge secret."""
    _run(PAGES_HARNESS, "export")


def test_options_import_config_round_trip_and_validation() -> None:
    """Importing an export restores it; cleaning, clamping and rejections."""
    _run(PAGES_HARNESS, "import")
