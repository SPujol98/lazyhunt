"""Role families reach the extension (role-families T6, S3/S5).

The bot serves the profile's family list on the channel that already carries
the profile's title filters (GET /searches/suggested, ``families``); the
extension builds the "Filter profile" selects and the title-filter segment
from it, keeps a declared family id through save/load/export/import and the
round, and degrades an unknown id to the default family. The node areas run
the existing harnesses (pages, characterization worker and pages).
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.config import Settings
from tests.demo import DEMO_CLAIMS
from tests.service_stub import service_stub

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "s3cret-bridge"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)
needs_extension = pytest.mark.skipif(
    not (REPO_ROOT / "extension").is_dir(), reason="extension/ not present"
)

FAMILIES_TOML = """
[families.ai]
default = true
[families.backend]
[families.frontend]
label = "Frontend"
title_terms = ["React"]
[families.frontend.title_filter]
include_groups = [["React", "Vue"]]
exclude_terms = ["Senior"]
"""


@pytest.fixture()
def workdir(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CVA_AUTO_ANALYZE", "false")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk")
    monkeypatch.setenv("CVA_OWNER_TELEGRAM_ID", "123456")
    monkeypatch.setenv("CVA_BRIDGE_SECRET", SECRET)
    monkeypatch.setenv("CVA_BRIDGE_PORT", "0")
    return tmp_path


def _suggested(
    workdir: Any,
    clean_settings: Callable[..., Settings],
    preferences: str,
    path: str = "/searches/suggested",
) -> dict[str, Any]:
    profile = workdir / "profile"
    profile.mkdir()
    shutil.copyfile(DEMO_CLAIMS, profile / "claims.json")
    (profile / "preferences.toml").write_text(preferences, encoding="utf-8")
    app = SimpleNamespace(post_init=None, bot=None, bot_data={})
    handle = bridge.register(
        app, clean_settings(profile_dir=profile), service_stub(65, profile)
    )
    assert handle is not None
    try:
        url = f"http://127.0.0.1:{handle.port}{path}"
        resp = httpx.get(url, headers={"X-CVA-Bridge-Secret": SECRET})
    finally:
        handle.stop()
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    return body


def test_bridge_serves_the_declared_families_in_order(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    body = _suggested(workdir, clean_settings, FAMILIES_TOML)
    families = body["families"]
    assert [(f["id"], f["label"], f["default"]) for f in families] == [
        ("ai", "AI", True),
        ("backend", "Backend", False),
        ("frontend", "Frontend", False),
    ]
    frontend = families[2]["titleFilter"]
    assert frontend["enabled"] is True
    assert frontend["includeGroups"] == [["React", "Vue"]]
    assert frontend["excludeTerms"][0] == "Senior"
    assert frontend["excludeTerms"][-1] == "BJAK"  # the company blocklist
    assert set(families[0]["titleFilter"]) == {
        "enabled",
        "includeGroups",
        "excludeTerms",
    }


def test_bridge_without_declared_families_sends_no_list(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """The built-in two are the extension's own fallback: the body is unchanged."""
    body = _suggested(workdir, clean_settings, "")
    assert set(body) == {"searches"}


def test_config_carries_the_declared_families(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    """GET /config, read by every round, keeps the family list in sync."""
    body = _suggested(workdir, clean_settings, FAMILIES_TOML, "/config")
    assert [f["id"] for f in body["families"]] == ["ai", "backend", "frontend"]
    assert body["families"][2]["titleFilter"]["includeGroups"] == [["React", "Vue"]]
    assert {"paused", "enabledPortals"} <= set(body)


def test_config_without_declared_families_is_unchanged(
    workdir: Any, clean_settings: Callable[..., Settings]
) -> None:
    body = _suggested(workdir, clean_settings, "", "/config")
    assert set(body) == {"paused", "enabledPortals"}


def test_families_payload_marks_the_declared_default() -> None:
    from cva.families import BUILTIN_REGISTRY, Family, FamilyRegistry

    assert bridge.families_payload(BUILTIN_REGISTRY) is None
    nursing = Family(id="nursing", label="Nursing", languages=("es",))
    payload = bridge.families_payload(
        FamilyRegistry((*BUILTIN_REGISTRY.families, nursing), "nursing")
    )
    assert payload is not None
    assert [(f["id"], f["default"]) for f in payload] == [
        ("ai", False),
        ("backend", False),
        ("nursing", True),
    ]


def _node(harness: str, area: str) -> None:
    result = subprocess.run(
        ["node", str(FIXTURES / harness), area],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@needs_node
@needs_extension
def test_options_page_builds_selects_and_filters_from_the_family_list() -> None:
    """Select/segment from a 3-family list; a frontend search and its filter
    survive save/load; old storage saves unchanged; suggest adopts the list."""
    _node("extension_pages_harness.js", "families")


@needs_node
@needs_extension
def test_export_and_import_keep_or_degrade_the_family() -> None:
    """Export keeps frontend; import keeps it where declared, degrades it to the
    default elsewhere; a v1 export of the previous version imports unchanged."""
    _node("extension_characterization_harness_pages.js", "families")


@needs_node
@needs_extension
def test_round_sends_the_family_as_the_payload_profile() -> None:
    _node("extension_characterization_harness.js", "families")
