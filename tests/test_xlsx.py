"""Sent-only xlsx export behavior (tracker spec: export excludes unsent)."""

import sqlite3
from pathlib import Path

import openpyxl
import pytest

from cva.tracker import OfferInput, OfferRepository, export_xlsx

S1_COLUMNS: list[str] = [
    "id",
    "fecha_envio",
    "empresa",
    "posicion",
    "origen",
    "url_oferta",
    "cv_base",
    "cv_adaptado",
    "cambios",
    "match",
    "sueldo_rango",
    "sueldo_ofrecido",
    "estado",
    "fecha_update",
    "contacto",
    "notas",
    "source_data",
]


class _Clock:
    def __init__(self, today: str) -> None:
        self.today = today

    def __call__(self) -> str:
        return self.today


@pytest.fixture()
def clock() -> _Clock:
    return _Clock("2026-08-30")


@pytest.fixture()
def repo(tmp_path: Path, clock: _Clock) -> OfferRepository:
    conn = sqlite3.connect(tmp_path / "app.db")
    return OfferRepository(conn, clock=clock)


def _offer(company: str, url: str) -> OfferInput:
    return OfferInput(
        job_id="4123",
        title="AI Engineer",
        company=company,
        url=url,
        text="full offer text",
        source="link",
    )


def _seed_three_sent_two_unsent(repo: OfferRepository) -> list[int]:
    sent_ids = [
        repo.upsert_offer(_offer(f"Acme{i}", f"https://x/{i}")) for i in range(3)
    ]
    for i, offer_id in enumerate(sent_ids):
        repo.mark_sent(
            offer_id,
            cv_path=f"adaptadas/cv{i}.pdf",
            cambios=f"cambio {i}",
            match=70 + i,
        )
    repo.upsert_offer(_offer("PendingCo", "https://y/0"))  # pendiente_analisis
    discarded = repo.upsert_offer(_offer("DiscardCo", "https://y/1"))
    repo.set_estado(discarded, "descartada")
    return sent_ids


def test_export_writes_only_sent_rows_with_s1_columns(
    repo: OfferRepository, tmp_path: Path
) -> None:
    sent_ids = _seed_three_sent_two_unsent(repo)
    out = tmp_path / "tracker.xlsx"
    assert export_xlsx(repo, out) == out
    rows = list(openpyxl.load_workbook(out).active.iter_rows(values_only=True))
    header, data = rows[0], rows[1:]
    assert list(header) == S1_COLUMNS
    assert len(data) == 3
    assert {row[S1_COLUMNS.index("empresa")] for row in data} == {
        "Acme0",
        "Acme1",
        "Acme2",
    }
    assert {row[S1_COLUMNS.index("estado")] for row in data} == {"enviada"}
    by_company = {row[S1_COLUMNS.index("empresa")]: row for row in data}
    acme1 = by_company["Acme1"]
    assert acme1[S1_COLUMNS.index("id")] == sent_ids[1]
    assert acme1[S1_COLUMNS.index("fecha_envio")] == "2026-08-30"
    assert acme1[S1_COLUMNS.index("cv_adaptado")] == "adaptadas/cv1.pdf"
    assert acme1[S1_COLUMNS.index("cambios")] == "cambio 1"
    assert acme1[S1_COLUMNS.index("match")] == 71
    assert acme1[S1_COLUMNS.index("source_data")] == "full offer text"


def test_export_without_sent_offers_writes_header_only(
    repo: OfferRepository, tmp_path: Path
) -> None:
    repo.upsert_offer(_offer("PendingCo", "https://y/0"))  # unsent only
    out = export_xlsx(repo, tmp_path / "empty.xlsx")
    rows = list(openpyxl.load_workbook(out).active.iter_rows(values_only=True))
    assert rows == [tuple(S1_COLUMNS)]
