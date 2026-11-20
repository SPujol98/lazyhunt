"""role-families T3: base CV delivery follows the profile's role families.

A family declared in ``preferences.toml`` gets its base CVs from manifest keys
``<ROLE>_<LANG>`` with no code change; its CV languages come from the family
(odd/tasks/role-families.md S3, S5; S4 is pinned by test_base_cv_delivery.py).
"""

import asyncio
import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

import cva.profile_paths as profile_paths_module
from cva.claimsdb import load_claims
from cva.cvgen import CvgenError
from cva.cvgen.base_pdfs import deliverable_variants
from cva.families import BUILTIN_REGISTRY
from cva.fetch import FetchResponse
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.service import JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.demo import DEMO_BASE_PDFS, DEMO_CLAIMS, DEMO_SYNONYMS

URL = "https://www.linkedin.com/jobs/view/4459804598/"
ES_TEXT = (
    "Buscamos desarrollador frontend. Imprescindible React y experiencia con APIs."
)
EN_TEXT = "We are hiring a frontend developer. React and REST APIs are required for this role."
CA_TEXT = "Cerquem un desenvolupador frontend amb experiència en React i en APIs. Treballaràs amb nosaltres."

FRONTEND_PREFS = """
[families.ai]
[families.frontend]
label = "Frontend"
languages = ["es", "en"]
title_terms = ["Frontend", "React"]
"""


class _Llm:
    model = "m"

    async def json_completion(
        self, system: str, user: str, schema: type[object]
    ) -> object:  # pragma: no cover
        raise AssertionError("no LLM call expected")


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:  # pragma: no cover
        raise AssertionError("no fetch expected")


