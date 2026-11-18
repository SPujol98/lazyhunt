"""demo-profile P1: the synthetic persona "Alex Rivera" under ``profiles/demo``.

Everything runs with ``profile_dir = profiles/demo``: settings, claims, masters,
prompts, CV renders and the base PDF manifest. A guard also checks that no
string of the repository owner appears anywhere under the profile directory.
"""

import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from cva.claimsdb import ClaimsDB, load_claims
from cva.config import Settings
from cva.cvgen import CvVariant, iter_leaves, load_master
from cva.cvgen.base_pdfs import deliverable_variants, verified_base_pdf
from cva.cvgen.engine import render
from cva.cvgen.plan import neutral_plan
from cva.cvgen.validators import extract_text
from cva.match.cover_letter import build_cover_letter_prompt, owner_name
from cva.match.extraction import OfferExtraction, Requirement
from cva.match.judge import build_claims_digest, build_system_prompt
from cva.match.scorer import SynonymMap, load_synonyms, score
from cva.profile_identity import load_identity, require_identity_claim
from cva.profile_paths import resolve_profile_paths
from cva.profile_persona import load_judge_persona
from cva.profile_prefs import load_preferences
from cva.service import JobService
from cva.star import star_candidates
from cva.suggest import suggest_searches
from tests.demo import DEMO_DIR
from tests.pii import generic_pii_hits

_ROOT = Path(__file__).resolve().parent.parent
_DEMO = DEMO_DIR
_DELIVERABLE = {
    "AI_ES": "CV_AI_Engineer_Alex_Rivera.pdf",
    "AI_EN": "CV_AI_Engineer_Alex_Rivera_EN.pdf",
    "AI_CA": "CV_AI_Engineer_Alex_Rivera_CA.pdf",
    "BACKEND_ES": "CV_Backend_Developer_Python_Alex_Rivera.pdf",
    "BACKEND_EN": "CV_Backend_Developer_Python_Alex_Rivera_EN.pdf",
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        CVA_TELEGRAM_TOKEN="123456:TEST",
        CVA_LLM_API_KEY="sk-test",
        _env_file=None,
        toml_file=str(tmp_path / "absent.toml"),
        profile_dir=_DEMO,  # type: ignore[arg-type]
    )


@pytest.fixture(scope="module")
def db() -> ClaimsDB:
    return load_claims(_DEMO / "claims.json")


@pytest.fixture(scope="module")
def syn(db: ClaimsDB) -> SynonymMap:
    return load_synonyms(_DEMO / "synonyms.json", db)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_demo_pdfs", _ROOT / "scripts" / "build_demo_pdfs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- settings, claims, synonyms ------------------------------------------------


def test_settings_load_a_service_from_the_demo_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CVA_PROFILE_DIR", raising=False)

    service = JobService.from_settings(_settings(tmp_path), db_path=tmp_path / "d.db")

    assert service._paths == resolve_profile_paths(_DEMO)
    assert service._db.by_id["identity-contact"].text.startswith("Alex Rivera")
    assert service._salary_table, "the example salary table must parse"
    assert service._voice_sample().strip()
    assert service.preferences.search_targets.locations


def test_claims_validate_and_identity_is_alex(db: ClaimsDB) -> None:
    identity = load_identity(_DEMO / "profile.toml")

    require_identity_claim(identity, db)
    assert owner_name(db, identity) == "Alex Rivera"
    assert identity.file_slug == "Alex_Rivera"
    for claim_id in identity.contact_claim_ids:
        assert claim_id in db.by_id, claim_id
    assert {c.category for c in db.claims} >= {
        "identity",
        "education",
        "experience",
        "project",
        "skill",
        "language",
        "soft",
    }


def test_every_spanish_claim_has_an_english_twin(db: ClaimsDB) -> None:
    for claim in db.claims:
        if claim.id.endswith(("_en", "_ca", "-ds")):
            continue
        if claim.category == "skill" and claim.id.startswith("skill-"):
            continue  # skill labels are language-neutral; twins only where needed
        assert f"{claim.id}_en" in db.by_id, f"{claim.id} has no _en twin"


