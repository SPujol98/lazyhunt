"""Service worker behaviours of the clean-room rewrite (extension-rewrite T2/T3).

They complement ``test_extension_characterization.py`` with the decisions of
``docs/extension-spec.md`` section 11 and S4 that the characterization tests
do not pin: D1 (legacy migration never moves auto searches), D3 (test-scrape
never touches the round's tab), D5 (texts name the portal), S4 (no Telegram)
and the LinkedIn noise-line rules of section 5.2. Driven through the public
surface only, over the same fake browser. Skipped without ``node``.
"""

import shutil
from pathlib import Path

import pytest

from tests.helpers import run_node_harness

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_engine_harness.js"

pytestmark = [
    pytest.mark.skipif(not EXTENSION.is_dir(), reason="extension/ not present"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node not available"),
]


def test_legacy_migration_never_moves_auto_searches() -> None:
    """D1: only legacy entries with a ``url`` move from ``searches`` to ``beacons``."""
    run_node_harness(HARNESS, "migrate")


def test_test_scrape_never_uses_the_round_tab() -> None:
    """D3: during a round it answers an error; otherwise it uses its own tab."""
    run_node_harness(HARNESS, "testscrape")


def test_no_telegram_left() -> None:
    """S4: credentials dropped, no Telegram fetch, ``test-telegram`` unknown."""
    run_node_harness(HARNESS, "telegram")


def test_user_texts_name_the_portal() -> None:
    """D5 and spec 2.5: session-expired and no-permission texts."""
    run_node_harness(HARNESS, "texts")


def test_linkedin_noise_lines_and_positional_fields() -> None:
    """Spec 5.2: badges, dates and hints dropped; title cut to 200 characters."""
    run_node_harness(HARNESS, "linkedin")


def test_scan_report_failure_never_breaks_the_round() -> None:
    """Spec 4.6: the scan report is best effort (HTTP error or network error)."""
    run_node_harness(HARNESS, "report")


def test_no_scan_without_a_bridge() -> None:
    """S2 (public-release): no URL or secret -> no tab, no fetch, no seen state."""
    run_node_harness(HARNESS, "nobridge")
