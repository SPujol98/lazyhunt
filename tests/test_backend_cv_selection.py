"""backend-profile T3: variant selection by offer profile (Backend CV in the
offer language, CA -> ES) (odd/tasks/backend-profile.md)."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from pypdf import PdfReader

from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent
MASTERS = DEMO_MASTERS
URL = "https://www.linkedin.com/jobs/view/4459804598/"
ES_TEXT = (
    "Buscamos desarrollador backend. Imprescindible Python y experiencia con APIs."
)
EN_TEXT = "We are hiring a backend developer. Python and REST APIs are required for this role."
CA_TEXT = "Cerquem un desenvolupador backend amb experiència en Python i en APIs. Treballaràs amb nosaltres."


# --- service: variant selection --------------------------------------------


class _Llm:
    model = "m"

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        return OfferExtraction(
            title="Dev",
            company="A",
            requirements=[
                Requirement(
                    name="Python", category="skill", must_have=True, evidence="Python"
                )
            ],
        )


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:  # pragma: no cover
        raise AssertionError("no fetch expected")


def _service(tmp_path: Path) -> tuple[JobService, OfferRepository]:
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    syn = load_synonyms(DEMO_SYNONYMS, db)
    svc = JobService(
        repo=repo,
        llm=_Llm(),  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=syn,
        cache=ExtractionCache(conn),
        masters_dir=MASTERS,
    )
    return svc, repo


def _offer(repo: OfferRepository, text: str, profile: str | None) -> int:
    return repo.upsert_offer(
        OfferInput(
            title="Backend Developer",
            company="Acme",
            url=URL,
            text=text,
            source="bridge-inline",
            profile=profile,  # type: ignore[arg-type]
        )
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(ES_TEXT, "BACKEND_ES", id="es"),
        pytest.param(EN_TEXT, "BACKEND_EN", id="en"),
        pytest.param(CA_TEXT, "BACKEND_ES", id="ca-falls-back-to-es"),
    ],
)
def test_backend_offer_without_forced_role_gets_backend_cv(
    tmp_path: Path, text: str, expected: str
) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, text, "backend")
    pdf = asyncio.run(svc.generate_cv(oid))
    row = repo.get_offer(oid)
    assert row.cv_base == expected
    assert row.estado == "preparada"
    assert len(PdfReader(str(pdf)).pages) == 1


@pytest.mark.parametrize("profile", ["ai", None])
def test_ai_and_legacy_offers_keep_the_ai_cv(
    tmp_path: Path, profile: str | None
) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, profile)
    asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base == "AI_ES"


def test_forced_role_beats_the_offer_profile(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "backend")
    asyncio.run(svc.generate_cv(oid, "AI"))
    assert repo.get_offer(oid).cv_base == "AI_ES"
    oid2 = _offer(repo, EN_TEXT, "ai")
    repo.upsert_offer(
        OfferInput(
            title="Other",
            company="B",
            url=URL + "2",
            text=EN_TEXT,
            source="bridge-inline",
        )
    )
    asyncio.run(svc.generate_cv(oid2, "BACKEND", "en"))
    assert repo.get_offer(oid2).cv_base == "BACKEND_EN"


def test_explicit_backend_catalan_is_rejected(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "backend")
    with pytest.raises(ServiceError, match="BACKEND_ca"):
        asyncio.run(svc.generate_cv(oid, "BACKEND", "ca"))


def test_backend_cv_file_name_uses_the_backend_label(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "backend")
    pdf = asyncio.run(svc.generate_cv(oid))
    assert pdf.is_file()
    assert repo.get_offer(oid).cv_adaptado == str(pdf)
