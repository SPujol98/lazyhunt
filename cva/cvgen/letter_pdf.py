"""Cover letters as PDF files: portals that ask for the letter as a file.

One A4 portrait page (a second page only when the text cannot fit at the
10 pt floor): the candidate's name and contact line at the top, from the same
identity claims the CV header and the contact validator use, the date in the
letter's language, then the letter text verbatim (paragraphs preserved; the
salutation and the sign-off are already part of it). The font family is the
CVs' (``Settings.cv_font`` through :func:`cva.cvgen.fonts.font_files`). The
PDF creation date is pinned to the letter date, so the bytes are a pure
function of (text, identity, language, font, date).

File names follow the CV delivery convention (:mod:`cva.cvgen.engine`):
``<out_dir>/<id>_<company>/<file_slug>_<LetterName>.pdf``, ``<LetterName>``
in the letter's own language like the CV role name is in the CV's.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Final

from fpdf import FPDF

from cva.claimsdb import ClaimsDB
from cva.cvgen.engine import FAMILY, OUTPUT_DIR, company_slug
from cva.cvgen.fonts import DEFAULT_CV_FONT, CvFont, font_files
from cva.cvgen.master import CvgenError
from cva.profile_identity import DEFAULT_IDENTITY, Identity

# Body size steps: the largest that fits one page wins; never below 10 pt.
BODY_SIZES: Final[tuple[float, ...]] = (11.0, 10.5, 10.0)
NAME_SIZE: Final[float] = 18.0
CONTACT_SIZE: Final[float] = 10.0
MARGIN_X: Final[float] = 25.0
MARGIN_TOP: Final[float] = 22.0
MARGIN_BOTTOM: Final[float] = 20.0
PT_TO_MM: Final[float] = 0.352778
LINE_FACTOR: Final[float] = 1.45  # body leading = size x 1.45
INK: Final[tuple[int, int, int]] = (25, 25, 25)
SLATE: Final[tuple[int, int, int]] = (75, 85, 99)
RULE: Final[tuple[int, int, int]] = (180, 180, 180)
_SEP: Final[str] = "  ·  "

# File-name part per letter language (ASCII, like the CV role names).
LETTER_FILE_NAMES: Final[dict[str, str]] = {
    "es": "Carta_Presentacion",
    "en": "Cover_Letter",
    "ca": "Carta_Presentacio",
}

_MONTHS: Final[dict[str, tuple[str, ...]]] = {
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "ca": (
        "gener",
        "febrer",
        "març",
        "abril",
        "maig",
        "juny",
        "juliol",
        "agost",
        "setembre",
        "octubre",
        "novembre",
        "desembre",
    ),
}


@dataclass(frozen=True)
class LetterIdentity:
    """The letter header: the candidate's name and contact fields."""

    name: str
    contact: tuple[str, ...]


def letter_date(day: date, language: str) -> str:
    """``day`` written out in ``language`` (``es``/``en``/``ca``).

    ``19 de noviembre de 2026`` / ``19 November 2026`` / ``19 de novembre de
    2026``; Catalan elides before a vowel (``3 d'abril de 2026``).
    """
    months = _MONTHS.get(language, _MONTHS["es"])
    month = months[day.month - 1]
    if language == "en":
        return f"{day.day} {month} {day.year}"
    if language == "ca" and month[0] in "aeiou":
        return f"{day.day} d'{month} de {day.year}"
    return f"{day.day} de {month} de {day.year}"


def letter_identity(
    claims: ClaimsDB,
    name: str,
    language: str,
    identity: Identity = DEFAULT_IDENTITY,
) -> LetterIdentity:
    """Header fields from the identity claims (the CV contact block's source).

    The identity claim reads ``Name · City · email · links``; its ``_en``
    twin is used for English letters when present. The phone claim follows
    the city, as in the CV header. ``name`` is the signature name the letters
    already use (``owner_name``).
    """
    family = identity.identity_claim_id.rsplit("-", 1)[0]
    suffix = "_en" if language == "en" else ""
    contact_claim = claims.by_id.get(
        identity.identity_claim_id + suffix
    ) or claims.by_id.get(identity.identity_claim_id)
    phone_claim = claims.by_id.get(f"{family}-phone{suffix}") or claims.by_id.get(
        f"{family}-phone"
    )
    fields = (
        [part.strip() for part in contact_claim.text.split("·")[1:]]
        if contact_claim is not None
        else []
    )
    fields = [part for part in fields if part]
    if phone_claim is not None and phone_claim.text.strip():
        fields.insert(1 if fields else 0, phone_claim.text.strip())
    return LetterIdentity(name=name, contact=tuple(fields))


def letter_file_name(language: str, owner_slug: str) -> str:
    """``<file_slug>_<LetterName>.pdf`` in the letter's language."""
    return (
        f"{owner_slug}_{LETTER_FILE_NAMES.get(language, LETTER_FILE_NAMES['es'])}.pdf"
    )