def test_synonyms_are_consistent_with_alex_claims(
    db: ClaimsDB, syn: SynonymMap
) -> None:
    assert syn.entries
    for alias, entry in syn.entries.items():
        assert entry.claim_id in db.by_id, alias


def test_alex_scores_his_own_stack_and_not_the_honest_gaps(
    db: ClaimsDB, syn: SynonymMap
) -> None:
    def status(name: str) -> str:
        offer = OfferExtraction(
            title="probe",
            company="probe",
            requirements=[
                Requirement(name=name, category="skill", must_have=True, evidence=name)
            ],
        )
        return score(offer, db, syn).breakdown[0].status

    for covered in ("python", "sql", "fastapi", "rag", "pandas"):
        assert status(covered) == "✓", covered
    gaps = _DEMO / "honest_gaps.md"
    assert "test_honest_gap_scores_zero" in gaps.read_text(encoding="utf-8")


def _registered_gaps() -> list[str]:
    """Every concept the demo ``honest_gaps.md`` registry lists (backticked names
    before the ``—`` of each section paragraph)."""
    text = (_DEMO / "honest_gaps.md").read_text(encoding="utf-8")
    sections = text.split("\n## ")[1:]
    gaps: list[str] = []
    for section in sections:
        body = section.split("\n", 1)[1]
        for paragraph in re.split(r"\n\s*\n", body.strip()):
            head = " ".join(paragraph.split()).split("—", 1)[0]
            gaps += re.findall(r"`([^`]+)`", head)
    return gaps


def test_the_registry_parser_finds_every_listed_gap() -> None:
    gaps = _registered_gaps()
    assert len(gaps) >= 50
    assert {"kubernetes", "ml", "ownership", "pyspark", "big data", "jupyter"} <= set(
        gaps
    )
    assert "skill-mypy" not in gaps  # a reason, not a gap


@pytest.mark.parametrize("gap", _registered_gaps())
def test_every_registered_honest_gap_scores_zero_for_alex(
    gap: str, db: ClaimsDB, syn: SynonymMap
) -> None:
    for must_have in (True, False):
        offer = OfferExtraction(
            title="probe",
            company="probe",
            requirements=[
                Requirement(
                    name=gap, category="skill", must_have=must_have, evidence=gap
                )
            ],
        )
        assert score(offer, db, syn).breakdown[0].status == "✗", gap


def test_engine_helpers_work_on_alex_claims(db: ClaimsDB) -> None:
    stories = star_candidates(db)
    keys = [s.key for s in stories]
    assert "exp-acme" in keys and "exp-northwind" in keys
    assert any(k.startswith("proj-") for k in keys)

    prefs = load_preferences(_DEMO / "preferences.toml")
    searches = suggest_searches(db, prefs.search_targets)
    assert searches
    assert {s.filter_profile for s in searches} == {"ai", "backend"}


# --- prompts --------------------------------------------------------------------


def test_judge_prompt_renders_with_alex_facts(db: ClaimsDB) -> None:
    persona = load_judge_persona(_DEMO / "judge")

    prompt = build_system_prompt(db, persona=persona)

    assert "Acme Analytics" in prompt
    assert persona.facts in prompt
    assert build_claims_digest(db) in prompt


def test_cover_letter_prompt_renders_with_alex_facts(
    db: ClaimsDB, tmp_path: Path
) -> None:
    prefs = load_preferences(_DEMO / "preferences.toml")
    identity = load_identity(_DEMO / "profile.toml")
    voice = (_DEMO / "voice.md").read_text(encoding="utf-8")

    system, user = build_cover_letter_prompt(
        offer_text="Junior data engineer, Python and SQL.",
        title="Data Engineer",
        company="Example Corp",
        language="en",
        branch="AI",
        claims_digest=build_claims_digest(db),
        apply_findings=[],
        voice_sample=voice,
        owner_name=owner_name(db, identity),
        persona=prefs.cover_letter,
    )

    assert "Candidate name (signature): Alex Rivera" in user
    assert "Acme Analytics" in user and "Northwind Logistics" in user
    assert voice.strip() in user
    assert "AI branch" in system


