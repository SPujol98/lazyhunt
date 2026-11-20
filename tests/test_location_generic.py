"""onboarding-v2 T3 (S6): generic location gate by city/country NAME.

"Filtro genérico por ciudad": outside Catalonia any city or country is matched
by name (no distance); Catalonia keeps its detailed gate. Name matching cannot
tell a suburb from another city, so the generic gate never FAILs: an offer
whose work place is elsewhere is FLAGged (a warning on the card) instead.
"""

import asyncio
from pathlib import Path

import pytest

from cva.match.gates import DEFAULT_GATE_PREFS, GatePrefs, gate_reason, location_gate
from tests.test_card_warnings import _offer, _service

MADRID = GatePrefs(city="Madrid", country="España")
LISBOA = GatePrefs(city="Lisboa", country="Portugal")
SPAIN_ONLY = GatePrefs(country="España")


@pytest.mark.parametrize(
    ("text", "location"),
    [
        ("Puesto presencial. Python.", "Madrid, España"),
        ("Puesto presencial. Python.", "Madrid, Spain"),
        ("Puesto presencial. Python.", "MADRID"),
        ("Modalidad híbrida en nuestra oficina de Madrid. Python.", None),
        ("Python engineer. Hybrid.", "Comunidad de Madrid"),
        ("Python engineer.", "Remote - Spain"),
        ("Trabajo 100% remoto. Python.", "Barcelona, Spain"),
        ("Backend developer. Python.", None),  # silence passes
    ],
)
def test_offer_in_the_home_city_or_remote_passes(
    text: str, location: str | None
) -> None:
    assert location_gate(text, location, MADRID).verdict == "PASS"


@pytest.mark.parametrize(
    ("text", "location", "places"),
    [
        ("Puesto presencial. Python.", "Barcelona, Spain", "Barcelona"),
        ("Modalidad híbrida. Python.", "Valencia", "Valencia"),
        ("Backend developer.", "Sevilla, España", "Sevilla"),
        ("Hybrid, 3 days in the office.", "Berlin, Germany", "Berlin, Germany"),
        ("Trabajo presencial. Ubicación: Bilbao.", None, "Bilbao"),
    ],
)
def test_another_city_or_country_is_flagged_never_failed(
    text: str, location: str | None, places: str
) -> None:
    result = location_gate(text, location, MADRID)
    assert result.verdict == "FLAG"
    assert result.reason.endswith(f"fuera de Madrid ({places}): revisa")


def test_flag_reason_is_translated_with_the_city_kept() -> None:
    result = location_gate("Puesto presencial.", "Barcelona, Spain", MADRID)
    assert result.reason == "Presencial fuera de Madrid (Barcelona): revisa"
    assert gate_reason(result.reason, "en") == (
        "On-site outside Madrid (Barcelona): check"
    )


def test_city_names_are_folded_and_any_country_works() -> None:
    accented = GatePrefs(city="málaga")
    assert location_gate("Presencial.", "Malaga, Spain", accented).verdict == "PASS"
    assert location_gate("Presencial.", "Lisboa, Portugal", LISBOA).verdict == "PASS"
    porto = location_gate("Presencial.", "Porto, Portugal", LISBOA)
    assert porto.verdict == "FLAG"
    assert "fuera de Lisboa (Porto)" in porto.reason


def test_onsite_in_the_home_country_without_a_city_is_flagged() -> None:
    result = location_gate("Puesto presencial.", "España", MADRID)
    assert result.verdict == "FLAG"
    assert result.reason == "Presencial sin ubicación concreta: revisa"


def test_country_only_profile_passes_its_country_and_flags_others() -> None:
    assert location_gate("Presencial.", "Sevilla", SPAIN_ONLY).verdict == "PASS"
    assert location_gate("Presencial.", "Barcelona", SPAIN_ONLY).verdict == "PASS"
    assert location_gate("Presencial.", "Spain", SPAIN_ONLY).verdict == "PASS"
    abroad = location_gate("Presencial.", "London, United Kingdom", SPAIN_ONLY)
    assert abroad.verdict == "FLAG"
    assert "fuera de España" in abroad.reason


def test_remote_with_visits_to_another_city_keeps_the_remote_flag() -> None:
    text = "Trabajo 100% remoto. Visitas mensuales a la oficina de Bilbao."
    result = location_gate(text, None, MADRID)
    assert result.verdict == "FLAG"
    assert "fuera de Madrid (Bilbao)" in result.reason


# --- Catalonia keeps its detailed gate ---------------------------------------------


@pytest.mark.parametrize(
    "prefs",
    [
        DEFAULT_GATE_PREFS,
        GatePrefs(city="Barcelona"),
        GatePrefs(city="Sant Cugat del Vallès", country="España"),
    ],
)
def test_catalan_home_city_keeps_the_detailed_fail(prefs: GatePrefs) -> None:
    result = location_gate("Puesto presencial. Python.", "Madrid", prefs)
    assert result.verdict == "FAIL"
    assert result.reason == "Presencial fuera de Cataluña (Madrid)"
    assert location_gate("Híbrido.", "Girona", prefs).verdict == "PASS"


# --- the service reads [location] city/country live --------------------------------


def test_analyze_uses_the_profile_city(tmp_path: Path) -> None:
    text = "AI Engineer en Acme. Puesto presencial. Python."
    madrid, madrid_repo = _service(tmp_path / "madrid", '[location]\ncity = "Madrid"\n')
    plain, plain_repo = _service(tmp_path / "plain", None)
    got = asyncio.run(madrid.analyze(_offer(madrid_repo, text, "Barcelona")))
    old = asyncio.run(plain.analyze(_offer(plain_repo, text, "Barcelona")))
    assert [g.verdict for g in got.gates if g.gate == "location"] == ["FLAG"]
    assert [g.verdict for g in old.gates if g.gate == "location"] == ["PASS"]
