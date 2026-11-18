"""fpdf2 render engine + validators (cv-generation spec; design D9, v3 part B).

Real owner-engine port throughout: every render writes a PDF with the
frozen format (faithful port of ``cvgen/generate.py``; Carlito by default) to
``tmp_path``. The variant matrix renders the real masters (legacy since
fixed-base-cvs: the engine no longer drives delivery).
"""

import re
import unicodedata
from pathlib import Path

import pytest
from fpdf import FPDF
from pypdf import PdfReader

from cva.claimsdb import load_claims, whitelist_violations
from cva.cvgen import CvVariant, load_master
from cva.cvgen.engine import output_path, render, tailored_output_path
from cva.cvgen.plan import SelectionPlan, neutral_plan
from cva.cvgen.validators import check_contact, check_page_count, extract_text
from cva.profile_identity import DEFAULT_IDENTITY
from tests.demo import DEMO_CLAIMS, DEMO_MASTERS

REAL_MASTERS = DEMO_MASTERS
CV_TEMPLATE = Path("cv_template")
SLUG = DEFAULT_IDENTITY.file_slug
CLAIMS_PATH = DEMO_CLAIMS


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def db() -> object:
    return load_claims(CLAIMS_PATH)


@pytest.fixture(scope="module")
def masters(db: object) -> dict[str, object]:
    return {v.value: load_master(v, REAL_MASTERS, db) for v in CvVariant}


def _render_to(
    tmp_path: Path, name: str, master: object, plan: SelectionPlan, db: object
):
    out = tmp_path / name
    return out, render(master, plan, out=out, db=db)  # type: ignore[arg-type]


def test_render_fixture_master_smoke(tmp_path: Path, db: object) -> None:
    master = load_master(CvVariant.AI_ES, DEMO_MASTERS, db)
    out, result = _render_to(
        tmp_path, "smoke.pdf", master, neutral_plan(CvVariant.AI_ES), db
    )
    assert out.is_file() and out.stat().st_size > 1000
    assert result.pdf_path == out
    assert result.pages == 1
    assert result.violations == []


