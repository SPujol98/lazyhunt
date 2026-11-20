"""Suggested searches follow the ``/setup`` answers (onboarding-v2 T6, S3/S7).

``GET /searches/suggested`` builds its searches from the selected families'
``search_roles`` x the search locations. Without a hand-written
``[search_targets] locations`` those locations are derived when the profile is
read: the city when on-site or hybrid work is accepted, plus a remote search
("remoto" when Spanish or Catalan offers are accepted, else "remote") when
remote work is. A hand-written ``locations`` always wins. The full ``/setup``
reply ends with the next step: the extension's "Suggest from my profile".
"""

import tomllib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import bot
from cva import bridge
from cva.config import Settings
from cva.i18n import Lang, t
from cva.onboarding import flow
from cva.profile_prefs import ProfileError, preferences_from_data
from cva.service import JobService
from tests.test_onboarding import (
    DEMO_PROFILE,
    Driver,
    _answer_everything,
    _full_app,
    _setup_mode,
)
from tests.test_onboarding_sections import _start

SECRET = "s3cret-searches"


def _suggested(
    profile: Path, tmp_path: Path, clean_settings: Callable[..., Settings]
) -> dict[str, Any]:
    """``GET /searches/suggested`` of a bot started on ``profile``."""
    settings = clean_settings(
        profile_dir=str(profile),
        owner_telegram_id=4242,
        CVA_BRIDGE_SECRET=SECRET,
        bridge_port=0,
        enabled_portals=["linkedin", "infojobs"],
    )
    service = JobService.from_settings(settings, db_path=tmp_path / "bridge.db")
    app = SimpleNamespace(post_init=None, post_shutdown=None, bot=None, bot_data={})
    handle = bridge.register(app, settings, service)
    assert handle is not None
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{handle.port}/searches/suggested",
            headers={"X-CVA-Bridge-Secret": SECRET},
            timeout=10,
        )
    finally:
        handle.stop()
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    return body


def _pairs(body: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (s["filterProfile"], s["keywords"], s["location"]) for s in body["searches"]
    }


# --- rule: searches from the answers of a first /setup ----------------------------


def test_first_setup_suggests_the_families_roles_in_the_city_and_remote(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    d, profile, _ = _setup_mode(tmp_path)
    d.context.bot_data[bot._SETUP_SETTINGS_KEY] = clean_settings()
    d.start()
    # Families AI + "Data Analyst, BI Developer"; remote + hybrid; Valencia;
    # offers in Spanish and English.
    _answer_everything(d, "en")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    # Nothing is written for the searches: they are derived when read.
    raw = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    assert "search_targets" not in raw

    body = _suggested(profile, tmp_path, clean_settings)
    pairs = _pairs(body)
    assert {loc for _, _, loc in pairs} == {"Valencia", "remoto"}
    assert ("ai", "AI Engineer", "Valencia") in pairs
    assert ("ai", "AI Engineer", "remoto") in pairs
    assert ("data_analyst", "Data Analyst", "Valencia") in pairs
    assert ("data_analyst", "Data Analyst", "remoto") in pairs
    assert ("data_analyst", "BI Developer", "Valencia") in pairs
    assert not any("Barcelona" in loc for _, _, loc in pairs)
    assert {tuple(s["portals"]) for s in body["searches"]} == {("linkedin", "infojobs")}


def _location_section(d: Driver, modes: tuple[str, ...], city: str) -> None:
    _start(d, "/setup location")
    assert d.step == "work_modes"
    for mode in ("remote", "hybrid", "onsite"):
        chosen = d.context.user_data[flow.STATE_KEY]["a"].get("work_modes") or []
        if (mode in modes) != (mode in chosen):
            d.press(f"t:{mode}")
    d.press("done")
    d.text(city)
    d.text("España")
    assert d.step == "summary"
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))


def _without_search_targets(profile: Path) -> None:
    prefs = profile / "preferences.toml"
    data = prefs.read_text("utf-8")
    head, _, rest = data.partition("[search_targets]")
    rest = rest[rest.index("\n[") :] if "\n[" in rest else ""
    prefs.write_text(head + rest.lstrip("\n"), encoding="utf-8")
    assert "search_targets" not in tomllib.loads(prefs.read_text("utf-8"))


