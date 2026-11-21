"""Starter CVs (beta3-ux T4): one-page ATS-friendly PDFs from the answers only."""

from pathlib import Path

import pytest
from pypdf import PdfReader

from cva.cvgen.master import CvgenError
from cva.cvgen.starter import TEMPLATES, StarterCv, render_starter_cv, sections_of
from cva.onboarding import cv_upload as U
from cva.onboarding.starter import letter_texts, starter_cv

ANSWERS = {
    "name": "Alex Rivera",
    "email": "alex.rivera@example.com",
    "city": "Barcelona",
    "linkedin": "https://linkedin.com/in/alex-rivera-example",
    "families": ["ai"],
    "level": "junior",
    "spoken_langs": ["es", "en"],
    "spoken_levels": {"es": "native", "en": "C1"},
    "skills": ["Python", "SQL", "FastAPI"],
    "exp_years": "1-2",
    "last_role": None,
    "education": ["Data and AI Bootcamp — Northgate Tech Academy"],
}


def _text(path: Path) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("lang", ["en", "es", "ca"])
def test_each_template_is_a_valid_one_page_pdf_with_only_the_answers(
    tmp_path: Path, template: str, lang: str
) -> None:
    cv = starter_cv(ANSWERS, U.Slot(f"AI_{lang.upper()}", "AI", lang))
    out = tmp_path / f"{template}.pdf"
    pages = render_starter_cv(cv, template, out)  # type: ignore[arg-type]
    assert pages == 1 and len(PdfReader(out).pages) == 1
    text = _text(out)
    for given in ("Alex Rivera", "AI Engineer", "Python", "FastAPI", "Barcelona"):
        assert given in text
    assert "alex.rivera@example.com" in text
    assert "Northgate Tech Academy" in text
    # Not given: no phone, no last role -> no Experience section, no projects.
    assert sections_of(cv) == ["profile", "education", "skills", "languages"]
    for heading in ("Experience", "Experiencia", "Experiència", "Projects"):
        assert heading not in text
    assert "+34" not in text


def test_empty_sections_are_left_out(tmp_path: Path) -> None:
    cv = StarterCv(name="Alex Rivera", headline="Data Analyst", lang="en")
    out = tmp_path / "bare.pdf"
    render_starter_cv(cv, "classic", out)
    text = _text(out)
    assert "Alex Rivera" in text and "Data Analyst" in text
    for heading in ("PROFILE", "EDUCATION", "SKILLS", "LANGUAGES", "EXPERIENCE"):
        assert heading not in text


def test_a_cv_needs_a_name_and_a_known_template(tmp_path: Path) -> None:
    with pytest.raises(CvgenError):
        render_starter_cv(
            StarterCv(name=" ", headline="x", lang="en"), "classic", tmp_path / "a.pdf"
        )
    with pytest.raises(CvgenError):
        render_starter_cv(
            StarterCv(name="A", headline="x", lang="en"), "fancy", tmp_path / "b.pdf"
        )  # type: ignore[arg-type]


def test_letter_texts_say_only_what_was_answered() -> None:
    tables = letter_texts(ANSWERS)
    en = tables["en"]
    assert en["signoff"] == "Best regards,\nAlex Rivera"
    assert "Python, SQL, FastAPI" in en["paragraph1"]
    assert "last role" not in en["paragraph1"]  # none given
    assert en["salutation"] == "Dear {company} team,"
    assert "{company_de}" in tables["ca"]["salutation"]
