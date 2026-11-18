"""Profile-driven searches (portal-adapters T2).

``extension/searches.js`` expands the stored ``searches`` list into auto
beacons through each portal adapter's ``buildSearchUrl``. The URL builders and
the expansion run in a Node harness; the background wiring (auto beacons are
scanned like manual ones, optional title filter) runs in the background
harness; options and storage keys are checked structurally.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_searches_harness.js"

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_search_urls_and_expansion_via_node_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def _read(name: str) -> str:
    return (EXTENSION / name).read_text(encoding="utf-8")


def test_manifest_keeps_the_baseline_host_permissions() -> None:
    """Searches added no permission; new portals may append theirs (add-portal)."""
    import json

    manifest = json.loads(_read("manifest.json"))
    # Portal hosts are optional permissions (safe-defaults S3); the loopback
    # bridge stays required; Telegram is gone (extension-rewrite S4).
    hosts = manifest["host_permissions"] + manifest["optional_host_permissions"]
    assert "https://api.telegram.org/*" not in hosts
    for baseline in (
        "https://www.linkedin.com/*",
        "http://127.0.0.1/*",
        "https://www.infojobs.net/*",
    ):
        assert baseline in hosts
    assert not any(h in ("<all_urls>", "*://*/*", "https://*/*") for h in hosts)


def test_search_slug_has_one_shared_helper() -> None:
    searches = _read("searches.js")
    infojobs = (EXTENSION / "portals" / "infojobs.js").read_text(encoding="utf-8")
    slug = _read("slug.js")
    assert "export function slugify" in slug
    assert "\\u0300-\\u036f" in slug
    assert "from './slug.js'" in searches
    assert "from '../slug.js'" in infojobs
    assert "normalize('NFD')" not in searches
    assert "normalize('NFD')" not in infojobs
