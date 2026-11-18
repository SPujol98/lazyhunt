"""InfoJobs adapter over a sanitized copy of the owner's real results page.

``tests/fixtures/portals/infojobs.html`` keeps the real card markup
(``ij-OfferCard*`` / ``sui-AtomCard-*``) of five cards (one duplicated, as on
the live page) with every name, id, logo and description replaced by neutral
values; the raw saved page is never committed.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pii import generic_pii_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "portals" / "infojobs.html"
HOST = "www.infojobs.net"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_infojobs_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "infojobs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_infojobs_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import infojobs from './infojobs.js';" in registry


def test_infojobs_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "<svg" not in html.lower()
    assert generic_pii_hits(html) == []
    assert not re.search(r"(?<![0-9a-f])\+?\d[\d ]{8,}\d(?![0-9a-f])", html)
    # real card markup kept, five cards with a duplicate
    assert html.count("ij-OfferCard ") == 5
