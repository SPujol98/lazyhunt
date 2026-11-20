"""JobFluent portal adapter over the owner's saved results page (scaffolded).

RED until ``tests/fixtures/portals/jobfluent.html`` holds a SANITIZED copy of the
saved page (3+ cards, no personal data), ``jobfluent.expected.json`` lists the
cards it must yield, and ``extension/portals/jobfluent.js`` extracts them.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pii import generic_pii_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
FREEZE = REPO_ROOT / "tests" / "fixtures" / "freeze_date.js"
HOST = "www.jobfluent.com"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_jobfluent_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", "--require", str(FREEZE), str(HARNESS), "jobfluent"],
        cwd=REPO_ROOT,
        env={**os.environ, "FROZEN_NOW": "2026-10-28T12:00:00Z"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_jobfluent_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import jobfluent from './jobfluent.js';" in registry
    assert "jobfluent" in registry.split("PORTALS = [", 1)[1]


def test_jobfluent_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = (REPO_ROOT / "tests" / "fixtures" / "portals" / "jobfluent.html").read_text(
        encoding="utf-8"
    )
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "<svg" not in html.lower()
    assert generic_pii_hits(html) == []
    assert not re.search(r"(?<![0-9a-f])\+?\d[\d ]{8,}\d(?![0-9a-f])", html)
    assert html.count("panel-offer panel-default") == 4


def _node(expr: str) -> str:
    script = f"import('./extension/portals/jobfluent.js').then(m => {expr})"
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        encoding="utf-8",
    ).stdout


@requires_node
def test_build_search_url_known_and_unknown_cities() -> None:
    def url(loc: str) -> str:
        return _node(
            "process.stdout.write(m.default.buildSearchUrl("
            f"{{keywords: 'python', location: '{loc}'}}))"
        )

    assert url("Barcelona") == "https://www.jobfluent.com/jobs-barcelona?q=python"
    assert url("Madrid, España").startswith("https://www.jobfluent.com/jobs-madrid?")
    assert url("Valencia").startswith("https://www.jobfluent.com/jobs-valencia?")
    assert url("remoto").startswith("https://www.jobfluent.com/jobs-remote?")
    # unknown city: documented fallback to Barcelona
    assert url("Soria").startswith("https://www.jobfluent.com/jobs-barcelona?")


@requires_node
def test_age_cutoff_keeps_up_to_a_week() -> None:
    ages = {
        "000001": "added yesterday",
        "000002": "added 7 days ago",
        "000003": "added 8 days ago",
        "000004": "added 1 week ago",
        "000005": "added 2 weeks ago",
        "000006": "added 3 hours ago",
        "000007": "added 1 month ago",
        "000008": "",
    }
    html = "".join(
        f'<div id="offer-{i}" class="panel panel-offer panel-default"><h3 class="offer-title">'
        f'<a href="/jobs/x-{i}">T at C</a></h3><span class="published-date">{a}</span></div>'
        for i, a in ages.items()
    )
    script = (
        "const {installDocument} = require('./tests/fixtures/minidom.js');"
        "globalThis.setTimeout = (f) => setImmediate(f);"
        f"installDocument({json.dumps(html)});"
        "import('./extension/portals/jobfluent.js')"
        ".then(m => m.default.extractCards())"
        ".then(c => process.stdout.write(JSON.stringify(c.map(x => x.id))))"
    )
    out = subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        encoding="utf-8",
    ).stdout
    assert json.loads(out) == ["000001", "000002", "000004", "000006", "000008"]


def test_jobfluent_is_in_tab_only_on_the_bot_side() -> None:
    from cva.ingest.sources import SOURCES, job_id_from_url, source_from_url

    url = "https://www.jobfluent.com/jobs/backend-engineer-barcelona-f1a2b3?q=python"
    assert source_from_url(url) == "jobfluent"
    assert job_id_from_url(url) == "f1a2b3"
    assert job_id_from_url("https://www.jobfluent.com/jobs-barcelona?q=a1b2c3") is None
    assert SOURCES["jobfluent"].inline_only is True
    assert SOURCES["jobfluent"].server_fetch is False
