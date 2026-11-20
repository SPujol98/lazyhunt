"""onboarding-v2 T2 (S4, S6, S7): the questionnaire's answers in ``preferences.toml``.

``[search]`` holds work modes, level and minimum salary; ``[location]`` gains
the home city and country next to ``home_region``. Every key is optional and
its default keeps today's behaviour (every work mode accepted, no level, no
minimum salary, no city/country).
"""

from pathlib import Path

import pytest

from cva.profile_identity import ProfileError
from cva.profile_prefs import (
    DEFAULT_PREFS,
    LocationPrefs,
    SearchPrefs,
    load_preferences,
)
from tests.demo import DEMO_DIR


def _load(tmp_path: Path, body: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "preferences.toml"
    path.write_text(body, encoding="utf-8")
    return load_preferences(path)


def test_defaults_keep_todays_behaviour() -> None:
    assert DEFAULT_PREFS.search == SearchPrefs(
        work_modes=("remote", "hybrid", "onsite"),
        level=None,
        min_salary=None,
        currency="EUR",
    )
    assert DEFAULT_PREFS.location == LocationPrefs(city=None, country=None)


def test_demo_profile_declares_none_of_them() -> None:
    prefs = load_preferences(DEMO_DIR / "preferences.toml")
    assert prefs.search == DEFAULT_PREFS.search
    assert prefs.location == DEFAULT_PREFS.location


def test_search_keys_are_read(tmp_path: Path) -> None:
    prefs = _load(
        tmp_path,
        '[search]\nwork_modes = ["Remote", "hybrid", "remote"]\nlevel = "Mid"\n'
        'min_salary = 32000\ncurrency = "usd"\n',
    )
    assert prefs.search == SearchPrefs(
        work_modes=("remote", "hybrid"),
        level="mid",
        min_salary=32000,
        currency="USD",
    )


def test_each_search_key_overrides_only_itself(tmp_path: Path) -> None:
    prefs = _load(tmp_path, "[search]\nmin_salary = 0\n")
    assert prefs.search == SearchPrefs(min_salary=0)


def test_city_and_country_live_next_to_home_region(tmp_path: Path) -> None:
    prefs = _load(
        tmp_path,
        '[location]\nhome_region = "catalonia"\ncity = " Valencia "\n'
        'country = "España"\n',
    )
    assert prefs.location == LocationPrefs(city="Valencia", country="España")
    assert prefs.gates.home_region == "catalonia"


@pytest.mark.parametrize(
    "body",
    [
        "[search]\nbogus = 1\n",
        '[search]\nwork_modes = "remote"\n',
        "[search]\nwork_modes = []\n",
        '[search]\nwork_modes = ["remote", "office"]\n',
        '[search]\nlevel = "lead"\n',
        "[search]\nlevel = 2\n",
        "[search]\nmin_salary = -1\n",
        "[search]\nmin_salary = true\n",
        "[search]\nmin_salary = 30000.5\n",
        '[search]\nmin_salary = "30000"\n',
        '[search]\ncurrency = "euro"\n',
        '[search]\ncurrency = "E1R"\n',
        "[search]\ncurrency = 978\n",
        '[location]\ncity = ""\n',
        "[location]\ncountry = 34\n",
        "[location]\nradius_km = 30\n",
        "[search]\n[search.extra]\nx = 1\n",
        'search = "remote"\n',
    ],
)
def test_invalid_search_preferences_fail_loudly(tmp_path: Path, body: str) -> None:
    with pytest.raises(ProfileError):
        _load(tmp_path, body)
