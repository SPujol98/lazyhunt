"""Slice 9 E2E: the FULL service pipeline on tmp state, zero live I/O.

Drives paste → ingest → analyze (fake LLM, deterministic extraction) →
generate_cv (REAL WeasyPrint against the real masters) → mark_sent →
export_xlsx, asserting every estado transition, the persisted match_results
and keyword_registry rows, and the sent-only xlsx filter. This is the spec's
SC2/SC4/SC5 happy path proven end-to-end at the service seam, minus live
Telegram (owner smoke, tasks 8.6/8.7/9.5).
"""

import asyncio
import sqlite3
from pathlib import Path

from openpyxl import load_workbook
from pypdf import PdfReader

from cva.claimsdb import load_claims
from cva.fetch import FetchResponse
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService
from cva.tracker import OfferRepository
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS, DEMO_SYNONYMS

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = DEMO_CLAIMS
SYNONYMS_PATH = DEMO_SYNONYMS
MASTERS_DIR = DEMO_MASTERS

OFFER_TEXT = (
    "AI Engineer en Acme (Barcelona). Imprescindible experiencia con Python. "
    "Se valoran conocimientos de containers para las integraciones de datos. "
    "También gestionarás clusters de Kubernetes en producción. Ofrecemos "
    "formación continua y contrato indefinido."
)


class FakeLlmClient:
    """Deterministic stand-in for the LlmClient protocol (zero network)."""

    model = "e2e-model"

    def __init__(self, reply: OfferExtraction) -> None:
        self.calls = 0
        self._reply = reply

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:
        self.calls += 1
        return self._reply


class FakeFetcher:
    """Unused by the paste path; satisfies the service constructor seam."""

    async def get(self, url: str) -> FetchResponse:
        raise AssertionError("E2E paste flow must never fetch")


def _extraction() -> OfferExtraction:
    return OfferExtraction(
        title="AI Engineer",
        company="Acme",
        requirements=[
            Requirement(
                name="Python",
                category="skill",
                must_have=True,
                evidence="experiencia con Python",
            ),
            Requirement(
                name="containers",
                category="tool",
                must_have=False,
                evidence="conocimientos de containers",
            ),
            Requirement(
                name="Kubernetes",
                category="tool",
                must_have=True,
                evidence="clusters de Kubernetes",
            ),
        ],
        keywords=["Python", "containers", "Kubernetes"],
    )


def make_e2e_service(
    tmp_path: Path,
) -> tuple[JobService, OfferRepository, FakeLlmClient]:
    conn = sqlite3.connect(tmp_path / "e2e.db")
    repo = OfferRepository(conn)
    llm = FakeLlmClient(_extraction())
    claims = load_claims(CLAIMS_PATH)
    service = JobService(
        repo=repo,
        llm=llm,
        fetcher=FakeFetcher(),
        db=claims,
        syn=load_synonyms(SYNONYMS_PATH, claims),
        cache=ExtractionCache(conn),
        threshold=65,
        masters_dir=MASTERS_DIR,
    )
    return service, repo, llm


def test_full_pipeline_paste_analyze_cv_send_export(
    tmp_path: Path, monkeypatch
) -> None:
    """SC2/SC4/SC5 happy path: paste → CV → send → xlsx, estados verbatim."""
    monkeypatch.chdir(tmp_path)  # adaptadas/ + xlsx land in tmp state
    service, repo, llm = make_e2e_service(tmp_path)

    # 1. Paste offer → pendiente_analisis
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    assert repo.get_offer(offer_id).estado == "pendiente_analisis"

    # 2. Analyze → deterministic breakdown vs threshold 65 → analizada
    result = asyncio.run(service.analyze(offer_id))
    assert llm.calls == 1
    assert result.threshold == 65
    assert result.score == 50  # 100·(3·1.0 + 1·0.5 + 3·0.0)/7
    by_name = {m.requirement.name: m.status for m in result.breakdown}
    assert by_name == {"Python": "✓", "containers": "~", "Kubernetes": "✗"}
    assert repo.get_offer(offer_id).estado == "analizada"

    # 3. match_results + keyword_registry persisted
    match_row = repo.latest_match(offer_id)
    assert match_row is not None
    assert match_row.score == 50
    assert match_row.threshold == 65
    keywords = {r.keyword: r for r in service.keywords()}
    assert {k: v.status for k, v in keywords.items()} == {
        "Python": "✓",
        "containers": "~",
        "Kubernetes": "✗",
    }
    assert all(r.frequency == 1 for r in keywords.values())

    # 4. Pre-send export is header-only (sent-only filter)
    pre_xlsx = service.export(tmp_path / "pre.xlsx")
    wb = load_workbook(pre_xlsx)
    sheet = wb.active
    assert sheet is not None
    assert sheet.max_row == 1

    # 5. generate_cv: REAL WeasyPrint, validators clean, exactly 1 page
    pdf_path = asyncio.run(service.generate_cv(offer_id, "AI"))
    assert pdf_path.is_file()
    assert PdfReader(str(pdf_path)).pages and len(PdfReader(str(pdf_path)).pages) == 1
    row = repo.get_offer(offer_id)
    assert row.estado == "preparada"
    assert row.cv_base == "AI_ES"  # Spanish paste → inferred es
    assert row.cv_adaptado == str(pdf_path)

    # 6. mark_sent → enviada + fecha_envio + match snapshot
    service.mark_sent(offer_id, pdf_path)
    sent = repo.get_offer(offer_id)
    assert sent.estado == "enviada"
    assert sent.fecha_envio is not None
    assert sent.match == 50

    # 7. Post-send export: exactly the one sent row
    post_xlsx = service.export(tmp_path / "post.xlsx")
    wb2 = load_workbook(post_xlsx)
    sheet2 = wb2.active
    assert sheet2 is not None
    assert sheet2.max_row == 2
    header = [cell.value for cell in next(sheet2.iter_rows(min_row=1, max_row=1))]
    data = [cell.value for cell in next(sheet2.iter_rows(min_row=2, max_row=2))]
    assert header[0] == "id" and header[9] == "match" and header[12] == "estado"
    assert data[0] == offer_id
    assert data[9] == 50
    assert data[12] == "enviada"


def test_pipeline_state_machine_without_send(tmp_path: Path, monkeypatch) -> None:
    """Unsent offer never leaks into the export (sent-only invariant)."""
    monkeypatch.chdir(tmp_path)
    service, repo, _llm = make_e2e_service(tmp_path)
    offer_id = asyncio.run(service.ingest(None, OFFER_TEXT))
    asyncio.run(service.analyze(offer_id))
    asyncio.run(service.generate_cv(offer_id, "AI"))
    xlsx = service.export(tmp_path / "unsent.xlsx")
    wb = load_workbook(xlsx)
    sheet = wb.active
    assert sheet is not None
    assert sheet.max_row == 1  # preparada ≠ enviada → not exported
    assert repo.get_offer(offer_id).estado == "preparada"