def test_setup_location_moves_the_suggestions_at_once(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    _without_search_targets(profile)
    _location_section(d, ("onsite",), "Sevilla")
    pairs = _pairs(_suggested(profile, tmp_path, clean_settings))
    assert {loc for _, _, loc in pairs} == {"Sevilla"}
    _location_section(d, ("remote",), "Sevilla")
    pairs = _pairs(_suggested(profile, tmp_path, clean_settings))
    assert {loc for _, _, loc in pairs} == {"remoto"}


def test_a_hand_written_locations_list_is_kept_by_setup(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The demo profile writes locations = ["Barcelona", "remoto"] by hand.
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    before = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    _location_section(d, ("onsite",), "Sevilla")
    after = tomllib.loads((profile / "preferences.toml").read_text("utf-8"))
    assert after["search_targets"] == before["search_targets"]
    pairs = _pairs(_suggested(profile, tmp_path, clean_settings))
    assert {loc for _, _, loc in pairs} == {"Barcelona", "remoto"}


# --- rule: the derivation (strict loader, every case it names) --------------------


def _locations(doc: dict[str, Any]) -> tuple[str, ...]:
    return preferences_from_data(doc).search_targets.locations


@pytest.mark.parametrize(
    ("modes", "langs", "expected"),
    [
        (["remote", "hybrid", "onsite"], ["es", "en"], ("Valencia", "remoto")),
        (["hybrid"], ["en"], ("Valencia",)),
        (["onsite"], ["es"], ("Valencia",)),
        (["remote"], ["es"], ("remoto",)),
        (["remote"], ["ca"], ("remoto",)),
        (["remote"], ["en", "fr"], ("remote",)),
        (["onsite", "remote"], ["en"], ("Valencia", "remote")),
    ],
)
def test_locations_derive_from_city_work_modes_and_offer_languages(
    modes: list[str], langs: list[str], expected: tuple[str, ...]
) -> None:
    doc = {
        "search": {"work_modes": modes},
        "location": {"city": "Valencia", "country": "España"},
        "gates": {"allowed_languages": langs},
    }
    assert _locations(doc) == expected


def test_without_a_city_on_site_searches_use_the_country() -> None:
    doc = {"search": {"work_modes": ["onsite"]}, "location": {"country": "Portugal"}}
    assert _locations(doc) == ("Portugal",)


def test_a_city_alone_accepts_every_work_mode() -> None:
    # No [search] work_modes = every mode; default offer languages include es.
    assert _locations({"location": {"city": "Bilbao"}}) == ("Bilbao", "remoto")


def test_hand_written_locations_win_over_the_answers() -> None:
    doc = {
        "search": {"work_modes": ["onsite"]},
        "location": {"city": "Valencia"},
        "search_targets": {"locations": ["Madrid"]},
    }
    assert _locations(doc) == ("Madrid",)


def test_profiles_without_answers_keep_the_engine_default() -> None:
    assert _locations({}) == ("Barcelona", "remoto")
    assert _locations({"location": {"home_region": "catalonia"}}) == (
        "Barcelona",
        "remoto",
    )
    demo = tomllib.loads((DEMO_PROFILE / "preferences.toml").read_text("utf-8"))
    assert _locations(demo) == ("Barcelona", "remoto")


def test_an_empty_hand_written_list_is_still_refused() -> None:
    with pytest.raises(ProfileError, match="locations is empty"):
        preferences_from_data({"search_targets": {"locations": []}})


# --- rule: the full /setup reply ends with the next step ---------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_full_setup_reply_ends_with_the_suggest_next_step(
    tmp_path: Path, clean_settings: Callable[..., Settings], lang: Lang
) -> None:
    d, _, _ = _setup_mode(tmp_path, lang)
    d.context.bot_data[bot._SETUP_SETTINGS_KEY] = clean_settings()
    d.start()
    _answer_everything(d, lang)
    d.press("confirm")
    assert d.replies[-1].endswith(t("onboarding.next_step", lang))


def test_a_section_rerun_has_no_next_step(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    _start(d, "/setup level")
    d.press("go:mid")
    d.press("confirm")
    reply = d.replies[-1]
    assert reply.startswith(t("onboarding.saved", "en"))
    assert t("onboarding.next_step", "en") not in reply