def _pdf(text: str) -> bytes:
    """A tiny one-page PDF whose bytes differ per ``text``."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length "
        + str(len(stream)).encode()
        + b">>stream\n"
        + stream
        + b"\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _base(tmp_path: Path, extra: dict[str, dict[str, str]]) -> Path:
    """Demo base PDFs plus ``extra`` variants (key -> {"text", optional
    "delivery_name"}) written as tiny PDFs listed in the manifest."""
    base = tmp_path / "base"
    shutil.copytree(DEMO_BASE_PDFS, base)
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    for key, spec in extra.items():
        data = _pdf(spec["text"])
        (base / f"CV_{key}.pdf").write_bytes(data)
        entry = {"file": f"CV_{key}.pdf", "sha256": _sha(data)}
        if "delivery_name" in spec:
            entry["delivery_name"] = spec["delivery_name"]
        manifest["variants"][key] = entry
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return base


def _service(
    tmp_path: Path, base: Path, prefs: str | None
) -> tuple[JobService, OfferRepository]:
    paths = profile_paths_module.resolve_profile_paths()  # the demo profile
    if prefs is not None:
        pref_path = tmp_path / "preferences.toml"
        pref_path.write_text(prefs, encoding="utf-8")
        paths = replace(paths, preferences=pref_path)
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    svc = JobService(
        repo=repo,
        llm=_Llm(),  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        base_pdfs_dir=base,
        profile_paths=paths,
        output_dir=tmp_path / "adaptadas",
    )
    return svc, repo


def _offer(repo: OfferRepository, text: str, profile: str | None) -> int:
    return repo.upsert_offer(
        OfferInput(
            title="Frontend Developer",
            company="Acme",
            url=URL,
            text=text,
            source="bridge-inline",
            profile=profile,
        )
    )


_FRONTEND = {"FRONTEND_ES": {"text": "front es"}, "FRONTEND_EN": {"text": "front en"}}


@pytest.mark.parametrize(
    ("text", "lang", "key", "name"),
    [
        pytest.param(
            ES_TEXT, None, "FRONTEND_ES", "Alex_Rivera_Frontend_ES.pdf", id="es"
        ),
        pytest.param(
            EN_TEXT, None, "FRONTEND_EN", "Alex_Rivera_Frontend_EN.pdf", id="en"
        ),
        pytest.param(
            ES_TEXT,
            "en",
            "FRONTEND_EN",
            "Alex_Rivera_Frontend_EN.pdf",
            id="explicit-en",
        ),
    ],
)
def test_declared_family_delivers_its_manifest_base_cv(
    tmp_path: Path, text: str, lang: str | None, key: str, name: str
) -> None:
    base = _base(tmp_path, _FRONTEND)
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, text, "frontend")
    pdf = asyncio.run(svc.generate_cv(oid, None, lang))  # type: ignore[arg-type]
    row = repo.get_offer(oid)
    assert row.cv_base == key
    assert row.estado == "preparada"
    assert row.cv_adaptado == pdf.as_posix()
    assert pdf == tmp_path / "adaptadas" / f"{oid}_Acme" / name
    assert pdf.read_bytes() == (base / f"CV_{key}.pdf").read_bytes()
    assert row.cambios is not None and key in row.cambios


def test_explicit_family_role_is_honoured(tmp_path: Path) -> None:
    base = _base(tmp_path, _FRONTEND)
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, ES_TEXT, "ai")
    asyncio.run(svc.generate_cv(oid, "FRONTEND", None))  # type: ignore[arg-type]
    assert repo.get_offer(oid).cv_base == "FRONTEND_ES"


@pytest.mark.parametrize(
    ("profile", "key"),
    [("frontend", "FRONTEND_EN"), ("ai", "AI_EN")],
)
def test_manifest_delivery_name_overrides_the_file_name(
    tmp_path: Path, profile: str, key: str
) -> None:
    base = _base(
        tmp_path,
        {
            "FRONTEND_ES": {"text": "front es"},
            "FRONTEND_EN": {"text": "front en", "delivery_name": "Frontend Developer"},
        },
    )
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    manifest["variants"]["AI_EN"]["delivery_name"] = "Frontend Developer"
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, EN_TEXT, profile)
    pdf = asyncio.run(svc.generate_cv(oid))
    assert pdf.name == "Alex_Rivera_Frontend_Developer.pdf"
    assert repo.get_offer(oid).cv_base == key


def test_bad_delivery_name_blocks_delivery(tmp_path: Path) -> None:
    base = _base(
        tmp_path,
        {"FRONTEND_ES": {"text": "front es", "delivery_name": "///"}},
    )
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, ES_TEXT, "frontend")
    with pytest.raises(CvgenError, match="delivery_name"):
        asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base is None


def test_inferred_language_the_family_lacks_falls_back_to_its_first(
    tmp_path: Path,
) -> None:
    base = _base(tmp_path, _FRONTEND)
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, CA_TEXT, "frontend")
    pdf = asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base == "FRONTEND_ES"
    assert pdf.read_bytes() == (base / "CV_FRONTEND_ES.pdf").read_bytes()


def test_inferred_fallback_uses_the_family_first_language(tmp_path: Path) -> None:
    prefs = '[families.ai]\n[families.frontend]\nlanguages = ["en", "es"]\n'
    base = _base(tmp_path, _FRONTEND)
    svc, repo = _service(tmp_path, base, prefs)
    oid = _offer(repo, CA_TEXT, "frontend")
    asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base == "FRONTEND_EN"


def test_explicit_language_the_family_lacks_is_a_clear_error(tmp_path: Path) -> None:
    # The manifest even lists FRONTEND_CA: the family's languages decide, not the
    # manifest nor the DB (whose cv_base CHECK is only a shape rule since v23).
    base = _base(tmp_path, {**_FRONTEND, "FRONTEND_CA": {"text": "front ca"}})
    svc, repo = _service(tmp_path, base, FRONTEND_PREFS)
    oid = _offer(repo, ES_TEXT, "frontend")
    with pytest.raises(ServiceError, match=r"FRONTEND_ca.*frontend.*es, en"):
        asyncio.run(svc.generate_cv(oid, None, "ca"))  # type: ignore[arg-type]
    row = repo.get_offer(oid)
    assert row.cv_base is None and row.cv_adaptado is None
    assert row.estado != "preparada"
    assert not (tmp_path / "adaptadas").exists()


def test_declared_backend_without_catalan_rejects_backend_ca(tmp_path: Path) -> None:
    prefs = "[families.ai]\n[families.backend]\n"
    base = _base(tmp_path, {"BACKEND_CA": {"text": "backend ca"}})
    svc, repo = _service(tmp_path, base, prefs)
    oid = _offer(repo, ES_TEXT, "backend")
    with pytest.raises(ServiceError, match="BACKEND_ca"):
        asyncio.run(svc.generate_cv(oid, "BACKEND", "ca"))
    assert repo.get_offer(oid).cv_base is None
    # Inferred Catalan still downgrades to the family's first language.
    ca = _offer(repo, CA_TEXT, "backend")
    asyncio.run(svc.generate_cv(ca))
    assert repo.get_offer(ca).cv_base == "BACKEND_ES"


def test_undeclared_family_role_is_refused_even_if_in_the_manifest(
    tmp_path: Path,
) -> None:
    base = _base(tmp_path, {"MARKETING_ES": {"text": "mkt"}})
    svc, repo = _service(tmp_path, base, None)
    oid = _offer(repo, ES_TEXT, "ai")
    with pytest.raises(ServiceError, match="MARKETING"):
        asyncio.run(svc.generate_cv(oid, "MARKETING", "es"))  # type: ignore[arg-type]
    assert repo.get_offer(oid).cv_base is None


def test_unknown_manifest_keys_do_not_break_builtin_delivery(tmp_path: Path) -> None:
    base = _base(tmp_path, {"MARKETING_ES": {"text": "mkt"}})
    svc, repo = _service(tmp_path, base, None)
    oid = _offer(repo, ES_TEXT, "ai")
    pdf = asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base == "AI_ES"
    assert pdf.name == "Alex_Rivera_Ingeniero_IA.pdf"
    assert (
        pdf.read_bytes()
        == (DEMO_BASE_PDFS / "CV_AI_Engineer_Alex_Rivera.pdf").read_bytes()
    )
    assert "MARKETING_ES" in deliverable_variants(base)
    assert "MARKETING_ES" not in deliverable_variants(base, BUILTIN_REGISTRY)
    assert deliverable_variants(base, BUILTIN_REGISTRY) == deliverable_variants(
        DEMO_BASE_PDFS
    )


def test_offer_of_an_undeclared_family_degrades_to_the_default(tmp_path: Path) -> None:
    base = _base(tmp_path, _FRONTEND)
    svc, repo = _service(tmp_path, base, None)  # built-ins: no frontend family
    oid = _offer(repo, ES_TEXT, "frontend")
    asyncio.run(svc.generate_cv(oid))
    assert repo.get_offer(oid).cv_base == "AI_ES"
