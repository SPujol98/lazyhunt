"""Indeed portal adapter over the owner's saved results page (scaffolded).

RED until ``tests/fixtures/portals/indeed.html`` holds a SANITIZED copy of the
saved page (3+ cards, no personal data), ``indeed.expected.json`` lists the
cards it must yield, and ``extension/portals/indeed.js`` extracts them.
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
HOST = "es.indeed.com"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_indeed_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "indeed"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_indeed_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import indeed from './indeed.js';" in registry
    assert "indeed" in registry.split("PORTALS = [", 1)[1]


def test_indeed_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = (REPO_ROOT / "tests" / "fixtures" / "portals" / "indeed.html").read_text(
        encoding="utf-8"
    )
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "<svg" not in html.lower()
    assert generic_pii_hits(html) == []
    assert not re.search(r"(?<![0-9a-f])\+?\d[\d ]{8,}\d(?![0-9a-f])", html)
    assert "bb=" not in html and "xkcb" not in html and "mobtk" not in html
    assert html.count("job_seen_beacon") == 4


def _search_url(args: str) -> str:
    script = (
        "import('./extension/portals/indeed.js').then(m => "
        f"process.stdout.write(m.default.buildSearchUrl({args})))"
    )
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout


@requires_node
def test_build_search_url_matches_the_owner_search() -> None:
    url = _search_url("{keywords: 'python', location: 'Barcelona'}")
    assert url == (
        "https://es.indeed.com/jobs?q=python&l=Barcelona%2C+Barcelona+provincia"
        "&radius=25&fromage=7"
    )


@requires_node
def test_build_search_url_remote_and_unknown_locations() -> None:
    remote = _search_url("{keywords: 'python', location: 'remoto'}")
    assert remote == "https://es.indeed.com/jobs?q=python&fromage=7"
    other = _search_url("{keywords: 'python', location: 'Madrid'}")
    assert "l=Madrid" in other and "fromage=7" in other


@requires_node
def test_canonical_url_from_the_job_key() -> None:
    script = (
        "import('./extension/portals/indeed.js').then(m => "
        "process.stdout.write(String(m.default.canonicalUrl('a1a1a1a1b2b2b2b2'))"
        " + '|' + String(m.default.canonicalUrl('zz'))))"
    )
    out = subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    assert out == "https://es.indeed.com/viewjob?jk=a1a1a1a1b2b2b2b2|null"
