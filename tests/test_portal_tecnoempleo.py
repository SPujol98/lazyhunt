"""Tecnoempleo portal adapter over the owner's saved results page (scaffolded).

RED until ``tests/fixtures/portals/tecnoempleo.html`` holds a SANITIZED copy of the
saved page (3+ cards, no personal data), ``tecnoempleo.expected.json`` lists the
cards it must yield, and ``extension/portals/tecnoempleo.js`` extracts them.
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
HOST = "www.tecnoempleo.com"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_tecnoempleo_adapter_over_the_saved_page() -> None:
    result = subprocess.run(
        ["node", "--require", str(FREEZE), str(HARNESS), "tecnoempleo"],
        cwd=REPO_ROOT,
        env={**os.environ, "FROZEN_NOW": "2026-10-28T12:00:00Z"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_tecnoempleo_is_registered() -> None:
    registry = (REPO_ROOT / "extension" / "portals" / "registry.js").read_text(
        encoding="utf-8"
    )
    assert "import tecnoempleo from './tecnoempleo.js';" in registry
    assert "tecnoempleo" in registry.split("PORTALS = [", 1)[1]


def test_tecnoempleo_host_is_granted_to_the_extension() -> None:
    manifest = json.loads(
        (REPO_ROOT / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    assert f"https://{HOST}/*" in manifest["optional_host_permissions"]


def test_fixture_is_sanitized() -> None:
    html = (
        REPO_ROOT / "tests" / "fixtures" / "portals" / "tecnoempleo.html"
    ).read_text(encoding="utf-8")
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "<svg" not in html.lower()
    assert generic_pii_hits(html) == []
    assert not re.search(r"(?<![0-9a-f])\+?\d[\d ]{8,}\d(?![0-9a-f])", html)
    assert html.count("p-3 border rounded") == 4


def _node(expr: str) -> str:
    script = f"import('./extension/portals/tecnoempleo.js').then(m => {expr})"
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout


@requires_node
def test_build_search_url_province_and_unknown_city() -> None:
    barcelona = _node(
        "process.stdout.write(m.default.buildSearchUrl("
        "{keywords: 'python', location: 'Barcelona'}))"
    )
    assert barcelona == "https://www.tecnoempleo.com/ofertas-trabajo/?te=python&pr=240"
    madrid = _node(
        "process.stdout.write(m.default.buildSearchUrl("
        "{keywords: 'python', location: 'Madrid'}))"
    )
    assert madrid.endswith("te=python&pr=263")
    unknown = _node(
        "process.stdout.write(m.default.buildSearchUrl("
        "{keywords: 'python', location: 'Soria'}))"
    )
    assert unknown == "https://www.tecnoempleo.com/ofertas-trabajo/?te=python"


def test_tecnoempleo_is_in_tab_only_on_the_bot_side() -> None:
    from cva.ingest.sources import SOURCES, is_inline_source, source_from_url

    url = "https://www.tecnoempleo.com/x-y/z/rf-a1b2c3d4e5f6a1b2c3d4"
    assert source_from_url(url) == "tecnoempleo"
    assert SOURCES["tecnoempleo"].inline_only is True
    assert is_inline_source("tecnoempleo") is True
