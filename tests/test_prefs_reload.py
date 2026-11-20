"""onboarding-v2 T2: one live, reloadable ``ProfilePrefs`` owned by the service.

The service loads ``preferences.toml`` once and re-reads it on
``reload_preferences()``; the bridge reads the service's instance on every
request (``/config``, ``/searches/suggested``, beacon family routing), so an
edited file reaches the extension without a restart. An invalid edit keeps the
previous instance and raises.
"""

import asyncio
import shutil
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from cva import bridge
from cva.claimsdb import load_claims
from cva.config import Settings
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.profile_identity import ProfileError
from cva.profile_paths import resolve_profile_paths
from cva.profile_prefs import load_preferences
from cva.service import JobService
from cva.tracker import OfferRepository
from tests.demo import DEMO_DIR

SECRET = "s3cret-reload"

_FAMILIES = (
    '[families.data]\nlabel = "Data"\ntitle_terms = ["Data"]\n'
    'search_roles = ["Data Engineer"]\n'
)


class _Bot:
    async def send_message(self, *args: Any, **kwargs: Any) -> bool:
        return True


@pytest.fixture()
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CVA_AUTO_ANALYZE", "false")
    monkeypatch.setenv("CVA_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("CVA_LLM_API_KEY", "sk")
    monkeypatch.setenv("CVA_OWNER_TELEGRAM_ID", "123")
    monkeypatch.setenv("CVA_BRIDGE_SECRET", SECRET)
    monkeypatch.setenv("CVA_BRIDGE_PORT", "0")
    return tmp_path


def _profile(root: Path) -> Path:
    profile = root / "profile"
    shutil.copytree(DEMO_DIR, profile)
    return profile


def _service(root: Path, profile: Path) -> JobService:
    conn = sqlite3.connect(root / "app.db")
    db = load_claims(profile / "claims.json")

    class _Llm:
        pass

    return JobService(
        repo=OfferRepository(conn),
        llm=_Llm(),  # type: ignore[arg-type]
        fetcher=None,  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(profile / "synonyms.json", db),
        cache=ExtractionCache(conn),
        profile_paths=resolve_profile_paths(profile),
    )


def _get(port: int, path: str) -> dict[str, Any]:
    resp = httpx.get(
        f"http://127.0.0.1:{port}{path}", headers={"X-CVA-Bridge-Secret": SECRET}
    )
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    return body


# --- service -----------------------------------------------------------------


def test_reload_preferences_reads_the_edited_file(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    service = _service(tmp_path, profile)
    assert service.preferences.search_targets.locations == ("Barcelona", "remoto")

    prefs_file = profile / "preferences.toml"
    prefs_file.write_text(
        prefs_file.read_text(encoding="utf-8").replace(
            'locations = ["Barcelona", "remoto"]', 'locations = ["Madrid"]'
        ),
        encoding="utf-8",
    )
    returned = service.reload_preferences()

    assert service.preferences.search_targets.locations == ("Madrid",)
    assert service.loaded_preferences == load_preferences(prefs_file)
    assert returned is service.preferences


def test_reload_preferences_cuts_families_to_the_manifest_again(
    tmp_path: Path,
) -> None:
    """The reloaded instance keeps the delivery view: each family's languages
    cut to its base CVs (``available_registry``), exactly as at start-up."""
    profile = _profile(tmp_path)
    service = _service(tmp_path, profile)
    before = service.preferences

    service.reload_preferences()

    assert service.preferences == before


def test_invalid_reload_keeps_the_previous_preferences(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    service = _service(tmp_path, profile)
    before = service.preferences
    loaded_before = service.loaded_preferences

    (profile / "preferences.toml").write_text("[nope]\nx = 1\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="unknown table"):
        service.reload_preferences()

    assert service.preferences is before
    assert service.loaded_preferences is loaded_before


# --- bridge reads through the service ----------------------------------------


def test_bridge_serves_the_reloaded_preferences_without_restart(
    workdir: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = _profile(workdir)
    service = _service(workdir, profile)
    app = SimpleNamespace(post_init=None, bot=None, bot_data={})
    handle = bridge.register(app, clean_settings(profile_dir=profile), service)
    assert handle is not None
    try:
        before = _get(handle.port, "/searches/suggested")
        config_before = _get(handle.port, "/config")
        prefs_file = profile / "preferences.toml"
        prefs_file.write_text(
            prefs_file.read_text(encoding="utf-8") + _FAMILIES, encoding="utf-8"
        )
        service.reload_preferences()
        after = _get(handle.port, "/searches/suggested")
        config_after = _get(handle.port, "/config")
    finally:
        handle.stop()

    assert "families" not in before
    assert "families" not in config_before
    assert [f["id"] for f in after["families"]] == ["data"]
    assert [f["id"] for f in config_after["families"]] == ["data"]
    assert "Data Engineer" in {s["keywords"] for s in after["searches"]}


def test_bridge_routes_beacon_offers_with_the_reloaded_families(
    workdir: Path, clean_settings: Callable[..., Settings]
) -> None:
    """Beacon family routing reads the live registry: a profile name the
    reloaded families declare is kept instead of being normalised away."""
    profile = _profile(workdir)
    service = _service(workdir, profile)
    app = SimpleNamespace(post_init=None, bot=_Bot(), bot_data={})
    handle = bridge.register(app, clean_settings(profile_dir=profile), service)
    assert handle is not None
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(app.post_init(app), loop).result(timeout=5)
    try:
        prefs_file = profile / "preferences.toml"
        prefs_file.write_text(
            prefs_file.read_text(encoding="utf-8") + _FAMILIES, encoding="utf-8"
        )
        service.reload_preferences()
        resp = httpx.post(
            f"http://127.0.0.1:{handle.port}/bridge",
            json={
                "beacon": "Data BCN",
                "profile": "data",
                "jobs": [
                    {
                        "title": "Data Engineer",
                        "company": "Acme",
                        "location": "BCN",
                        "url": "https://www.linkedin.com/jobs/view/4200000001/",
                    }
                ],
            },
            headers={"X-CVA-Bridge-Secret": SECRET},
        )
    finally:
        asyncio.run_coroutine_threadsafe(handle.shutdown(), loop).result(timeout=5)
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
    assert resp.status_code == 200
    conn = sqlite3.connect(workdir / "data" / "app.db")
    try:
        (perfil,) = conn.execute("SELECT perfil FROM offers").fetchone()
    finally:
        conn.close()
    assert perfil == "data"