def offer_letter_path(
    offer_id: int,
    company: str | None,
    language: str,
    out_dir: Path = OUTPUT_DIR,
    owner_slug: str = DEFAULT_IDENTITY.file_slug,
) -> Path:
    """Next to the offer's CV: ``<out_dir>/<id>_<company>/<file_slug>_<LetterName>.pdf``."""
    folder = f"{offer_id}_{company_slug(company)}"
    return out_dir / folder / letter_file_name(language, owner_slug)


def open_letter_path(
    language: str,
    out_dir: Path = OUTPUT_DIR,
    owner_slug: str = DEFAULT_IDENTITY.file_slug,
) -> Path:
    """The ``/cartabase`` letter: ``<out_dir>/base_letter_<lang>/<file_slug>_<LetterName>.pdf``."""
    return out_dir / f"base_letter_{language}" / letter_file_name(language, owner_slug)


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs; single line breaks stay inside."""
    blocks = text.replace("\r\n", "\n").strip().split("\n\n")
    return [block.strip("\n") for block in blocks if block.strip()]


def _contact_lines(pdf: FPDF, fields: Sequence[str], width: float) -> str:
    """The contact fields on one line, or two balanced lines split between
    fields (never inside one) when one line is too wide."""
    joined = _SEP.join(fields)
    if pdf.get_string_width(joined) <= width or len(fields) < 2:
        return joined
    best = 1
    for cut in range(1, len(fields)):
        first = pdf.get_string_width(_SEP.join(fields[:cut]))
        second = pdf.get_string_width(_SEP.join(fields[cut:]))
        if max(first, second) <= width:
            best = cut
            if first >= second:
                break
    return _SEP.join(fields[:best]) + "\n" + _SEP.join(fields[best:])


def _build(
    paragraphs: Sequence[str],
    identity: LetterIdentity,
    date_text: str,
    font: CvFont,
    body_size: float,
    created: datetime,
) -> FPDF:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.creation_date = created
    pdf.set_margins(MARGIN_X, MARGIN_TOP, MARGIN_X)
    pdf.set_auto_page_break(True, margin=MARGIN_BOTTOM)
    pdf.c_margin = 0
    for style, path in font_files(font).items():
        pdf.add_font(FAMILY, style, str(path))
    pdf.add_page()
    width = pdf.w - 2 * MARGIN_X

    # ---- Header: name, contact line, thin rule.
    pdf.set_text_color(*INK)
    pdf.set_font(FAMILY, "B", NAME_SIZE)
    pdf.multi_cell(width, NAME_SIZE * PT_TO_MM * 1.2, identity.name)
    if identity.contact:
        pdf.ln(1.2)
        pdf.set_font(FAMILY, "", CONTACT_SIZE)
        pdf.set_text_color(*SLATE)
        pdf.multi_cell(
            width,
            CONTACT_SIZE * PT_TO_MM * 1.4,
            _contact_lines(pdf, identity.contact, width),
        )
    pdf.ln(3.5)
    y = pdf.get_y()
    pdf.set_draw_color(*RULE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN_X, y, pdf.w - MARGIN_X, y)

    # ---- Date (right-aligned), then the letter verbatim.
    lead = body_size * PT_TO_MM * LINE_FACTOR
    pdf.ln(9)
    pdf.set_text_color(*INK)
    pdf.set_font(FAMILY, "", body_size)
    pdf.cell(width, lead, date_text, align="R")
    pdf.ln(lead + 8)
    for index, paragraph in enumerate(paragraphs):
        if index:
            pdf.ln(lead * 0.6)
        pdf.multi_cell(width, lead, paragraph, align="L")
    return pdf


def render_letter_pdf(
    text: str,
    *,
    identity: LetterIdentity,
    language: str,
    out_path: Path,
    date: date,
    font: CvFont = DEFAULT_CV_FONT,
) -> Path:
    """Write ``text`` as a letter PDF at ``out_path``; return ``out_path``.

    The body is set at the largest of :data:`BODY_SIZES` that keeps the
    letter on one page; a letter too long even at 10 pt continues onto a
    second page instead of shrinking further. ``CvgenError`` for an empty
    letter or missing font files.
    """
    paragraphs = _paragraphs(text)
    if not paragraphs:
        raise CvgenError("the cover letter is empty; nothing to render")
    created = datetime.combine(date, time(0, 0), tzinfo=UTC)
    date_text = letter_date(date, language)
    pdf: FPDF | None = None
    for size in BODY_SIZES:
        pdf = _build(paragraphs, identity, date_text, font, size, created)
        if pdf.page_no() == 1:
            break
    assert pdf is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(pdf.output()))
    return out_path
