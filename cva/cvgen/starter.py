"""Starter CVs: a clean one-page PDF built only from the ``/setup`` answers.

``/setup`` offers "Create one with lazyhunt" for a base CV slot (beta3-ux T4):
the candidate's own answers (name, contact, target position, profile line,
last role, education, skills, spoken languages) drawn in one of
:data:`TEMPLATES`. The engine is the CVs' own: fpdf2 with the
``Settings.cv_font`` family (:func:`cva.cvgen.fonts.font_files`) and the
letter PDF's contact-line wrapping.

ATS-friendly by construction: one column, standard section headings in the
CV's language, real (selectable) text, no photo, no tables, no images. An
empty section is left out; nothing is added that the caller did not pass.
The templates differ in layout only:

=========  ==============================================================
template   layout
=========  ==============================================================
classic    centred header, small-caps headings over a full-width rule
modern     left header with the name in blue, blue headings with a marker
compact    smaller type and tight spacing: the most content per page
=========  ==============================================================

The section headings and the few fixed words (profile line, language
levels) are in the CV language (``es``/``en``/``ca``), like the letters'
month names (:mod:`cva.cvgen.letter_pdf`).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from cva.cvgen.engine import FAMILY
from cva.cvgen.fonts import DEFAULT_CV_FONT, CvFont, font_files
from cva.cvgen.letter_pdf import _contact_lines
from cva.cvgen.master import CvgenError

Template = Literal["classic", "modern", "compact"]
TEMPLATES: Final[tuple[Template, ...]] = ("classic", "modern", "compact")
PT_TO_MM: Final[float] = 0.352778

INK: Final[tuple[int, int, int]] = (25, 25, 25)
SLATE: Final[tuple[int, int, int]] = (75, 85, 99)
BLUE: Final[tuple[int, int, int]] = (18, 84, 171)
RULE: Final[tuple[int, int, int]] = (170, 170, 170)

SECTIONS: Final[tuple[str, ...]] = (
    "profile",
    "experience",
    "education",
    "skills",
    "languages",
)
HEADINGS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "profile": "Profile",
        "experience": "Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
    },
    "es": {
        "profile": "Perfil",
        "experience": "Experiencia",
        "education": "Formación",
        "skills": "Habilidades",
        "languages": "Idiomas",
    },
    "ca": {
        "profile": "Perfil",
        "experience": "Experiència",
        "education": "Formació",
        "skills": "Habilitats",
        "languages": "Idiomes",
    },
}

# --- fixed words in the CV language ------------------------------------------------

_LEVELS: Final[dict[str, dict[str, str]]] = {
    "en": {"junior": "Junior", "mid": "Mid-level", "senior": "Senior"},
    "es": {"junior": "junior", "mid": "nivel medio", "senior": "senior"},
    "ca": {"junior": "júnior", "mid": "nivell mitjà", "senior": "sènior"},
}
_YEARS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "lt1": "less than a year",
        "1-2": "1-2 years",
        "3-5": "3-5 years",
        "6-10": "6-10 years",
        "10plus": "more than 10 years",
    },
    "es": {
        "lt1": "menos de un año",
        "1-2": "1-2 años",
        "3-5": "3-5 años",
        "6-10": "6-10 años",
        "10plus": "más de 10 años",
    },
    "ca": {
        "lt1": "menys d'un any",
        "1-2": "1-2 anys",
        "3-5": "3-5 anys",
        "6-10": "6-10 anys",
        "10plus": "més de 10 anys",
    },
}
LANGUAGE_NAMES: Final[dict[str, dict[str, str]]] = {
    "en": {
        "es": "Spanish",
        "en": "English",
        "ca": "Catalan",
        "fr": "French",
        "de": "German",
        "it": "Italian",
        "pt": "Portuguese",
    },
    "es": {
        "es": "Español",
        "en": "Inglés",
        "ca": "Catalán",
        "fr": "Francés",
        "de": "Alemán",
        "it": "Italiano",
        "pt": "Portugués",
    },
    "ca": {
        "es": "Castellà",
        "en": "Anglès",
        "ca": "Català",
        "fr": "Francès",
        "de": "Alemany",
        "it": "Italià",
        "pt": "Portuguès",
    },
}
_NATIVE: Final[dict[str, str]] = {"en": "Native", "es": "Nativo", "ca": "Nadiu"}


def _words(table: dict[str, dict[str, str]], lang: str) -> dict[str, str]:
    return table.get(lang, table["en"])


def profile_line(headline: str, level: str | None, years: str | None, lang: str) -> str:
    """``Junior AI Engineer with 1-2 years of professional experience.``
    (``es``/``ca``: ``AI Engineer (junior) con 1-2 años de experiencia
    profesional.``); only the parts that were answered."""
    level_word = _words(_LEVELS, lang).get(level or "")
    years_word = _words(_YEARS, lang).get(years or "")
    if lang == "en":
        text = f"{level_word} {headline}" if level_word else headline
        if years_word:
            text += f" with {years_word} of professional experience"
        return text + "."
    text = f"{headline} ({level_word})" if level_word else headline
    if years_word:
        text += (
            f" amb {years_word} d'experiència professional"
            if lang == "ca"
            else f" con {years_word} de experiencia profesional"
        )
    return text + "."


def language_line(language: str, level: str, lang: str) -> str:
    """``English — C1``: a common language's name in the CV language (a
    typed one as typed), its CEFR level or "native"."""
    name = _words(LANGUAGE_NAMES, lang).get(language, language)
    shown = _NATIVE.get(lang, _NATIVE["en"]) if level == "native" else level
    return f"{name} — {shown}"


@dataclass(frozen=True)
class StarterCv:
    """What a starter CV says: every field comes from the caller as given."""

    name: str
    headline: str  # the target position
    lang: str  # the CV language (headings): ``es``/``en``/``ca``
    contact: tuple[str, ...] = ()
    profile: str | None = None
    experience: tuple[tuple[str, str | None], ...] = ()  # (role, company)
    education: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    languages: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class _Style:
    margin: float
    name_size: float
    name_color: tuple[int, int, int]
    headline_size: float
    contact_size: float
    heading_size: float
    heading_color: tuple[int, int, int]
    body_size: float
    centered: bool
    section_gap: float
    leading: float  # body line height factor


_STYLES: Final[dict[Template, _Style]] = {
    "classic": _Style(20.0, 20.0, INK, 11.5, 9.5, 10.5, INK, 10.5, True, 5.0, 1.4),
    "modern": _Style(18.0, 22.0, BLUE, 12.0, 9.5, 11.0, BLUE, 10.5, False, 5.5, 1.4),
    "compact": _Style(14.0, 16.0, INK, 10.0, 9.0, 9.5, INK, 9.5, False, 3.2, 1.3),
}


def _sections(cv: StarterCv) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """``(section id, lines)`` of the non-empty sections; a line is
    ``(text, bold part)``."""
    out: list[tuple[str, list[tuple[str, str | None]]]] = []
    if cv.profile:
        out.append(("profile", [(cv.profile, None)]))
    if cv.experience:
        out.append(
            (
                "experience",
                [
                    (f"{role} · {company}" if company else role, role)
                    for role, company in cv.experience
                ],
            )
        )
    if cv.education:
        out.append(("education", [(entry, None) for entry in cv.education]))
    if cv.skills:
        out.append(("skills", [(" · ".join(cv.skills), None)]))
    if cv.languages:
        out.append(("languages", [(" · ".join(cv.languages), None)]))
    return out


def _heading(pdf: FPDF, text: str, style: _Style, template: Template) -> None:
    width = pdf.w - 2 * style.margin
    size = style.heading_size
    pdf.set_font(FAMILY, "B", size)
    pdf.set_text_color(*style.heading_color)
    height = size * PT_TO_MM * 1.3
    if template == "modern":
        y = pdf.get_y()
        pdf.set_fill_color(*BLUE)
        pdf.rect(style.margin, y + height * 0.2, 1.2, height * 0.6, style="F")
        pdf.set_x(style.margin + 3.0)
        pdf.cell(width - 3.0, height, text)
        pdf.ln(height + 1.0)
        return
    pdf.cell(width, height, text.upper())
    pdf.ln(height + 0.6)
    y = pdf.get_y()
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.25)
    end = style.margin + (width if template == "classic" else width * 0.35)
    pdf.line(style.margin, y, end, y)
    pdf.ln(1.6 if template == "classic" else 1.2)


def _line(pdf: FPDF, text: str, bold: str | None, style: _Style) -> None:
    width = pdf.w - 2 * style.margin
    lead = style.body_size * PT_TO_MM * style.leading
    pdf.set_text_color(*INK)
    if bold and text.startswith(bold) and len(text) > len(bold):
        pdf.set_font(FAMILY, "B", style.body_size)
        pdf.write(lead, bold)
        pdf.set_font(FAMILY, "", style.body_size)
        pdf.write(lead, text[len(bold) :])
        pdf.ln(lead)
        return
    pdf.set_font(FAMILY, "B" if bold else "", style.body_size)
    pdf.multi_cell(width, lead, text, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _build(cv: StarterCv, template: Template, font: CvFont, created: datetime) -> FPDF:
    style = _STYLES[template]
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.creation_date = created
    pdf.set_title(f"{cv.name} - CV")
    pdf.set_author(cv.name)
    pdf.set_margins(style.margin, style.margin, style.margin)
    pdf.set_auto_page_break(True, margin=style.margin)
    pdf.c_margin = 0
    for font_style, path in font_files(font).items():
        pdf.add_font(FAMILY, font_style, str(path))
    pdf.add_page()
    width = pdf.w - 2 * style.margin
    align = "C" if style.centered else "L"

    # ---- Header: name, target position, contact line.
    pdf.set_font(FAMILY, "B", style.name_size)
    pdf.set_text_color(*style.name_color)
    pdf.multi_cell(
        width,
        style.name_size * PT_TO_MM * 1.2,
        cv.name,
        align=align,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_font(FAMILY, "", style.headline_size)
    pdf.set_text_color(*(INK if template == "modern" else SLATE))
    pdf.multi_cell(
        width,
        style.headline_size * PT_TO_MM * 1.35,
        cv.headline,
        align=align,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    if cv.contact:
        pdf.ln(0.8)
        pdf.set_font(FAMILY, "", style.contact_size)
        pdf.set_text_color(*SLATE)
        pdf.multi_cell(
            width,
            style.contact_size * PT_TO_MM * 1.4,
            _contact_lines(pdf, cv.contact, width),
            align=align,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    if template == "modern":
        pdf.ln(2.0)
        y = pdf.get_y()
        pdf.set_draw_color(*BLUE)
        pdf.set_line_width(0.5)
        pdf.line(style.margin, y, pdf.w - style.margin, y)
    pdf.ln(style.section_gap)

    # ---- Sections, in the standard order; empty ones are not drawn.
    headings = _words(HEADINGS, cv.lang)
    for sid, lines in _sections(cv):
        _heading(pdf, headings[sid], style, template)
        for text, bold in lines:
            _line(pdf, text, bold, style)
        pdf.ln(style.section_gap)
    return pdf


def render_starter_cv(
    cv: StarterCv,
    template: Template,
    out_path: Path,
    *,
    font: CvFont = DEFAULT_CV_FONT,
    created: datetime | None = None,
) -> int:
    """Write ``cv`` drawn with ``template`` to ``out_path``; return its page
    count. ``CvgenError`` for an unknown template, a CV without a name or
    missing font files."""
    if template not in _STYLES:
        raise CvgenError(f"unknown starter CV template {template!r}")
    if not cv.name.strip():
        raise CvgenError("a starter CV needs the candidate's name")
    when = created or datetime.now(UTC).replace(microsecond=0)
    pdf = _build(cv, template, font, when)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(pdf.output()))
    return int(pdf.page_no())


def sections_of(cv: StarterCv) -> Sequence[str]:
    """The section ids a starter CV draws (the non-empty ones, in order)."""
    return [sid for sid, _ in _sections(cv)]
