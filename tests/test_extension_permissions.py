"""Safe defaults, extension side (safe-defaults T2: S2 pause, S3 portal opt-in, S4 pace).

The wiring in ``background.js`` (enabled portals + browser permission gates, the
owner migration, pause, the bot's ``GET /config``, the 60 minute fresh-install
pace) runs in a Node harness over stubbed ``chrome.*``; options and popup are
checked structurally, like the other extension tests.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers import read_extension

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = (
    Path(__file__).resolve().parent / "fixtures" / "extension_permissions_harness.js"
)

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


@requires_node
def test_background_safe_defaults_via_node_harness() -> None:
    result = subprocess.run(
        ["node", str(HARNESS)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_manifest_portal_hosts_are_optional_permissions() -> None:
    manifest = json.loads(read_extension("manifest.json"))
    # Only the loopback bridge is required; Telegram is gone (extension-rewrite S4).
    assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
    assert "https://www.linkedin.com/*" in manifest["optional_host_permissions"]
    assert len(manifest["optional_host_permissions"]) == 8


def test_options_has_a_portals_section_with_permission_flow() -> None:
    html = read_extension("options.html")
    js = read_extension("options.js")
    assert 'id="portalList"' in html
    assert "chrome.permissions.request" in js
    assert "chrome.permissions.remove" in js
    assert "termsWarning(" in js
    assert "confirm(" in js
    assert "enabledPortals" in js


def test_popup_shows_the_bot_pause_and_skipped_portals() -> None:
    popup = read_extension("popup.js")
    assert "botConfig" in popup
    assert "no_permission" in popup