@pytest.mark.parametrize(
    "variant",
    [
        CvVariant.AI_ES,
        CvVariant.AI_EN,
        CvVariant.DS_ES,
        CvVariant.DS_EN,
        CvVariant.AI_CA,
    ],
)
def test_five_variant_matrix_each_renders_valid_one_page_pdf(
    variant: CvVariant, masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters[variant.value]
    _out, result = _render_to(
        tmp_path, f"{variant.value}.pdf", master, neutral_plan(variant), db
    )
    assert result.pages == 1
    assert result.violations == []
    text = extract_text(result.pdf_path)
    assert "ALEX RIVERA" in _collapse(text)  # frozen format: uppercase name
    assert whitelist_violations(text, db, master=master) == []  # type: ignore[arg-type]
    assert check_contact(result.pdf_path, master) == []  # type: ignore[arg-type]


def test_ca_variant_renders_catalan_content(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """AI_CA (v3 part A master) renders through the new engine end-to-end."""
    master = masters["AI_CA"]
    _out, result = _render_to(
        tmp_path, "ca.pdf", master, neutral_plan(CvVariant.AI_CA), db
    )
    text = _collapse(extract_text(result.pdf_path))
    assert result.pages == 1
    assert result.violations == []
    assert "PERFIL PROFESSIONAL" in text  # frozen format: uppercase headers
    assert "Enginyer de dades i backend" in text
    assert "Barcelona, Espanya" in text


# Content-parity tests against cv_base_pdfs/ were retired in fixed-base-cvs:
# the bot no longer renders masters for delivery and the base PDFs now come
# from the owner's hand-reviewed generator (v4), so they intentionally differ
# from the legacy masters. cv_base_pdfs/ integrity lives in test_sync_base_cvs.py.


# --- Plan semantics ------------------------------------------------------------


def test_neutral_plan_is_identity_preserving_master_order(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    _, result = _render_to(
        tmp_path, "neutral.pdf", master, neutral_plan(CvVariant.AI_ES), db
    )
    text = _collapse(extract_text(result.pdf_path))
    rag = text.index("DocSearch")
    cmm = text.index("Metrics API")
    assert rag < cmm  # master order preserved, no reordering
    assert "Fly-in" not in text  # v2: Fly-in removed from masters


def test_promote_moves_referenced_blocks_first(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=["proj-metrics-api-overview"],
        demote=[],
        emphasis={},
        tagline_keywords=[],
    )
    _, result = _render_to(tmp_path, "promote.pdf", master, plan, db)
    text = _collapse(extract_text(result.pdf_path))
    assert text.index("Metrics API") < text.index("DocSearch")
    assert result.violations == []


def test_demote_moves_referenced_blocks_last(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=["proj-rag-overview"],
        emphasis={},
        tagline_keywords=[],
    )
    _, result = _render_to(tmp_path, "demote.pdf", master, plan, db)
    text = _collapse(extract_text(result.pdf_path))
    assert text.index("Metrics API") < text.index("DocSearch")
    assert result.violations == []


def test_emphasis_is_styling_only_text_and_validators_unchanged(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    _, neutral = _render_to(
        tmp_path, "n.pdf", master, neutral_plan(CvVariant.AI_ES), db
    )
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=[],
        emphasis={"identity-linkedin-headline": "lead with this"},
        tagline_keywords=[],
    )
    _, emphasized = _render_to(tmp_path, "e.pdf", master, plan, db)
    assert _collapse(extract_text(emphasized.pdf_path)) == _collapse(
        extract_text(neutral.pdf_path)
    )
    assert emphasized.pages == 1
    assert emphasized.violations == []


def _worst_case_emphasis_ids(master: object) -> set[str]:
    """Every claim id at an engine bold site (design D3/D7): experience
    bullets, project bullets, the FIRST education entry's bullets, and
    certification lines — bolding every one of them is the worst case for
    the one-page invariant."""
    ids: set[str] = set()
    experience = master.sections.get("experience")  # type: ignore[attr-defined]
    if experience is not None:
        for entry in experience.entries:
            ids.update(bullet.claim_id for bullet in entry.bullets)
    projects = master.sections.get("projects")  # type: ignore[attr-defined]
    if projects is not None:
        for project in projects.projects:
            ids.update(bullet.claim_id for bullet in project.bullets)
    education = master.sections.get("education")  # type: ignore[attr-defined]
    if education is not None and education.entries:
        ids.update(bullet.claim_id for bullet in education.entries[0].bullets)
    certifications = master.sections.get("certifications")  # type: ignore[attr-defined]
    if certifications is not None:
        ids.update(leaf.claim_id for leaf in certifications.certs)
    return ids


@pytest.mark.parametrize("variant", list(CvVariant))
def test_worst_case_emphasis_keeps_every_shipped_master_one_page(
    variant: CvVariant, masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """D7 evidence: bolding EVERY eligible bold site on the real shipped
    master still renders exactly 1 page with no validator violations. If a
    master ever overflows here, the D7 fallback (exercised through the
    real ``JobService.generate_cv`` path, never a hand-written retry) MUST
    still recover it to 1 page — see ``tests/test_service.py``'s overflow
    fallback tests for that path."""
    master = masters[variant.value]
    ids = _worst_case_emphasis_ids(master)
    assert ids  # sanity: every shipped master has at least one bold site
    plan = SelectionPlan(
        variant=variant,
        promote=[],
        demote=[],
        emphasis=dict.fromkeys(ids, "worst-case"),
        tagline_keywords=[],
    )
    _out, result = _render_to(
        tmp_path, f"worstcase-{variant.value}.pdf", master, plan, db
    )
    assert result.pages == 1, (
        f"{variant}: worst-case emphasis overflowed to {result.pages} pages — "
        "the D7 fallback must be proven via JobService.generate_cv, not here"
    )
    assert result.violations == []


def test_tagline_keywords_append_claim_text_to_tagline(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    plan = SelectionPlan(
        variant=CvVariant.AI_ES,
        promote=[],
        demote=[],
        emphasis={},
        tagline_keywords=["skill-llms"],  # the AI/ML skills-row leaf anchor
    )
    _, result = _render_to(tmp_path, "kw.pdf", master, plan, db)
    text = _collapse(extract_text(result.pdf_path))
    assert "Python & SQL · LLMs" in text
    assert result.violations == []


# --- Validators / invariants ---------------------------------------------------


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_cv_template_byte_identical_across_any_number_of_renders(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """cv_template/ stays read-only (reference only since the engine swap)."""
    before = _tree_snapshot(CV_TEMPLATE)
    master = masters["AI_ES"]
    for index, plan in enumerate(
        [
            neutral_plan(CvVariant.AI_ES),
            SelectionPlan(
                variant=CvVariant.AI_ES,
                promote=["proj-metrics-api-overview"],
                demote=["proj-rag-overview"],
                emphasis={"skill-llms": "x"},
                tagline_keywords=["skill-sql"],
            ),
            neutral_plan(CvVariant.AI_ES),
        ]
    ):
        _render_to(tmp_path, f"t{index}.pdf", master, plan, db)
    assert _tree_snapshot(CV_TEMPLATE) == before


def test_two_page_render_blocked_with_page_count_violation(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """The page gate still blocks a 2-page PDF.

    The frozen engine renders 1 page BY CONSTRUCTION (no auto page break —
    owner format constant), so the gate is proven against a real 2-page
    PDF built directly with fpdf2.
    """
    fat = masters["AI_ES"].model_copy(deep=True)
    fat.sections["summary"].paragraphs = fat.sections["summary"].paragraphs * 12
    _, result = _render_to(tmp_path, "fat.pdf", fat, neutral_plan(CvVariant.AI_ES), db)
    assert result.pages == 1  # frozen engine: overflow clips, never spills
    assert result.violations == []
    two_pager = tmp_path / "two.pdf"
    pdf = FPDF(unit="mm", format="A4")
    pdf.add_page()
    pdf.add_page()
    pdf.output(str(two_pager))
    violations = check_page_count(two_pager)
    assert len(violations) == 1
    assert "page-count" in violations[0]


def test_tampered_contact_block_blocks_delivery(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    original = masters["AI_ES"]
    tampered = original.model_copy(deep=True)
    # Swap the phone leaf for a different claim-clean number: the render is
    # whitelist-clean but no longer matches the master's contact block.
    phone_leaf = next(leaf for leaf in tampered.header.contact if leaf.text.startswith("+34"))
    phone_leaf.text = "+34 611 222 333"
    _, result = _render_to(
        tmp_path, "tampered.pdf", tampered, neutral_plan(CvVariant.AI_ES), db
    )
    assert result.violations == []  # tampered master renders clean on its own
    violations = check_contact(result.pdf_path, original)  # type: ignore[arg-type]
    assert violations != []
    assert any("contact" in violation for violation in violations)


# --- Output naming -------------------------------------------------------------


def test_output_naming_convention(tmp_path: Path) -> None:
    assert output_path("AI Engineer", "Acme", tmp_path) == tmp_path / (
        f"{SLUG}_AI_Engineer_Acme.pdf"
    )
    assert output_path("Data  Scientist", "Acme Corp", tmp_path) == tmp_path / (
        f"{SLUG}_Data_Scientist_Acme_Corp.pdf"
    )
    assert output_path("AI Engineer", "Acme", tmp_path).parent == tmp_path


def test_tailored_output_path_drops_parenthesised_title_noise(tmp_path: Path) -> None:
    """LinkedIn title markers like "(all genders welcome)" never reach the name."""
    assert tailored_output_path(
        "Junior AI Engineer (all genders welcome)", tmp_path
    ) == tmp_path / f"{SLUG}_Junior_AI_Engineer.pdf"
    assert tailored_output_path("Data Scientist [m/f/d] (Remote)", tmp_path) == (
        tmp_path / f"{SLUG}_Data_Scientist.pdf"
    )
    # A title that is only a parenthesised segment keeps its words.
    assert tailored_output_path("(AI Engineer)", tmp_path) == (
        tmp_path / f"{SLUG}_AI_Engineer.pdf"
    )


def test_tailored_output_path_strips_diacritics_and_spaces(tmp_path: Path) -> None:
    """v3 part B naming: adaptadas/<slug>_<tailored-role>.pdf."""
    assert tailored_output_path("Desenvolupador Python", tmp_path) == tmp_path / (
        f"{SLUG}_Desenvolupador_Python.pdf"
    )
    assert tailored_output_path("Ingeniería de Software", tmp_path) == tmp_path / (
        f"{SLUG}_Ingenieria_de_Software.pdf"
    )
    # Filesystem-hostile punctuation collapses into single underscores.
    assert tailored_output_path("  Backend   C++  ", tmp_path) == tmp_path / (
        f"{SLUG}_Backend_C.pdf"
    )


def test_render_with_role_title_writes_tailored_filename(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_CA"]
    out = tmp_path / "ignored.pdf"
    result = render(
        master,
        neutral_plan(CvVariant.AI_CA),
        out=out,
        db=db,
        role_title="Desenvolupador Python",
    )
    expected = tmp_path / f"{SLUG}_Desenvolupador_Python.pdf"
    assert result.pdf_path == expected
    assert expected.is_file()
    assert not out.exists()  # the generic name is never written
    assert result.pages == 1
    assert result.violations == []


def test_render_without_role_title_keeps_caller_path(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    master = masters["AI_ES"]
    out = tmp_path / f"{SLUG}_AI_Engineer_Acme.pdf"
    result = render(master, neutral_plan(CvVariant.AI_ES), out=out, db=db)
    assert result.pdf_path == out and out.is_file()


def test_render_refuses_mismatched_plan_variant(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    out = tmp_path / "x.pdf"
    with pytest.raises(Exception, match="variant"):
        render(masters["AI_ES"], neutral_plan(CvVariant.DS_ES), out=out, db=db)  # type: ignore[arg-type]
    assert not out.exists()  # nothing rendered on refusal


@pytest.mark.skipif(
    not Path("cvgen/fonts/calibri.ttf").is_file(),
    reason="proprietary Calibri files are not in this clone",
)
def test_calibri_fonts_embedded_from_cvgen_fonts(
    masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """Format fidelity (CAMBIOS §2): cv_font="calibri" embeds real Calibri."""
    master = masters["AI_ES"]
    result = render(
        master,  # type: ignore[arg-type]
        neutral_plan(CvVariant.AI_ES),
        out=tmp_path / "fonts.pdf",
        db=db,  # type: ignore[arg-type]
        font="calibri",
    )
    fonts: set[str] = set()
    resources = PdfReader(str(result.pdf_path)).pages[0]["/Resources"]
    for key in resources["/Font"]:
        base_font = str(resources["/Font"][key].get_object().get("/BaseFont", ""))
        fonts.add(base_font.split("+")[-1])
    assert any("Calibri" in font for font in fonts), fonts
    assert not any("Carlito" in font for font in fonts), fonts


def test_no_render_from_analyze_or_notification_path() -> None:
    """CV generation is manual-trigger-only (cv-generation spec)."""
    analyze_path_modules = list(Path("cva/match").rglob("*.py")) + list(
        Path("cva/ingest").rglob("*.py")
    )
    assert analyze_path_modules  # the scan really has modules to check
    for module in analyze_path_modules:
        assert "cvgen" not in module.read_text(encoding="utf-8"), module
    for module in Path("cva/cvgen").glob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "from cva.llm" not in source, module  # render is deterministic
        assert "httpx" not in source, module  # no network in render/validators


# Owner amendment (2026-08-30): EN masters must render 100% English. These
# Spanish content tokens (accent-stripped, token-exact) must never appear in
# an EN render; every one of them is present in the pre-amendment EN renders.
ES_LANGUAGE_MARKERS: frozenset[str] = frozenset(
    {
        "sobre",
        "ficheros",
        "ponderada",
        "similitud",
        "semantica",
        "codigo",
        "tokenizacion",
        "cobertura",
        "evaluacion",
        "preguntas",
        "migracion",
        "automatizacion",
        "historicos",
        "mantenimiento",
        "validacion",
        "datos",
        "intensivo",
        "ingenieria",
        "previsto",
        "algoritmos",
        "grado",
        "nativo",
        "invalido",
        "imposible",
        "diseno",
        "enrutado",
        "planificacion",
    }
)


def _accentless_tokens(text: str) -> set[str]:
    lowered = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return {token for token in re.split(r"[^a-z0-9]+", stripped) if token}


@pytest.mark.parametrize("variant", [CvVariant.AI_EN, CvVariant.DS_EN])
def test_en_renders_are_fully_english(
    variant: CvVariant, masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    _out, result = _render_to(
        tmp_path,
        f"{variant.value}-en.pdf",
        masters[variant.value],
        neutral_plan(variant),
        db,
    )
    tokens = _accentless_tokens(extract_text(result.pdf_path))
    spanish = tokens & ES_LANGUAGE_MARKERS
    assert spanish == set(), (
        f"{variant} render carries Spanish tokens: {sorted(spanish)}"
    )


@pytest.mark.parametrize("variant", [CvVariant.AI_EN, CvVariant.DS_EN])
def test_tampered_en_contact_block_still_gated(
    variant: CvVariant, masters: dict[str, object], db: object, tmp_path: Path
) -> None:
    """The contact gate must keep enforcing `_en` identity twins, not skip them."""
    original = masters[variant.value]
    tampered = original.model_copy(deep=True)
    phone_leaf = next(leaf for leaf in tampered.header.contact if leaf.text.startswith("+34"))  # type: ignore[union-attr]
    phone_leaf.text = "+34 611 222 333"
    _out, result = _render_to(
        tmp_path, f"tampered-{variant.value}.pdf", tampered, neutral_plan(variant), db
    )
    violations = check_contact(result.pdf_path, original)  # type: ignore[arg-type]
    assert violations != []
    assert any("contact" in violation for violation in violations)


# --- Education wording (cv-education-wording) ---------------------------------

_OLD_EDU_STRINGS = ("Todo bajo Linux/Bash", "Base Java + SQL")


def test_no_terse_education_wording_in_masters_fixtures_or_claims() -> None:
    paths = [
        *REAL_MASTERS.glob("*.json"),
        CLAIMS_PATH,
    ]
    assert paths
    offenders = [
        f"{path}: {old}"
        for path in paths
        for old in _OLD_EDU_STRINGS
        if old in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
