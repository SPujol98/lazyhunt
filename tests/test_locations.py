"""Location data: Catalonia vs. outside, folded whole-word matching."""

import pytest

from cva.match.locations import (
    CATALONIA,
    OUTSIDE,
    STRUCTURED_ONLY,
    find_places,
    fold_place,
)


def _names(text: str, *, structured: bool = False) -> list[tuple[str, bool]]:
    return [
        (h.name, h.inside) for h in find_places(fold_place(text), structured=structured)
    ]


def test_fold_place_keeps_length_and_drops_accents_and_punctuation() -> None:
    text = "L'Hospitalet de Llobregat, Cornellà"
    folded = fold_place(text)
    assert len(folded) == len(text)
    assert folded == "l hospitalet de llobregat, cornella"


def test_sets_are_disjoint_and_broad() -> None:
    assert not CATALONIA & OUTSIDE
    assert len(CATALONIA) >= 300  # 42 comarcas + 4 provinces + >200 municipalities
    assert len(OUTSIDE) >= 250
    assert STRUCTURED_ONLY <= CATALONIA | OUTSIDE


@pytest.mark.parametrize(
    "name",
    [
        "Sant Andreu de la Barca",
        "Sant Cugat del Vallès",
        "San Cugat",
        "Sabadell",
        "Terrassa",
        "L'Hospitalet de Llobregat",
        "Hospitalet",
        "Badalona",
        "Mataró",
        "Granollers",
        "Rubí",
        "Cornellà de Llobregat",
        "Sant Boi de Llobregat",
        "Castelldefels",
        "El Prat de Llobregat",
        "Viladecans",
        "Martorell",
        "Vilanova i la Geltrú",
        "Igualada",
        "Manresa",
        "Vic",
        "Reus",
        "Figueres",
        "Blanes",
        "Girona",
        "Gerona",
        "Lleida",
        "Lérida",
        "Tarragona",
        "Cataluña",
        "Catalunya",
        "Baix Llobregat",
        "Vallès Occidental",
        "Maresme",
        "Osona",
        "Priorat",
        "Val d'Aran",
    ],
)
def test_catalan_samples_are_inside(name: str) -> None:
    hits = _names(f"Oficina en {name} (puesto)")
    assert hits and all(inside for _n, inside in hits), hits


@pytest.mark.parametrize(
    "name",
    [
        "Madrid",
        "Valencia",
        "Sevilla",
        "Málaga",
        "Bilbao",
        "Zaragoza",
        "Alicante",
        "Murcia",
        "Palma de Mallorca",
        "Las Palmas",
        "Valladolid",
        "Vigo",
        "Gijón",
        "A Coruña",
        "San Sebastián",
        "Donostia",
        "Pamplona",
        "Comunidad de Madrid",
        "País Vasco",
        "Andalucía",
        "Galicia",
        "London",
        "Paris",
        "Berlin",
        "Lisboa",
        "Amsterdam",
        "Dublin",
        "Munich",
        "Milano",
        "Portugal",
        "United Kingdom",
        "Germany",
    ],
)
def test_outside_samples_are_outside(name: str) -> None:
    hits = _names(f"Oficina en {name} (puesto)")
    assert hits and not any(inside for _n, inside in hits), hits


def test_longest_name_wins() -> None:
    assert _names("Sant Cugat del Vallès") == [("sant cugat del valles", True)]
    # Spain's Castellon is outside; Catalan Castello d'Empuries stays inside.
    assert _names("Castellón") == [("castellon", False)]
    assert _names("Castelló d'Empúries") == [("castello d empuries", True)]
    assert _names("San Sebastián de los Reyes") == [
        ("san sebastian de los reyes", False)
    ]


def test_whole_word_and_whitespace_tolerant() -> None:
    assert _names("Sant   Cugat\ndel Vallès") == [("sant cugat del valles", True)]
    assert _names("Madridejos") == []  # not a listed word
    assert _names("anti-Madrid") == [("madrid", False)]


def test_ambiguous_names_need_the_structured_field() -> None:
    assert _names("add some salt and sort the list") == []
    assert _names("Girona, Salt", structured=True) == [
        ("girona", True),
        ("salt", True),
    ]
    assert _names("Leon Smith joins Granada Inc.") == []
    assert _names("León", structured=True) == [("leon", False)]
    assert _names("Banco Santander") == []
