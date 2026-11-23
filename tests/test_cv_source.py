"""The structured source of a base CV lazyhunt builds (tailored-cv T1, S3/S4(a)/S5).

A starter CV is rendered from a :class:`cva.cvgen.cv_source.CvSource` whose
items point at claim ids; ``/setup`` Confirm stores it as
``cv_base_pdfs/<SLOT>.source.json`` next to the PDF, and
:func:`cva.cvgen.cv_source.adaptable_source` answers it only while the PDF
and every claim it uses are still there.
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cva.cvgen import cv_source as S
from cva.cvgen.starter import render_source, render_starter_cv
from cva.onboarding import cv_upload as U
from cva.onboarding import starter
from cva.onboarding.profile_gen import setup_claims
from tests.test_bot import FakeDocument
from tests.test_onboarding import _setup_mode
from tests.test_onboarding_cv_upload import _pdf, _send, _to_cv_step

ANSWERS: dict[str, Any] = {
    "name": "Alex Rivera",
    "email": "alex.rivera@example.com",
    "phone": "+34 600 000 000",
    "city": "Barcelona",
    "linkedin": "https://linkedin.com/in/alex-rivera-example",
    "families": ["ai"],
    "level": "junior",
    "spoken_langs": ["es", "en"],
    "spoken_levels": {"es": "native", "en": "C1"},
    "skills": ["Python", "SQL", "FastAPI"],
    "exp_years": "1-2",
    "last_role": "Data Analyst",
    "last_company": "Acme",
    "education": ["Data and AI Bootcamp — Northgate Tech Academy", "BSc Physics"],
}
WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
# sha256 of the starter render of ANSWERS before the source refactor
# (render_starter_cv at 969b621, created=WHEN): the same content must give the
# same bytes.
GOLDEN = {
    (
        "en",
        "classic",
    ): "256e0628cbd15df9dbdaca37193cee5b0c41644bd18737b5ae0ed88e0ecda203",
    (
        "en",
        "modern",
    ): "5e02090f9332fd3966882fa639098ae8f655e0cb4f2a3471ca0e99bf9f81c1d2",
    (
        "en",
        "compact",
    ): "2e1c76a3d0e8648a4aeed34fa0544812cfd9527d024b3aaecc4b0f3ca3bc9aff",
    (
        "es",
        "classic",
    ): "6f8f571cb25ee9b90d83aaa489e5c3da844e41ba7685a0880b19b823e33ce509",
    (
        "es",
        "modern",
    ): "679fccbbe783475fa46e8c3756ad796a40895d057414dc7413a5d50697a1add1",
    (
        "es",
        "compact",
    ): "9de6a236d61b1d3b57734c853c4c9477f156e2112dad3d36fec20d56b256ec09",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slot(lang: str) -> U.Slot:
    return U.Slot(f"AI_{lang.upper()}", "AI", lang)


@pytest.mark.parametrize(("lang", "template"), sorted(GOLDEN))
def test_render_from_the_source_is_byte_identical_to_the_starter_render(
    tmp_path: Path, lang: str, template: str
) -> None:
    source = starter.starter_source(ANSWERS, _slot(lang), template)
    via_source = tmp_path / "source.pdf"
    render_source(source, via_source, created=WHEN)
    assert _sha(via_source) == GOLDEN[(lang, template)]
    legacy = tmp_path / "legacy.pdf"
    render_starter_cv(
        starter.starter_cv(ANSWERS, _slot(lang)),
        template,  # type: ignore[arg-type]
        legacy,
        created=WHEN,
    )
    assert _sha(legacy) == GOLDEN[(lang, template)]


def test_every_item_points_at_a_setup_or_identity_claim() -> None:
    source = starter.starter_source(ANSWERS, _slot("es"), "modern")
    ids = {c["id"] for c in setup_claims(ANSWERS, "en")} | {"identity-contact"}
    assert source.identity_claim_id == "identity-contact"
    assert set(source.claim_ids()) <= ids
    assert [s.kind for s in source.sections] == list(S.SECTION_KINDS)
    by_kind = {s.kind: s for s in source.sections}
    assert [i.claim_id for i in by_kind["skills"].items] == [
        "setup-skill-python",
        "setup-skill-sql",
        "setup-skill-fastapi",
    ]
    assert [i.text for i in by_kind["languages"].items] == [
        "Español — Nativo",
        "Inglés — C1",
    ]
    assert [i.claim_id for i in by_kind["languages"].items] == [
        "setup-lang-es",
        "setup-lang-en",
    ]
    (exp,) = by_kind["experience"].items
    assert (exp.claim_id, exp.text, exp.bold) == (
        "setup-experience",
        "Data Analyst · Acme",
        "Data Analyst",
    )
    assert by_kind["profile"].items[0].claim_id == "setup-experience"
    assert len(by_kind["education"].items) == 2
    assert source.template == "modern" and source.slot == "AI_ES"


def test_without_experience_the_profile_line_rests_on_the_identity_claim() -> None:
    answers = {**ANSWERS, "exp_years": None, "last_role": None}
    source = starter.starter_source(answers, _slot("en"), "classic")
    assert source.section("experience") is None
    profile = source.section("profile")
    assert profile is not None
    assert profile.items[0].claim_id == "identity-contact"


def test_source_json_round_trips_and_bad_files_are_refused() -> None:
    source = starter.starter_source(ANSWERS, _slot("en"), "compact")
    assert S.parse_source(source.to_json()) == source
    data = json.loads(source.to_json())
    assert data["version"] == S.SOURCE_VERSION
    for broken in (
        "not json",
        "[]",
        json.dumps({**data, "version": 99}),
        json.dumps({**data, "slot": "ai-es"}),
        json.dumps({**data, "sections": [{"kind": "hobbies", "items": []}]}),
        json.dumps(
            {**data, "sections": [{"kind": "skills", "items": [{"text": "x"}]}]}
        ),
    ):
        with pytest.raises(S.CvSourceError):
            S.parse_source(broken)


def _confirmed_created(tmp_path: Path, template: str = "modern") -> Path:
    """``/setup`` with "Create one with lazyhunt" for AI · ES, then Confirm."""
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("create")
    d.press(f"tpl:{template}")
    d.press("skip_all")
    d.press("skip_all_yes")
    d.press("confirm")
    return profile


def test_confirm_stores_the_source_next_to_the_pdf_and_it_is_adaptable(
    tmp_path: Path,
) -> None:
    profile = _confirmed_created(tmp_path)
    base = profile / "cv_base_pdfs"
    path = S.source_path(base, "AI_ES")
    assert path.is_file()
    source = S.adaptable_source(profile, "AI_ES")
    assert source is not None
    assert source.template == "modern" and source.name == "José Pérez"
    manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    entry = manifest["variants"]["AI_ES"]
    assert set(entry) == {"file", "sha256"}  # the manifest format is unchanged
    assert source.pdf_sha256 == entry["sha256"]
    # Every claim id it uses is in the claims.json Confirm generated.
    claims = {c["id"] for c in json.loads((profile / "claims.json").read_text())}
    assert set(source.claim_ids()) <= claims
    # Nothing pending is left behind once Confirm succeeded.
    assert not (profile / U.PENDING_DIR / "AI_ES.source.json").exists()


def test_a_source_is_not_adaptable_once_its_pdf_or_a_claim_changes(
    tmp_path: Path,
) -> None:
    profile = _confirmed_created(tmp_path)
    base = profile / "cv_base_pdfs"
    assert S.adaptable_source(profile, "AI_ES") is not None
    claims_path = profile / "claims.json"
    kept = claims_path.read_text("utf-8")
    claims = [c for c in json.loads(kept) if c["id"] != "setup-skill-python"]
    claims_path.write_text(json.dumps(claims), "utf-8")
    assert S.adaptable_source(profile, "AI_ES") is None  # stale, no exception
    claims_path.write_text(kept, "utf-8")
    assert S.adaptable_source(profile, "AI_ES") is not None
    pdf = next(base.glob("CV_*.pdf"))
    pdf.write_bytes(pdf.read_bytes() + b"\n")  # no longer the manifest's PDF
    assert S.adaptable_source(profile, "AI_ES") is None
    S.source_path(base, "AI_ES").write_text("{broken", "utf-8")
    assert S.adaptable_source(profile, "AI_ES") is None


def test_uploaded_legacy_and_sourceless_slots_are_not_adaptable(
    tmp_path: Path,
) -> None:
    assert S.adaptable_source(None, "AI_ES") is None  # the owner's legacy install
    demo = Path(__file__).resolve().parents[1] / "profiles" / "demo"
    copy = tmp_path / "demo"
    shutil.copytree(demo, copy)
    assert S.adaptable_source(copy, "AI_ES") is None  # hand-made base PDFs


def test_uploading_over_a_created_cv_drops_its_source_on_confirm(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("create")
    d.press("tpl:classic")
    assert (profile / U.PENDING_DIR / "AI_ES.source.json").is_file()
    d.press("pick:AI_ES")
    taken, _ = _send(d, FakeDocument(_pdf()))
    assert taken
    assert not (profile / U.PENDING_DIR / "AI_ES.source.json").exists()
    d.press("skip_all")
    d.press("skip_all_yes")
    d.press("confirm")
    assert not S.source_path(profile / "cv_base_pdfs", "AI_ES").exists()
    assert S.adaptable_source(profile, "AI_ES") is None
