"""fixed-base-cvs: generate_cv delivers a byte-identical copy of the owner's base PDF."""

import asyncio
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from cva.claimsdb import load_claims
from cva.cvgen import CvgenError, CvVariant
from cva.cvgen.base_pdfs import deliver_base_pdf, deliverable_variants
from cva.fetch import FetchResponse
from cva.match import OfferExtraction, Requirement
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import BASE_CAMBIOS, NEUTRAL_CAMBIOS, JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_BASE_PDFS, DEMO_CLAIMS, DEMO_SYNONYMS

_ROOT = Path(__file__).resolve().parent.parent
BASE = DEMO_BASE_PDFS
URL = "https://www.linkedin.com/jobs/view/4459804598/"
ES_TEXT = (
    "Buscamos desarrollador backend. Imprescindible Python y experiencia con APIs."
)
EN_TEXT = "We are hiring a backend developer. Python and REST APIs are required for this role."
CA_TEXT = "Cerquem un desenvolupador backend amb experiència en Python i en APIs. Treballaràs amb nosaltres."

FILES = {
    "AI_ES": "CV_AI_Engineer_Alex_Rivera.pdf",
    "AI_EN": "CV_AI_Engineer_Alex_Rivera_EN.pdf",
    "AI_CA": "CV_AI_Engineer_Alex_Rivera_CA.pdf",
    "BACKEND_ES": "CV_Backend_Developer_Python_Alex_Rivera.pdf",
    "BACKEND_EN": "CV_Backend_Developer_Python_Alex_Rivera_EN.pdf",
}


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


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _service(
    tmp_path: Path, base_dir: Path = BASE
) -> tuple[JobService, OfferRepository]:
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
        base_pdfs_dir=base_dir,
        output_dir=tmp_path / "adaptadas",
    )
    return svc, repo


def _offer(
    repo: OfferRepository,
    text: str,
    profile: str | None,
    title: str = "Backend Developer",
    url: str = URL,
) -> int:
    return repo.upsert_offer(
        OfferInput(
            title=title,
            company="Acme",
            url=url,
            text=text,
            source="bridge-inline",
            profile=profile,  # type: ignore[arg-type]
        )
    )


# --- selection matrix -------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "text", "role", "lang", "expected"),
    [
        pytest.param("ai", ES_TEXT, None, None, "AI_ES", id="ai-es"),
        pytest.param("ai", EN_TEXT, None, None, "AI_EN", id="ai-en"),
        pytest.param("ai", CA_TEXT, None, None, "AI_CA", id="ai-ca"),
        pytest.param(None, ES_TEXT, None, None, "AI_ES", id="legacy-null-is-ai"),
        pytest.param("backend", ES_TEXT, None, None, "BACKEND_ES", id="backend-es"),
        pytest.param("backend", EN_TEXT, None, None, "BACKEND_EN", id="backend-en"),
        pytest.param(
            "backend", CA_TEXT, None, None, "BACKEND_ES", id="backend-ca-to-es"
        ),
        pytest.param("backend", ES_TEXT, "AI", None, "AI_ES", id="forced-ai"),
        pytest.param("ai", EN_TEXT, "BACKEND", None, "BACKEND_EN", id="forced-backend"),
        pytest.param("ai", ES_TEXT, "AI", "ca", "AI_CA", id="explicit-ca-ai"),
        pytest.param(
            "ai", ES_TEXT, "BACKEND", "en", "BACKEND_EN", id="explicit-en-backend"
        ),
    ],
)
def test_variant_selection_delivers_the_matching_base_pdf(
    tmp_path: Path,
    profile: str | None,
    text: str,
    role: str | None,
    lang: str | None,
    expected: str,
) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, text, profile)
    pdf = asyncio.run(svc.generate_cv(oid, role, lang))  # type: ignore[arg-type]
    row = repo.get_offer(oid)
    assert row.cv_base == expected
    assert row.estado == "preparada"
    assert _sha(pdf) == _sha(BASE / FILES[expected])  # byte-identical


