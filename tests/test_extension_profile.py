"""The extension's Profile tab (onboarding-v2 T10, S9/S10).

``extension/profile_editor.js`` turns the bot's ``GET /profile`` answer into
an editable draft and each section back into a ``POST /profile`` body. Its
pure helpers run in a Node harness; the page/bot contract is checked against
the Python side (same sections, fields and error codes) and end to end: the
bodies the page sends for an unedited profile are accepted by a real bridge
and change nothing. Locale keys and the page structure are checked statically.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cva.onboarding import profile_edit
from tests.test_profile_endpoint import (  # noqa: F401 — ``live`` is a fixture
    BACKGROUND,
    _get,
    _hashes,
    _post,
    live,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION = REPO_ROOT / "extension"
HARNESS = Path(__file__).resolve().parent / "fixtures" / "extension_profile_harness.js"

pytestmark = pytest.mark.skipif(
    not EXTENSION.is_dir(), reason="extension/ snapshot not present"
)
requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _node(mode: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["node", str(HARNESS), mode],
        cwd=REPO_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout


def _messages(lang: str) -> dict[str, dict[str, str]]:
    path = EXTENSION / "_locales" / lang / "messages.json"
    data: dict[str, dict[str, str]] = json.loads(path.read_text("utf-8"))
    return data


@requires_node
def test_profile_editor_helpers_via_node_harness() -> None:
    assert "ok" in _node("helpers")


@requires_node
def test_the_page_and_the_bot_agree_on_sections_fields_and_error_codes() -> None:
    contract = json.loads(_node("contract"))
    assert contract["sections"] == {
        k: list(v) for k, v in profile_edit.SECTION_FIELDS.items()
    }
    assert contract["errors"] == list(profile_edit.ERROR_CODES)


@requires_node
def test_an_unedited_profile_posts_back_without_changing_a_byte(
    live: SimpleNamespace,  # noqa: F811 — the imported fixture
) -> None:
    # Give every section a value first (the demo profile has none for some;
    # its allowed_languages are not in the questionnaire's order, which a
    # save normalizes exactly like /setup offer_langs).
    for section, values in (
        ("offer_langs", {"offer_langs": ["es", "en", "ca"]}),
        ("level", {"level": "mid"}),
        ("background", BACKGROUND),
        (
            "location",
            {"work_modes": ["remote"], "work_city": "Valencia", "country": "Spain"},
        ),
        ("salary", {"min_salary": 30000, "currency": "EUR"}),
        ("positions", {"families": ["ai"], "kept_families": [], "custom_titles": []}),
    ):
        assert _post(live, {"section": section, "values": values}).status_code == 200
    profile = _get(live).text
    before = {
        rel: digest
        for rel, digest in _hashes(live.profile).items()
        if rel != "onboarding_answers.json"  # records the last section saved
    }
    bodies = json.loads(_node("roundtrip", profile))
    assert [b["section"] for b in bodies] == list(profile_edit.EDITABLE_SECTIONS)
    for body in bodies:
        resp = _post(live, body)
        assert resp.status_code == 200, (body, resp.text)
    after = _hashes(live.profile)
    assert {rel: after[rel] for rel in before} == before


def test_every_profile_text_exists_in_both_locales() -> None:
    used: set[str] = set()
    for name in ("profile_editor.js", "options.js", "options.html"):
        text = (EXTENSION / name).read_text("utf-8")
        used |= set(re.findall(r"\btr\('(profile\w+)'", text))
        used |= set(re.findall(r"\['(profile\w+)', '", text))
        used |= set(re.findall(r'data-i18n="(profile\w+)"', text))
    assert len(used) > 40
    for lang in ("en", "es"):
        assert sorted(used - set(_messages(lang))) == [], lang


def test_the_options_page_has_the_profile_tab() -> None:
    html = (EXTENSION / "options.html").read_text("utf-8")
    script = (EXTENSION / "options.js").read_text("utf-8")
    assert 'href="#profile"' in html
    assert 'id="profile"' in html
    assert "from './profile_editor.js'" in script
    editor = (EXTENSION / "profile_editor.js").read_text("utf-8")
    # The page never writes claims.json or any file: only the bot does.
    assert "claims.json" not in re.sub(r"tr\([^)]*\)|//.*", "", editor)
