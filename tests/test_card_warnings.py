"""onboarding-v2 T3 (S4): work-mode and minimum-salary warnings on the card.

"Avisar en la tarjeta": the offer still arrives, marked ⚠️ with the reason.
The warnings are computed from the stored offer and the live ``[search]``
preferences each time a card is rendered; they never discard an offer nor
change its score.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from cva.fetch import FetchResponse
from cva.match.gates import work_mode
from cva.match.warnings import offer_warnings
from cva.service import JobService
from cva.tracker import OfferInput, OfferRepository
from tests.test_service import (
    NOW,
    OFFER_URL,
    FakeFetcher,
    FakeLlmClient,
    _extraction,
    make_service,
)

ONSITE = "AI Engineer en Acme. Puesto presencial en la oficina. Python."
REMOTE = "AI Engineer en Acme. Trabajo 100% remoto. Python."
HYBRID = "AI Engineer en Acme. Modalidad híbrida, 2 días en oficina. Python."
PERK = (
    "AI Engineer en Acme. Python.\n"
    "Beneficios: vacaciones por puntos, teletrabajo y flexibilidad."
)
SALARY_LOW = "AI Engineer en Acme. Python. Salario: 28.000 - 34.000 € brutos/año."
SALARY_OK = "AI Engineer en Acme. Python. Salario: 38.000 - 45.000 € brutos/año."


def _warn(text: str, location: str | None = None, **prefs: object) -> list[str]:
    kwargs: dict[str, object] = {
        "work_modes": ("remote", "hybrid", "onsite"),
        "min_salary": None,
        "currency": "EUR",
        "lang": "es",
    }
    kwargs.update(prefs)
    return offer_warnings(text, location, **kwargs)  # type: ignore[arg-type]


# --- work mode -------------------------------------------------------------------


def test_work_mode_reuses_the_location_gate_detection() -> None:
    assert work_mode(ONSITE) == "onsite"
    assert work_mode(REMOTE) == "remote"
    assert work_mode(HYBRID) == "hybrid"
    assert work_mode("AI Engineer en Acme. Python.") == "unknown"
    # "teletrabajo" among the benefits is a perk, not the work mode.
    assert work_mode(PERK) == "hybrid"


def test_onsite_offer_warns_when_the_profile_only_accepts_remote() -> None:
    lines = _warn(ONSITE, work_modes=("remote",))
    assert lines == ["⚠️ Modalidad presencial; aceptas: remoto"]
    assert _warn(ONSITE, work_modes=("remote", "hybrid"), lang="en") == [
        "⚠️ On-site work; you accept: remote and hybrid"
    ]


def test_accepted_or_unknown_work_mode_gives_no_warning() -> None:
    assert _warn(REMOTE, work_modes=("remote",)) == []
    assert _warn("AI Engineer en Acme. Python.", work_modes=("remote",)) == []
    # A perk "teletrabajo" does not make an office job remote...
    assert _warn(PERK, work_modes=("remote",)) == [
        "⚠️ Modalidad híbrida; aceptas: remoto"
    ]
    # ...and all modes accepted (the default) never warns.
    assert _warn(ONSITE) == []


# --- salary ----------------------------------------------------------------------


def test_published_salary_below_the_minimum_warns() -> None:
    assert _warn(SALARY_LOW, min_salary=40000) == [
        "⚠️ Salario por debajo de tu mínimo (40.000 €): hasta 34.000 €"
    ]
    assert _warn(SALARY_LOW, min_salary=40000, lang="en") == [
        "⚠️ Salary below your minimum (40.000 €): up to 34.000 €"
    ]


def test_salary_warning_needs_a_stated_salary_in_the_profile_currency() -> None:
    assert _warn(SALARY_OK, min_salary=40000) == []  # upper bound reaches it
    assert _warn(ONSITE, min_salary=40000) == []  # no salary stated
    assert _warn("Python. Salario competitivo.", min_salary=40000) == []
    # The extractor reads euros only: another profile currency never compares.
    assert _warn(SALARY_LOW, min_salary=40000, currency="USD") == []
    assert _warn(SALARY_LOW) == []  # no minimum set


# --- the card (service seam) -----------------------------------------------------


def _service(tmp_path: Path, prefs: str | None) -> tuple[JobService, OfferRepository]:
    tmp_path.mkdir(exist_ok=True)
    service, repo = make_service(
        tmp_path,
        FakeLlmClient(_extraction()),
        FakeFetcher(FetchResponse(200, "", OFFER_URL)),
        now_fn=lambda: NOW,
    )
    if prefs is not None:
        path = tmp_path / "preferences.toml"
        path.write_text(prefs, encoding="utf-8")
        service._paths = replace(service._paths, preferences=path)
        service.reload_preferences()
    return service, repo


def _offer(repo: OfferRepository, text: str, location: str | None = None) -> int:
    return repo.upsert_offer(
        OfferInput(text=text, source="paste", title="AI Engineer", location=location)
    )


_PREFS = '[search]\nwork_modes = ["remote"]\nmin_salary = 40000\n'


def test_card_extras_carry_the_warnings_from_the_live_preferences(
    tmp_path: Path,
) -> None:
    service, repo = _service(tmp_path, _PREFS)
    offer_id = _offer(repo, f"{ONSITE} Salario: 28.000 - 34.000 € brutos/año.")
    extras = service.card_extras(offer_id).splitlines()
    assert extras[:2] == [
        "⚠️ Modalidad presencial; aceptas: remoto",
        "⚠️ Salario por debajo de tu mínimo (40.000 €): hasta 34.000 €",
    ]
    assert extras[2].startswith("💶 Salario publicado")


@pytest.mark.parametrize("prefs", [None, "", "[search]\n"])
def test_profiles_without_search_keys_get_exactly_the_old_card(
    tmp_path: Path, prefs: str | None
) -> None:
    service, repo = _service(tmp_path, prefs)
    offer_id = _offer(repo, SALARY_LOW + " Puesto presencial.")
    extras = service.card_extras(offer_id)
    assert "⚠️" not in extras
    assert extras.startswith("💶 Salario publicado: 28.000–34.000 € brutos/año")


def test_warnings_never_change_the_score_nor_discard(tmp_path: Path) -> None:
    plain, plain_repo = _service(tmp_path / "plain", None)
    warned, warned_repo = _service(tmp_path / "warned", _PREFS)
    text = f"{ONSITE} Salario: 28.000 - 34.000 € brutos/año."
    a = asyncio.run(plain.analyze(_offer(plain_repo, text)))
    b = asyncio.run(warned.analyze(_offer(warned_repo, text)))
    assert (b.score, b.walled_reason) == (a.score, a.walled_reason)
    assert [g.verdict for g in b.gates] == [g.verdict for g in a.gates]
