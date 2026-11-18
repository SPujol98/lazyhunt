"""Portal adapter contract (portal-adapters T1).

``extension/portals/<id>.js`` hold one adapter per portal and
``extension/portals/registry.js`` the list plus ``portalFor(url)``. The Node
harness (skipped without ``node``) runs the adapters over saved synthetic HTML
snippets with a minimal DOM; background.js is checked structurally for the
registry dispatch.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_portals_harness.js"

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_portal_adapters_via_node_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _background() -> str:
    return (EXTENSION / "background.js").read_text(encoding="utf-8")


def test_adapters_are_self_contained_page_functions() -> None:
    """executeScript serializes extractCards: it may not import or close over
    module scope, so the adapter files keep it free of references to other
    top-level bindings."""
    for name, fn in (
        ("linkedin", "extractJobsFromPage"),
        ("infojobs", "extractInfojobsCards"),
    ):
        source = (EXTENSION / "portals" / f"{name}.js").read_text(encoding="utf-8")
        start = source.index(f"function {fn}(")
        end = source.index("\n}\n", start)
        body = source[start:end]
        assert "import(" not in body and "require(" not in body
        assert "chrome." not in body


@requires_node
def test_detail_extractors_via_node_harness() -> None:
    """portal-detail-text D1: Indeed/Tecnoempleo/JobFluent/Manfred read the
    offer description in the owner's tab (synthetic, sanitized pages)."""
    result = subprocess.run(
        ["node", str(HARNESS.with_name("extension_detail_harness.js"))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