def test_explicit_backend_catalan_is_rejected(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "backend")
    with pytest.raises(ServiceError, match="BACKEND_ca"):
        asyncio.run(svc.generate_cv(oid, "BACKEND", "ca"))


def test_ds_role_is_no_longer_a_bot_role(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "ai")
    with pytest.raises(ServiceError, match="DS"):
        asyncio.run(svc.generate_cv(oid, "DS"))  # type: ignore[arg-type]


# --- delivery: naming, record, no render -------------------------------------


def test_ai_offer_gets_generic_ai_name_in_offer_folder(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, ES_TEXT, "ai", title="Junior AI Engineer (all genders welcome)")
    pdf = asyncio.run(svc.generate_cv(oid))
    assert (
        pdf == tmp_path / "adaptadas" / f"{oid}_Acme" / "Alex_Rivera_Ingeniero_IA.pdf"
    )
    row = repo.get_offer(oid)
    assert row.cv_adaptado == str(pdf)
    assert (row.cambios or "").startswith(BASE_CAMBIOS)
    assert BASE_CAMBIOS != NEUTRAL_CAMBIOS
    assert _sha(pdf) == _sha(BASE / FILES["AI_ES"])


@pytest.mark.parametrize(
    ("profile", "text", "lang", "expected"),
    [
        ("backend", ES_TEXT, None, "Alex_Rivera_Desarrollador_Backend.pdf"),
        ("backend", EN_TEXT, None, "Alex_Rivera_Backend_Developer.pdf"),
        ("ai", ES_TEXT, "ca", "Alex_Rivera_Enginyer_IA.pdf"),
        ("ai", EN_TEXT, None, "Alex_Rivera_AI_Engineer.pdf"),
    ],
)
def test_generic_name_per_branch(
    tmp_path: Path, profile: str, text: str, lang: str | None, expected: str
) -> None:
    svc, repo = _service(tmp_path)
    oid = _offer(repo, text, profile, title="Backend Engineer - Memberships")
    pdf = asyncio.run(svc.generate_cv(oid, None, lang))  # type: ignore[arg-type]
    assert pdf.name == expected


def test_generic_output_path_by_variant_and_ds_refused(tmp_path: Path) -> None:
    from cva.cvgen.engine import CvgenError, generic_output_path
    from cva.cvgen.master import CvVariant

    got = generic_output_path(
        CvVariant.AI_CA, 7, "Acme", tmp_path, owner_slug="Alex_Rivera"
    )
    assert got == tmp_path / "7_Acme" / "Alex_Rivera_Enginyer_IA.pdf"
    with pytest.raises(CvgenError):
        generic_output_path(CvVariant.DS_ES, 7, "Acme", tmp_path)


