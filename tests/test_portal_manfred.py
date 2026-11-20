"""Manfred portal adapter over the owner's saved results page (scaffolded).

RED until ``tests/fixtures/portals/manfred.html`` holds a SANITIZED copy of the
saved page (3+ cards, no personal data), ``manfred.expected.json`` lists the
cards it must yield, and ``extension/portals/manfred.js`` extracts them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pii import generic_pii_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
HOST = "www.getmanfred.com"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_manfred_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "manfred"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_manfred_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import manfred from './manfred.js';" in registry
    assert "manfred" in registry.split("PORTALS = [", 1)[1]


def test_manfred_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = (REPO_ROOT / "tests" / "fixtures" / "portals" / "manfred.html").read_text(
        encoding="utf-8"
    )
    assert "<script" not in html.lower()
    assert "<svg" not in html.lower()
    assert "src=" not in html.lower()  # company logos are alt-only
    assert generic_pii_hits(html) == []
    assert html.count("masonry-column") == 3


@requires_node
def test_build_search_url_ignores_keywords_and_keeps_barcelona() -> None:
    def url(loc: str) -> str:
        script = (
            "import('./extension/portals/manfred.js').then(m => "
            "process.stdout.write(m.default.buildSearchUrl("
            f"{{keywords: 'python', location: '{loc}'}})))"
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

    assert url("Barcelona") == (
        "https://www.getmanfred.com/ofertas-empleo?onlyActive=true&currency=%E2%82%AC"
        "&locations=ChIJ5TCOcRaYpBIRCmZHTz37sEQ%3ABarcelona%2C+Espa%C3%B1a"
    )
    assert url("Madrid") == (
        "https://www.getmanfred.com/ofertas-empleo?onlyActive=true&currency=%E2%82%AC"
    )


def test_manfred_is_in_the_bot_registry() -> None:
    from cva.ingest.sources import SOURCES, job_id_from_url, source_from_url

    url = "https://www.getmanfred.com/ofertas-empleo/8501/empresa-senior-oct26"
    assert source_from_url(url) == "manfred"
    assert job_id_from_url(url) == "8501"
    assert job_id_from_url("https://www.getmanfred.com/ofertas-empleo?x=1") is None
    assert SOURCES["manfred"].inline_only is True
