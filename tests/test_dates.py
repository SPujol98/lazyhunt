"""Offer date extraction: explicit patterns and JSON-LD only, never guessed."""

from datetime import date

import pytest

from cva.ingest.dates import extract_dates
from cva.ingest.jsonld import parse_jobposting

TODAY = date(2026, 10, 18)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fecha límite: 30/11/2026", "2026-11-30"),
        ("Plazo de inscripción hasta el 30 de noviembre de 2026.", "2026-11-30"),
        ("Deadline: November 30, 2026", "2026-11-30"),
        ("Deadline: 30 November 2026", "2026-11-30"),
        ("Apply by 2026-11-30 at the latest", "2026-11-30"),
        ("Data límit: 30/11/2026", "2026-11-30"),
        ("Data límit d'inscripció: 30 de novembre del 2026", "2026-11-30"),
        ("Fecha límite de solicitud: 5-12-2026", "2026-12-05"),
        ("Application deadline 15.01.2027", "2027-01-15"),
        ("Deadline: 12/25/2026", "2026-12-25"),  # month > 12 can only be M/D
        ("Closing date: Dec 3, 2026", "2026-12-03"),
        ("Cierre de candidaturas: 01/09/2026", "2026-09-01"),  # past: stored anyway
    ],
)
def test_deadline_from_explicit_text(text: str, expected: str) -> None:
    assert extract_dates(text, TODAY).limite == expected


@pytest.mark.parametrize(
    "text",
    [
        "Nos encantan los plazos ajustados y las fechas de entrega.",
        "Deadline: 05/06/2026",  # English marker + ambiguous numeric date
        "Fecha límite: 31/02/2026",  # impossible date
        "Fecha límite: 30 de noviembre",  # no year: never guess
        "Fecha límite: 2026-13-40",
        "Deadline: 30/11/2099",  # absurd horizon
        "Contrato desde el 30/11/2026 sin más información",  # no marker
    ],
)
def test_deadline_is_none_when_unclear(text: str) -> None:
    assert extract_dates(text, TODAY).limite is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Publicada el 01/10/2026", "2026-10-01"),
        ("Publicado el 1 de octubre de 2026", "2026-10-01"),
        ("Posted on October 1, 2026", "2026-10-01"),
        ("Date posted: 2026-10-01", "2026-10-01"),
        ("Data de publicació: 01/10/2026", "2026-10-01"),
        ("Publicada hace 3 días", "2026-10-15"),
        ("Posted 2 weeks ago", "2026-10-04"),
        ("Publicada fa 4 dies", "2026-10-14"),
    ],
)
def test_publication_from_explicit_text(text: str, expected: str) -> None:
    assert extract_dates(text, TODAY).publicacion == expected


@pytest.mark.parametrize(
    "text",
    [
        "Publicada hace 3 meses",  # months are too coarse: skip
        "Publicada hace 30+ días",
        "Reposted 3 days ago",  # a repost is not the first posting
        "Hace 3 días ocurrió algo",  # relative but not a posting age
        "Publicada el 01/10/2099",  # future
        "Posted on 03/04/2026",  # English marker + ambiguous
    ],
)
def test_publication_is_none_when_unclear(text: str) -> None:
    assert extract_dates(text, TODAY).publicacion is None


def test_structured_dates_win_over_text() -> None:
    dates = extract_dates(
        "Deadline: 01/12/2026",
        TODAY,
        posted="2026-10-02T09:30:00Z",
        valid_through="2026-11-15",
    )
    assert (dates.publicacion, dates.limite) == ("2026-10-02", "2026-11-15")


def test_invalid_structured_dates_fall_back_to_text() -> None:
    dates = extract_dates("Apply by 2026-11-30", TODAY, valid_through="soon")
    assert dates.limite == "2026-11-30"


def test_jobposting_exposes_date_posted_and_valid_through() -> None:
    body = (
        '<script type="application/ld+json">{"@type":"JobPosting","title":"T",'
        '"description":"<p>hola</p>","datePosted":"2026-10-01",'
        '"validThrough":"2026-11-30T23:59:00"}</script>'
    )
    data = parse_jobposting(body)
    assert data is not None
    assert (data.date_posted, data.valid_through) == (
        "2026-10-01",
        "2026-11-30T23:59:00",
    )


def test_jobposting_without_dates_keeps_none() -> None:
    body = (
        '<script type="application/ld+json">{"@type":"JobPosting",'
        '"description":"hola"}</script>'
    )
    data = parse_jobposting(body)
    assert data is not None
    assert data.date_posted is None and data.valid_through is None


def test_typo_year_first_match_does_not_hide_later_valid_deadline() -> None:
    text = "Fecha límite: 30/11/2099 (corregido). Fecha límite: 30/11/2026"
    assert extract_dates(text, TODAY).limite == "2026-11-30"


def test_future_posting_first_match_does_not_hide_later_valid_one() -> None:
    text = "Publicada el 01/10/2099. Publicada el 01/10/2026"
    assert extract_dates(text, TODAY).publicacion == "2026-10-01"
