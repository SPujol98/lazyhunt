"""foorilla adapter: card extraction, age cutoff, detail panel and bot registry.

Checks the adapter against a sanitized copy of the owner's saved htmx fragment
(``tests/fixtures/portals``), the 7-day age cutoff, the ``#main2`` detail
extraction and the foorilla ``SourceSpec`` of the bot.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.pii import generic_pii_hits

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "fixtures" / "extension_portal_fixture_harness.js"
HOST = "foorilla.com"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_foorilla_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "foorilla"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_foorilla_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import foorilla from './foorilla.js';" in registry
    assert "foorilla" in registry.split("PORTALS = [", 1)[1]


def test_foorilla_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def _node(script: str) -> str:
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout


def test_fixture_is_sanitized() -> None:
    root = REPO_ROOT / "tests" / "fixtures" / "portals"
    for name in ("foorilla.html", "foorilla_detail.html"):
        html = (root / name).read_text(encoding="utf-8")
        assert "<script" not in html.lower()
        assert "<svg" not in html.lower()
        assert "<img" not in html.lower()
        assert generic_pii_hits(html) == []
    listing = (root / "foorilla.html").read_text(encoding="utf-8")
    assert listing.count("terminal-title") == 5


@requires_node
def test_build_search_url_uses_the_quick_search_param() -> None:
    def url(kw: str, loc: str) -> str:
        return _node(
            "import('./extension/portals/foorilla.js').then(m => "
            "process.stdout.write(m.default.buildSearchUrl("
            f"{{keywords: '{kw}', location: '{loc}'}})))"
        )

    expected = "https://foorilla.com/hiring/jobs/?job_search=backend+python"
    assert url("backend python", "Barcelona") == expected
    # The location is never sent: the bot's location gate decides.
    assert url("backend python", "remoto") == expected
    assert url("", "Barcelona") == "https://foorilla.com/hiring/jobs/"


@requires_node
def test_canonical_url_host_match_and_detail_selector() -> None:
    out = _node(
        "import('./extension/portals/foorilla.js').then(m => "
        "process.stdout.write(JSON.stringify(["
        "m.default.canonicalUrl('9900001'),"
        "m.default.hostMatch('https://foorilla.com/hiring/jobs/'),"
        "m.default.hostMatch('https://www.foorilla.com/x'),"
        "m.default.hostMatch('https://notfoorilla.com/x'),"
        "m.default.detailArgs])))"
    )
    assert json.loads(out) == [None, True, True, False, ["#main2", True]]


AGE_CASES = [
    ("7d ago", True),
    ("1w ago", True),
    ("8d ago", False),
    ("2w ago", False),
    ("1mo ago", False),
    ("2y ago", False),
    ("45m ago", True),
    ("23h ago", True),
    ("yesterday", True),
    ("", True),
]


@requires_node
def test_card_age_cutoff_keeps_seven_days_and_drops_older() -> None:
    items = "".join(
        '<li class="list-group-item"><div>'
        '<a class="terminal-title" href="" '
        f'hx-get="/hiring/jobs/job-{n}-{1000 + n}/">Job {n}</a>'
        f'<div class="terminal-meta"><small>{age}</small></div>'
        "</div></li>"
        for n, (age, _) in enumerate(AGE_CASES)
    )
    script = (
        "const {installDocument}=require('./tests/fixtures/minidom.js');"
        "import('./extension/portals/foorilla.js').then(async m => {"
        f"installDocument({json.dumps('<ul>' + items + '</ul>')});"
        "const cards=await m.default.extractCards();"
        "process.stdout.write(JSON.stringify(cards.map(c=>c.id)));})"
    )
    kept = json.loads(_node(script))
    expected = [str(1000 + n) for n, (_, keep) in enumerate(AGE_CASES) if keep]
    assert kept == expected


@requires_node
def test_detail_extractor_reads_only_the_job_panel() -> None:
    script = (
        "const fs=require('fs');"
        "const {installDocument}=require('./tests/fixtures/minidom.js');"
        "import('./extension/portals/foorilla.js').then(m => {"
        "installDocument(fs.readFileSync('tests/fixtures/portals/foorilla_detail.html','utf8'));"
        "const t=m.default.extractDetail(...m.default.detailArgs);"
        "process.stdout.write(JSON.stringify(t));})"
    )
    text = json.loads(_node(script))
    assert "Disenar y mantener servicios REST en Python" in text
    assert "Backend Engineer (Python)" in text
    assert "Otra oferta de ejemplo" not in text


def test_foorilla_is_in_the_bot_registry() -> None:
    from cva.ingest.sources import (
        SOURCES,
        canonical_url,
        job_id_from_url,
        source_from_url,
    )

    url = "https://foorilla.com/hiring/jobs/technical-lead-python-us-4108970/?x=1"
    assert source_from_url(url) == "foorilla"
    assert job_id_from_url(url) == "4108970"
    assert job_id_from_url("https://foorilla.com/hiring/jobs/") is None
    assert job_id_from_url("https://foorilla.com/hiring/jobs/top/") is None
    assert job_id_from_url("https://foorilla.com/hiring/jobs/?page=2") is None
    assert canonical_url("foorilla", "4108970", original=url) == (
        "https://foorilla.com/hiring/jobs/technical-lead-python-us-4108970/"
    )
    spec = SOURCES["foorilla"]
    assert spec.label == "foorilla"
    assert spec.inline_only is True
    # #main2 is filled by htmx: a plain server GET returns no description.
    assert spec.server_fetch is False
    assert spec.suggest_by_default is False