def test_same_branch_offers_do_not_overwrite_each_other(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    first = _offer(repo, ES_TEXT, "backend", url=URL)
    second = _offer(repo, ES_TEXT, "backend", url=URL + "other/")
    assert first != second
    pdf_a = asyncio.run(svc.generate_cv(first))
    pdf_b = asyncio.run(svc.generate_cv(second))
    assert pdf_a != pdf_b
    assert pdf_a.name == pdf_b.name == "Alex_Rivera_Desarrollador_Backend.pdf"
    assert pdf_a.is_file() and pdf_b.is_file()


def test_regenerate_overwrites_only_its_own_file(tmp_path: Path) -> None:
    svc, repo = _service(tmp_path)
    first = _offer(repo, ES_TEXT, "ai", url=URL)
    second = _offer(repo, ES_TEXT, "ai", url=URL + "other/")
    pdf_a = asyncio.run(svc.generate_cv(first))
    pdf_b = asyncio.run(svc.generate_cv(second))
    pdf_b.write_bytes(b"sentinel")
    again = asyncio.run(svc.generate_cv(first))
    assert again == pdf_a
    assert pdf_b.read_bytes() == b"sentinel"
    assert _sha(pdf_a) == _sha(BASE / FILES["AI_ES"])
    assert sorted(p.name for p in pdf_a.parent.iterdir()) == [pdf_a.name]


@pytest.mark.parametrize(
    ("company", "slug"),
    [
        ("Acme", "Acme"),
        ("Ñandú & Cía, S.L.", "Nandu_Cia_S_L"),
        ("???", "oferta"),
    ],
)
def test_company_slug_is_ascii_and_has_fallback(
    tmp_path: Path, company: str, slug: str
) -> None:
    svc, repo = _service(tmp_path)
    oid = repo.upsert_offer(
        OfferInput(
            title="Dev",
            company=company,
            url=URL,
            text=ES_TEXT,
            source="bridge-inline",
            profile="ai",  # type: ignore[arg-type]
        )
    )
    pdf = asyncio.run(svc.generate_cv(oid))
    assert pdf.parent.name == f"{oid}_{slug}"


def test_delivery_never_renders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cva.service as service_module

    assert not hasattr(service_module, "render")
    assert not hasattr(service_module, "load_master")
    svc, repo = _service(tmp_path)
    asyncio.run(svc.generate_cv(_offer(repo, ES_TEXT, "ai")))


# --- integrity ----------------------------------------------------------------


def _copy_base(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    shutil.copytree(BASE, base)
    return base


def test_sha_mismatch_blocks_delivery(tmp_path: Path) -> None:
    base = _copy_base(tmp_path)
    (base / FILES["AI_ES"]).write_bytes(b"tampered")
    svc, repo = _service(tmp_path, base)
    oid = _offer(repo, ES_TEXT, "ai")
    with pytest.raises(CvgenError, match="sha256"):
        asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).estado != "preparada"
    assert not (tmp_path / "adaptadas").exists()


def test_missing_pdf_blocks_delivery(tmp_path: Path) -> None:
    base = _copy_base(tmp_path)
    (base / FILES["BACKEND_EN"]).unlink()
    svc, repo = _service(tmp_path, base)
    oid = _offer(repo, EN_TEXT, "backend")
    with pytest.raises(CvgenError, match="no encontrado"):
        asyncio.run(svc.generate_cv(oid))


def test_missing_manifest_blocks_delivery(tmp_path: Path) -> None:
    base = _copy_base(tmp_path)
    (base / "manifest.json").unlink()
    svc, repo = _service(tmp_path, base)
    with pytest.raises(CvgenError, match="manifiesto"):
        asyncio.run(svc.generate_cv(_offer(repo, ES_TEXT, "ai")))


def test_variant_not_in_manifest_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CvgenError, match="DS_ES"):
        deliver_base_pdf(CvVariant.DS_ES, tmp_path / "out.pdf", BASE)


def test_manifest_covers_exactly_the_deliverable_variants() -> None:
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))[
        "variants"
    ]
    assert {CvVariant[name] for name in manifest} == set(deliverable_variants(BASE))
    assert {name: entry["file"] for name, entry in manifest.items()} == FILES
    assert sorted(p.name for p in BASE.glob("*.pdf")) == sorted(FILES.values())


def test_delivery_is_atomic_on_copy_failure(tmp_path: Path, monkeypatch) -> None:
    """An interrupted copy must not leave a truncated PDF at the tailored path."""
    import shutil as _shutil

    from cva.cvgen import base_pdfs

    out = tmp_path / "out" / "Alex_Rivera_X.pdf"
    out.parent.mkdir()
    out.write_bytes(b"previous")

    def boom(src, dst, *a, **k):
        Path(dst).write_bytes(b"trunc")
        raise OSError("disk full")

    monkeypatch.setattr(_shutil, "copyfile", boom)
    monkeypatch.setattr(base_pdfs.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        base_pdfs.deliver_base_pdf(CvVariant.AI_ES, out)
    assert out.read_bytes() == b"previous"
    assert [p.name for p in out.parent.iterdir()] == ["Alex_Rivera_X.pdf"]


def test_cambios_names_the_delivered_base_version() -> None:
    """cambios carries the variant and the manifest sha prefix, not a fixed 'v4'."""
    from cva.cvgen.base_pdfs import base_cambios

    text = base_cambios(CvVariant.BACKEND_EN)
    assert "BACKEND_EN" in text and "v4" not in text
    import re

    assert re.search(r"\(([0-9a-f]{8})\)", text)
