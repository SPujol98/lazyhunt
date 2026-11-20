"""Adzuna adapter: card extraction, search URL and bot registry.

Checks the adapter against a sanitized copy of the owner's saved results page
(``tests/fixtures/portals``), the search URL (7-day filter, Barcelona and
remote), the extension host grant and the Adzuna ``SourceSpec`` of the bot.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pii import generic_pii_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
HOST = "www.adzuna.es"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_adzuna_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "adzuna"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_adzuna_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import adzuna from './adzuna.js';" in registry
    assert "adzuna" in registry.split("PORTALS = [", 1)[1]


def test_adzuna_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = (REPO_ROOT / "tests" / "fixtures" / "portals" / "adzuna.html").read_text(
        encoding="utf-8"
    )
    assert "<script" not in html.lower()
    assert "<svg" not in html.lower()
    assert "<img" not in html.lower()
    assert "src=" not in html.lower()
    assert generic_pii_hits(html) == []
    assert html.count("<article data-aid=") == 4


@requires_node
def test_build_search_url_matches_the_owner_url_and_remote_filter() -> None:
    def url(kw: str, loc: str) -> str:
        script = (
            "import('./extension/portals/adzuna.js').then(m => "
            "process.stdout.write(m.default.buildSearchUrl("
            f"{{keywords: '{kw}', location: '{loc}'}})))"
        )
        return subprocess.run(
            ["node", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            encoding="utf-8",
        ).stdout

    # The owner's URL: https://www.adzuna.es/search?f=7&loc=206508&q=backend
    assert url("backend", "Barcelona") == (
        "https://www.adzuna.es/search?f=7&loc=206508&q=backend"
    )
    assert url("backend", "Barcelona, Spain") == (
        "https://www.adzuna.es/search?f=7&loc=206508&q=backend"
    )
    # Unknown city: no loc (nationwide on the keywords).
    assert url("python dev", "Madrid") == (
        "https://www.adzuna.es/search?f=7&q=python+dev"
    )
    # Remote: the portal's own remote filter, nationwide.
    assert url("backend", "remoto") == (
        "https://www.adzuna.es/search?f=7&remote_only=1&q=backend"
    )


def test_adzuna_is_in_the_bot_registry() -> None:
    from cva.ingest.sources import (
        SOURCES,
        canonical_url,
        job_id_from_url,
        source_from_url,
    )

    url = "https://www.adzuna.es/details/5954311951?utm_source=x"
    assert source_from_url(url) == "adzuna"
    assert job_id_from_url(url) == "5954311951"
    assert job_id_from_url("https://www.adzuna.es/land/ad/5954156423?se=x") == (
        "5954156423"
    )
    assert job_id_from_url("https://www.adzuna.es/search?f=7&q=backend") is None
    assert canonical_url("adzuna", "5954311951") == (
        "https://www.adzuna.es/details/5954311951"
    )
    spec = SOURCES["adzuna"]
    assert spec.label == "Adzuna"
    assert spec.inline_only is True
    assert spec.server_fetch is False
    assert spec.suggest_by_default is False