# --- masters and CV renders -------------------------------------------------------


_TAGLINE = {
    CvVariant.AI_ES: "AI Engineer | LLMs",
    CvVariant.AI_EN: "AI Engineer | LLMs",
    CvVariant.AI_CA: "AI Engineer | LLMs",
    CvVariant.DS_ES: "Data Scientist | Python",
    CvVariant.DS_EN: "Data Scientist | Python",
    CvVariant.BACKEND_ES: "Python Backend Developer | FastAPI",
    CvVariant.BACKEND_EN: "Python Backend Developer | FastAPI",
}
_REGION = {"ES": "España", "EN": "Spain", "CA": "Espanya"}


@pytest.mark.parametrize("variant", list(CvVariant), ids=lambda v: v.value)
def test_cv_renders_for_each_variant(
    variant: CvVariant, db: ClaimsDB, tmp_path: Path
) -> None:
    master = load_master(variant, _DEMO / "masters", db)
    identity = load_identity(_DEMO / "profile.toml")

    result = render(
        master,
        neutral_plan(variant),
        out=tmp_path / f"{variant.value}.pdf",
        db=db,
        identity=identity,
    )

    assert result.violations == []
    assert result.pages == 1
    text = extract_text(result.pdf_path)
    assert "ALEX RIVERA" in text
    assert "Acme Analytics" in text and "Northwind Logistics" in text
    assert _TAGLINE[variant] in text
    assert _REGION[variant.value.rsplit("_", 1)[1]] in text
    assert len(iter_leaves(master)) > 10


# --- base PDFs + manifest ----------------------------------------------------------


def test_manifest_lists_the_five_deliverables_with_matching_sha256() -> None:
    base = _DEMO / "cv_base_pdfs"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))

    assert {v: e["file"] for v, e in manifest["variants"].items()} == _DELIVERABLE
    assert deliverable_variants(base) == {CvVariant(v) for v in _DELIVERABLE}
    for variant, entry in manifest["variants"].items():
        pdf = base / entry["file"]
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == entry["sha256"]
        assert verified_base_pdf(CvVariant(variant), base) == pdf
    on_disk = {p.name for p in base.glob("*.pdf")}
    assert on_disk == set(_DELIVERABLE.values())


