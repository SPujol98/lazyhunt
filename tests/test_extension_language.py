"""Interface language selector of the extension (bot-i18n T9, S9).

Behaviour runs in Node (``extension_pages_harness.js`` area ``language``): auto
keeps the ``chrome.i18n`` behaviour, a stored ``uiLanguage`` forces a
catalogue in Options and the popup, the selector persists and re-renders, and
export/import round-trip the key. Static checks keep the new strings in both
locales. Skipped without ``node``.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_pages_harness.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
pytestmark = pytest.mark.skipif(not EXTENSION.is_dir(), reason="extension/ not present")


@needs_node
def test_language_selector_behaviour() -> None:
    result = subprocess.run(
        ["node", str(HARNESS), "language"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_selector_strings_exist_in_both_locales() -> None:
    for lang in ("en", "es"):
        messages = json.loads((EXTENSION / "_locales" / lang / "messages.json").read_text(encoding="utf-8"))
        for key in ("languageLabel", "languageAuto", "languageAutoTitle"):
            assert messages[key]["message"], (lang, key)


def test_selector_is_in_the_options_header_with_an_accessible_label() -> None:
    html = (EXTENSION / "options.html").read_text(encoding="utf-8")
    header = html.split("</header>")[0]
    assert re.search(r'id="languageSegment"[^>]*role="group"[^>]*aria-label=', header)
    assert all(f'data-lang="{lang}"' in header for lang in ("auto", "en", "es"))


def test_uilanguage_is_whitelisted_where_keys_are_listed() -> None:
    assert "'uiLanguage'" in (EXTENSION / "core" / "round.js").read_text(encoding="utf-8")
    assert "'uiLanguage'" in (EXTENSION / "options.js").read_text(encoding="utf-8")
