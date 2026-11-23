"""Redesigned popup and options pages (extension-rewrite T4, S5).

Behaviour runs in Node (``tests/fixtures/extension_pages_harness.js``) over the
pages' own HTML and the shared fake browser: portal opt-in, Save validation,
the interval rule (D2), suggestions, clear history, test scrape, the popup's
beacon list (D4) and the i18n fill. Static checks here keep the locale files
complete: ``en`` and ``es`` have the same keys, and every key the pages use
exists. Skipped without ``node``, like the other extension tests.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from tests.helpers import read_extension, run_node_harness

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
LOCALES = EXTENSION / "_locales"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_pages_harness.js"
PAGES = {"popup.js": "popup.html", "options.js": "options.html"}

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)
pytestmark = pytest.mark.skipif(not EXTENSION.is_dir(), reason="extension/ not present")


def _messages(lang: str) -> dict[str, dict]:
    data: dict[str, dict] = json.loads(
        (LOCALES / lang / "messages.json").read_text(encoding="utf-8")
    )
    return data


@needs_node
@pytest.mark.parametrize(
    "area",
    [
        "i18n",
        "portals",
        "save",
        "interval",
        "suggest",
        "maintenance",
        "popup",
        "nobridge",
        "pair",  # beta3-ux T1
        "searchguide",  # beta3-ux T2
        "bridge",  # beta4-polish S4
    ],
)
def test_pages_behaviour(area: str) -> None:
    run_node_harness(HARNESS, area)


def test_locales_have_the_same_keys_and_placeholders() -> None:
    en, es = _messages("en"), _messages("es")
    assert en, "empty English catalogue"
    assert set(en) == set(es)
    for key, entry in en.items():
        assert re.fullmatch(r"[A-Za-z0-9_]+", key), key
        for lang, item in (("en", entry), ("es", es[key])):
            assert isinstance(item.get("message"), str) and item["message"], (lang, key)
            used = {
                m.lower() for m in re.findall(r"\$([A-Za-z0-9_@]+)\$", item["message"])
            }
            declared = set(item.get("placeholders", {}))
            assert used == declared, (lang, key, used, declared)
        assert set(entry.get("placeholders", {})) == set(
            es[key].get("placeholders", {})
        ), key


def test_every_key_the_pages_use_exists() -> None:
    en = _messages("en")
    used: set[str] = set()
    for script, html in PAGES.items():
        used |= set(re.findall(r"\btr\('(\w+)'", read_extension(script)))
        used |= set(re.findall(r'data-i18n(?:-[\w-]+)?="(\w+)"', read_extension(html)))
    assert len(used) > 50
    assert sorted(used - set(en)) == []


def test_every_id_the_scripts_use_is_in_their_page() -> None:
    for script, html in PAGES.items():
        ids = set(re.findall(r"\$\('([\w-]+)'\)", read_extension(script)))
        present = set(re.findall(r'\bid="([\w-]+)"', read_extension(html)))
        assert ids, script
        assert sorted(ids - present) == [], script


def test_pages_load_nothing_remote_and_have_no_telegram() -> None:
    names = [
        "popup.html",
        "popup.js",
        "popup.css",
        "options.html",
        "options.js",
        "options.css",
        "shared.css",
    ]
    names += [f"_locales/{lang}/messages.json" for lang in ("en", "es")]
    for name in names:
        text = read_extension(name)
        assert "telegram" not in text.lower(), name
        assert not re.search(
            r"googleapis|gstatic|@import|url\(\s*['\"]?https?:", text
        ), name
        assert not re.search(r'(?:src|href)="https?:', text), name