def test_manifest_records_the_fpdf2_and_font_environment() -> None:
    manifest = json.loads(
        (_DEMO / "cv_base_pdfs" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["environment"] == _script().environment()
    assert manifest["environment"]["fonts"], "the font files must be pinned"


def test_base_pdfs_regenerate_to_the_manifest_hashes(tmp_path: Path) -> None:
    script = _script()
    manifest = json.loads(
        (_DEMO / "cv_base_pdfs" / "manifest.json").read_text(encoding="utf-8")
    )
    if manifest["environment"] != script.environment():
        pytest.skip(
            "fpdf2/font environment differs from the one recorded in manifest.json "
            f"({manifest['environment']['fpdf2']}): bytes are not comparable"
        )

    built = script.build_all(_DEMO, tmp_path)

    assert built == manifest["variants"]


def _profile_copy(tmp_path: Path) -> Path:
    profile = tmp_path / "profile"
    shutil.copytree(_DEMO, profile, ignore=shutil.ignore_patterns("eval"))
    return profile


def test_script_check_passes_on_the_committed_pdfs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _script().main(["--check"]) == 0
    assert "match" in capsys.readouterr().out


def test_script_check_fails_on_a_tampered_committed_pdf(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile_copy(tmp_path)
    pdf = profile / "cv_base_pdfs" / _DELIVERABLE["AI_ES"]
    pdf.write_bytes(pdf.read_bytes() + b"\n%tampered")

    assert _script().main(["--check", "--profile-dir", str(profile)]) == 1
    assert f"{_DELIVERABLE['AI_ES']}: sha256 differs" in capsys.readouterr().err


def test_script_check_fails_on_unlisted_and_missing_pdfs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile_copy(tmp_path)
    base = profile / "cv_base_pdfs"
    (base / "CV_extra.pdf").write_bytes(b"%PDF-1.4")
    (base / _DELIVERABLE["BACKEND_EN"]).unlink()

    assert _script().main(["--check", "--profile-dir", str(profile)]) == 1
    err = capsys.readouterr().err
    assert "CV_extra.pdf: on disk but not listed" in err
    assert f"{_DELIVERABLE['BACKEND_EN']}: listed in the manifest but missing" in err


def test_script_check_reports_a_missing_manifest_without_a_traceback(
    tmp_path: Path,
) -> None:
    profile = _profile_copy(tmp_path)
    (profile / "cv_base_pdfs" / "manifest.json").unlink()

    with pytest.raises(SystemExit) as excinfo:
        _script().main(["--check", "--profile-dir", str(profile)])

    assert "manifest missing or unreadable" in str(excinfo.value)


def test_script_check_skips_regeneration_when_the_environment_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile_copy(tmp_path)
    path = profile / "cv_base_pdfs" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["environment"]["fpdf2"] = "0.0.0"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert _script().main(["--check", "--profile-dir", str(profile)]) == 0
    assert "regeneration skipped" in capsys.readouterr().err


# --- masters/claims agree with the judge facts -------------------------------------

_TWO_YEARS = re.compile(r"(?<![\w])(?:dos años|two years|2 years)", re.IGNORECASE)
_HEDGE = re.compile(r"(casi|nearly|almost|menos de|under)\s+$", re.IGNORECASE)


def test_total_experience_wording_never_contradicts_the_judge_facts(
    db: ClaimsDB,
) -> None:
    facts = (_DEMO / "judge" / "facts.md").read_text(encoding="utf-8")
    assert "under 2 years" in facts, "the persona must stay below two years"
    texts = [c.text for c in db.claims]
    for master in sorted((_DEMO / "masters").glob("*.json")):
        texts.append(master.read_text(encoding="utf-8"))
    offenders = [
        text[max(0, m.start() - 40) : m.end() + 20]
        for text in texts
        for m in _TWO_YEARS.finditer(text)
        if not _HEDGE.search(text[: m.start()])
    ]
    assert offenders == []


# --- no PII ---------------------------------------------------------------------------
# The owner's own identifiers are checked in tests/owner/test_pii_owner.py; this
# public guard states only generic patterns.


def test_demo_profile_holds_no_real_looking_personal_data() -> None:
    assert _DEMO.is_dir()
    hits: list[str] = []
    for path in sorted(_DEMO.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        texts = [path.relative_to(_DEMO).as_posix(), data.decode("utf-8", "ignore")]
        if path.suffix == ".pdf":
            # Compressed streams are noise to a raw scan; the rendered text counts.
            texts = texts[:1] + [extract_text(path)]
        for text in texts:
            hits += [f"{path.name}: {hit}" for hit in generic_pii_hits(text)]
    assert hits == []


def test_the_generic_pii_patterns_catch_what_they_claim() -> None:
    assert generic_pii_hits("write to jane.doe@gmail.com") == [
        "email: jane.doe@gmail.com"
    ]
    assert generic_pii_hits("alex.rivera@example.com, +34 600 000 000") == []
    assert generic_pii_hits("call +34 612 345 678") == ["phone: +34 612 345 678"]
    assert generic_pii_hits("https://x.example/p?utm_source=a&gclid=1")
    assert generic_pii_hits("plain text with 2024 and 12345") == []


def test_demo_files_exist() -> None:
    expected = [
        "profile.toml",
        "preferences.toml",
        "claims.json",
        "synonyms.json",
        "voice.md",
        "salary_reference.toml",
        "honest_gaps.md",
        "judge/facts.md",
        "judge/anchors.md",
        "judge/rubric_persona.md",
    ]
    assert [name for name in expected if not (_DEMO / name).is_file()] == []
    assert sorted(p.stem for p in (_DEMO / "masters").glob("*.json")) == sorted(
        v.value for v in CvVariant
    )
