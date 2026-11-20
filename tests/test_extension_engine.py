"""Service worker behaviours of the clean-room rewrite (extension-rewrite T2/T3).

They complement ``test_extension_characterization.py`` with the decisions of
``docs/extension-spec.md`` section 11 and S4 that the characterization tests
do not pin: D1 (legacy migration never moves auto searches), D3 (test-scrape
never touches the round's tab), D5 (texts name the portal), S4 (no Telegram)
and the LinkedIn noise-line rules of section 5.2. Driven through the public
surface only, over the same fake browser. Skipped without ``node``.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_engine_harness.js"

pytestmark = [
    pytest.mark.skipif(not EXTENSION.is_dir(), reason="extension/ not present"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not available"),
]


def _run(area: str) -> None:
    result = subprocess.run(
        ["node", str(HARNESS), area],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_legacy_migration_never_moves_auto_searches() -> None:
    """D1: only legacy entries with a ``url`` move from ``searches`` to ``beacons``."""
    _run("migrate")


def test_test_scrape_never_uses_the_round_tab() -> None:
    """D3: during a round it answers an error; otherwise it uses its own tab."""
    _run("testscrape")


def test_no_telegram_left() -> None:
    """S4: credentials dropped, no Telegram fetch, ``test-telegram`` unknown."""
    _run("telegram")


def test_user_texts_name_the_portal() -> None:
    """D5 and spec 2.5: session-expired and no-permission texts."""
    _run("texts")


def test_linkedin_noise_lines_and_positional_fields() -> None:
    """Spec 5.2: badges, dates and hints dropped; title cut to 200 characters."""
    _run("linkedin")


def test_scan_report_failure_never_breaks_the_round() -> None:
    """Spec 4.6: the scan report is best effort (HTTP error or network error)."""
    _run("report")


def test_no_scan_without_a_bridge() -> None:
    """S2 (public-release): no URL or secret -> no tab, no fetch, no seen state."""
    _run("nobridge")
