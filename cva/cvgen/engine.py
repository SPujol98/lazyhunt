"""fpdf2 render engine — faithful Python port of the OWNER's generator.

Reference: ``cvgen/generate.py`` (committed at the repo root, DO NOT EDIT)
plus the frozen format spec in ``CAMBIOS_CVS.md`` section 2. The format is
a frozen constant set, measured from the owner's AI ES reference PDF:

- A4 portrait, millimetres; margins left 15.0 / top 10.5 / right 14.2;
  ``c_margin = 0`` for exact positions.
- Font family chosen by ``Settings.cv_font`` (:mod:`cva.cvgen.fonts`):
  Carlito (default, SIL OFL 1.1, metric-compatible with Calibri) from
  ``cvgen/fonts/carlito/`` or the owner's real Calibri from
  ``cvgen/fonts/calibri*.ttf``; Regular/Bold/Italic/BoldItalic, referenced
  in place, never duplicated.
- Sizes: name 16, subtitle 11, section headers 10.5, body/titles 10,
  dates/stack 9.5, contact 9. Body leading 11.5pt (``LEAD`` 3.55mm) with
  ``GAP_SCALE`` 0.93 uniform gap compression.
- Dates italic 9.5 grey, LEFT-aligned at the x=141mm tab (never
  right-aligned). Links blue (18, 84, 171) + underlined.
- Exact vertical rhythm per element (``end(...)`` pt calls) — reproduced
  call by call; do not "improve" it.

Plan mapping (:class:`~cva.cvgen.plan.SelectionPlan` → drawing):

- neutral plan: the master renders verbatim (frozen section order:
  summary, experience, projects, education, certifications, skills).
- promote/demote: reorder blocks WITHIN each section container (stable
  merge); sections themselves never move.
- emphasis (claim_id → note): the matching summary paragraph / bullet
  draws fully bold; extracted text is unchanged (styling only).
- tagline_keywords: the referenced leaves' text appends to the subtitle.

Master→layout determinations (match the shipped masters):

- Education ``entries[0]`` draws 42-style (subtitle italic + description
  flowing inline on one line, optional areas bullet at indent 2.1);
  ``entries[1:]`` draw school-style (subtitle italic line, description
  below, then bullets at indent 2.1 like the first entry). Summary paragraphs and project stacks are plain text.
"""

import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

from fpdf import FPDF
from pydantic import BaseModel

from cva.claimsdb.loader import ClaimsDB
from cva.cvgen.fonts import DEFAULT_CV_FONT, CvFont, font_files
from cva.cvgen.master import (
    CvgenError,
    CvVariant,
    Entry,
    Leaf,
    MasterCv,
    Project,
    SkillsRow,
)
from cva.cvgen.plan import SelectionPlan
from cva.cvgen.validators import (
    check_contact,
    check_page_count,
    check_whitelist,
    page_count,
)
from cva.profile_identity import DEFAULT_IDENTITY, Identity

# ---------------------------------------------------------------- format constants
BLACK = (0, 0, 0)
NEAR = (18, 18, 18)
DARK = (31, 31, 31)
SLATE = (75, 85, 99)
BLUE = (18, 84, 171)
DATEGRAY = (156, 163, 175)
RULE = (150, 150, 150)

M_L: float = 15.0
M_R: float = 14.2
M_T: float = 10.5
DATE_TAB: float = 141.0
LEAD: float = 3.55  # mm = 10.06pt body leading (2 experiences fit 1 page)
GAP_SCALE: float = 0.93  # uniform gap compression
PT_TO_MM: float = 0.352778
PAGE_W: float = 210.0

# Internal fpdf2 family name; the embedded font name comes from the files.
FAMILY = "CvFont"

SECTION_ORDER: tuple[str, ...] = (
    "summary",
    "experience",
    "projects",
    "education",
    "certifications",
    "skills",
)

OUTPUT_DIR = Path("adaptadas")


class RenderResult(BaseModel):
    """Outcome of one render: written PDF plus validator verdicts."""

    pdf_path: Path
    pages: int
    violations: list[str]


def render(
    master: MasterCv,
    plan: SelectionPlan,
    *,
    out: Path,
    db: ClaimsDB,
    role_title: str | None = None,
    identity: Identity = DEFAULT_IDENTITY,
    font: CvFont = DEFAULT_CV_FONT,
) -> RenderResult:
    """Render ``master`` under ``plan`` to a PDF and validate it.

    With ``role_title`` the delivery name becomes the v3 part B convention
    ``<out_dir>/<file_slug>_<tailored-role>.pdf`` (spaces → underscores,
    diacritics stripped); otherwise the file lands exactly at ``out``
    (generic-variant-role naming decided by the caller). ``font`` is the
    ``Settings.cv_font`` family (Carlito by default).
    """
    if plan.variant != master.variant:
        raise CvgenError(
            f"plan variant {plan.variant.value} does not match master "
            f"{master.variant.value}; refusing to render"
        )
    target = (
        tailored_output_path(role_title, out.parent, identity.file_slug)
        if role_title
        else out
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = _apply_plan(master, plan)
    _draw(ordered, plan, target, font)
    violations = [
        *check_page_count(target),
        *check_whitelist(target, db, master=ordered),
        *check_contact(target, ordered, identity),
    ]
    return RenderResult(
        pdf_path=target, pages=page_count(target), violations=violations
    )


def output_path(
    role: str,
    company: str,
    out_dir: Path = OUTPUT_DIR,
    owner_slug: str = DEFAULT_IDENTITY.file_slug,
) -> Path:
    """Generic fallback name: ``adaptadas/<file_slug>_ROL_Empresa.pdf``."""
    return out_dir / f"{owner_slug}_{_slug(role)}_{_slug(company)}.pdf"


def tailored_output_path(
    role_title: str,
    out_dir: Path = OUTPUT_DIR,
    owner_slug: str = DEFAULT_IDENTITY.file_slug,
) -> Path:
    """Tailored delivery name: ``adaptadas/<file_slug>_<role>.pdf``.

    Diacritics stripped, every non-alphanumeric run collapsed to one
    underscore (spaces included), leading/trailing underscores trimmed:
    ``Desenvolupador Python`` → ``<file_slug>_Desenvolupador_Python.pdf``.
    Parenthesised/bracketed title noise is dropped first (``Junior AI
    Engineer (all genders welcome)`` → ``<file_slug>_Junior_AI_Engineer.pdf``)
    unless nothing would remain.
    """
    cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", role_title)
    if not re.search(r"[0-9A-Za-z]", cleaned):
        cleaned = role_title
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFD", cleaned)
        if not unicodedata.combining(ch)
    )
    slug = re.sub(r"[^0-9A-Za-z]+", "_", stripped).strip("_")
    if not slug:
        raise CvgenError(f"role title produces an empty filename slug: {role_title!r}")
    return out_dir / f"{owner_slug}_{slug}.pdf"


_DELIVERY_NAMES: dict[CvVariant, str] = {
    CvVariant.AI_ES: "Ingeniero_IA",
    CvVariant.AI_EN: "AI_Engineer",
    CvVariant.AI_CA: "Enginyer_IA",
    CvVariant.BACKEND_ES: "Desarrollador_Backend",
    CvVariant.BACKEND_EN: "Backend_Developer",
}


def company_slug(company: str | None) -> str:
    """ASCII folder-safe slug: diacritics stripped, non-alphanumerics → ``_``."""
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFD", company or "")
        if not unicodedata.combining(ch)
    )
    return re.sub(r"[^0-9A-Za-z]+", "_", stripped).strip("_") or "oferta"


def generic_output_path(
    variant: CvVariant,
    offer_id: int,
    company: str | None,
    out_dir: Path = OUTPUT_DIR,
    owner_slug: str = DEFAULT_IDENTITY.file_slug,
) -> Path:
    """Delivery path ``<out_dir>/<id>_<company>/<file_slug>_<Role>.pdf``.

    ``<Role>`` is the role title in the delivered CV's own language, with no
    language suffix (``AI_ES`` -> ``Ingeniero_IA``, ``AI_EN`` ->
    ``AI_Engineer``, ``AI_CA`` -> ``Enginyer_IA``, ``BACKEND_ES`` ->
    ``Desarrollador_Backend``, ``BACKEND_EN`` -> ``Backend_Developer``). DS
    variants are manual-only and are refused. The offer title never reaches
    the file name. The per-offer folder keeps same-variant offers from
    overwriting each other while re-generating one offer rewrites only its
    own file.
    """
    try:
        role = _DELIVERY_NAMES[variant]
    except KeyError as exc:
        raise CvgenError(f"variant not delivered by the bot: {variant!r}") from exc
    folder = f"{offer_id}_{company_slug(company)}"
    return out_dir / folder / f"{owner_slug}_{role}.pdf"


def _slug(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value.strip())
    return collapsed.replace(" ", "_")


def _apply_plan(master: MasterCv, plan: SelectionPlan) -> MasterCv:
    """Reorder blocks per promote/demote and append tagline keywords.

    Section order is frozen (``SECTION_ORDER``); only the content inside
    each section container is reordered (stable: promoted first, demoted
    last, the rest keep master order).
    """
    ordered = master.model_copy(deep=True)
    leaves_by_id: dict[str, Leaf] = {}
    for block in ordered.sections.values():
        for leaf in [
            *block.paragraphs,
            *block.certs,
            *(row.items for row in block.skills),
        ]:
            leaves_by_id.setdefault(leaf.claim_id, leaf)
        for entry in block.entries:
            for leaf in [entry.title, *entry.bullets]:
                leaves_by_id.setdefault(leaf.claim_id, leaf)
        for project in block.projects:
            for leaf in [project.title, *project.bullets]:
                leaves_by_id.setdefault(leaf.claim_id, leaf)

    for block in ordered.sections.values():
        block.paragraphs = _reorder(block.paragraphs, _leaf_ids, plan)
        block.certs = _reorder(block.certs, _leaf_ids, plan)
        block.entries = _reorder(block.entries, _entry_ids, plan)
        block.projects = _reorder(block.projects, _project_ids, plan)
        block.skills = _reorder(block.skills, _row_ids, plan)

    if ordered.header.tagline is not None and plan.tagline_keywords:
        extras = [
            leaves_by_id[claim_id].text
            for claim_id in plan.tagline_keywords
            if claim_id in leaves_by_id
        ]
        if extras:
            ordered.header.tagline = ordered.header.tagline.model_copy(
                update={
                    "text": ordered.header.tagline.text + " · " + " · ".join(extras)
                }
            )
    return ordered


def _reorder[T](
    items: list[T],
    id_of: Callable[[T], set[str]],
    plan: SelectionPlan,
) -> list[T]:
    """Stable reorder: promoted first, demoted last, rest keep master order."""
    promoted = [item for item in items if id_of(item) & set(plan.promote)]
    middle = [
        item
        for item in items
        if not (id_of(item) & set(plan.promote))
        and not (id_of(item) & set(plan.demote))
    ]
    demoted = [item for item in items if id_of(item) & set(plan.demote)]
    return [*promoted, *middle, *demoted]


def _leaf_ids(leaf: Leaf) -> set[str]:
    return {leaf.claim_id}


def _entry_ids(entry: Entry) -> set[str]:
    ids = {entry.title.claim_id}
    if entry.subtitle is not None:
        ids.add(entry.subtitle.claim_id)
    if entry.description is not None:
        ids.add(entry.description.claim_id)
    ids.update(bullet.claim_id for bullet in entry.bullets)
    return ids


def _project_ids(project: Project) -> set[str]:
    ids = {project.title.claim_id}
    ids.update(bullet.claim_id for bullet in project.bullets)
    if project.stack is not None:
        ids.add(project.stack.claim_id)
    return ids


def _row_ids(row: SkillsRow) -> set[str]:
    return {row.items.claim_id}


def _is_link(text: str) -> bool:
    return text.startswith(("http", "linkedin.com/", "github.com/"))


def _link_target(text: str) -> str:
    return text if text.startswith("http") else f"https://{text}"


def _bold_segments(leaf: Leaf, plan: SelectionPlan) -> list[tuple[str, str]]:
    """Emphasis (D8) draws the whole leaf bold; text is unchanged."""
    return [(leaf.text, "B")] if leaf.claim_id in plan.emphasis else [(leaf.text, "")]


def _draw(cv: MasterCv, plan: SelectionPlan, out: Path, font: CvFont) -> None:
    """One exact port of the owner's ``build()`` drawing pass."""
    missing = [key for key in SECTION_ORDER if key not in cv.sections]
    if missing:
        raise CvgenError(
            f"master {cv.variant.value} is missing frozen sections: "
            f"{', '.join(missing)}"
        )
    unknown = [key for key in cv.sections if key not in SECTION_ORDER]
    if unknown:
        raise CvgenError(
            f"master {cv.variant.value} declares non-frozen sections: "
            f"{', '.join(unknown)}"
        )
    blocks = cv.sections

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(M_L, M_T, M_R)
    pdf.c_margin = 0
    pdf.add_page()
    for style, path in font_files(font).items():
        pdf.add_font(FAMILY, style, str(path))

    def end(g_pt: float) -> None:
        pdf.ln(g_pt * GAP_SCALE * PT_TO_MM)

    def text(
        segments: str | list[tuple[str, str]],
        size: float = 10.0,
        color: tuple[int, int, int] = BLACK,
    ) -> None:
        pdf.set_text_color(*color)
        if isinstance(segments, str):
            segments = [(segments, "")]
        for piece, style in segments:
            pdf.set_font(FAMILY, style, size)
            pdf.write(LEAD, piece)

    def section(title: str, pre: float, consumed: float, first: float) -> None:
        pdf.ln((pre - consumed) * PT_TO_MM)
        text([(title.upper(), "B")], size=10.5, color=DARK)
        y = pdf.get_y()
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.3)
        pdf.line(M_L, y + LEAD + 0.5, PAGE_W - M_R, y + LEAD + 0.5)
        end(first)

    def bullet(txt: str, ind: float, next_pt: float, *, bold: bool = False) -> None:
        pdf.set_font(FAMILY, "B" if bold else "", 10)
        pdf.set_text_color(*NEAR)
        pdf.set_x(M_L + ind)
        pdf.cell(3.2, LEAD, "•")
        pdf.set_left_margin(M_L + ind + 3.2)
        pdf.set_x(M_L + ind + 3.2)
        pdf.multi_cell(0, LEAD, txt)
        pdf.set_left_margin(M_L)
        pdf.ln((next_pt * GAP_SCALE - 10.06) * PT_TO_MM)

    def title_row(left: str, right: str, next_pt: float) -> None:
        text([(left, "B")], size=10)
        pdf.set_font(FAMILY, "I", 9.5)
        pdf.set_text_color(*DATEGRAY)
        pdf.set_x(DATE_TAB)
        pdf.write(LEAD, right)
        end(next_pt)

    # ---- Header
    text([(cv.header.name.text.upper(), "B")], size=16)
    end(18.2)
    tagline = cv.header.tagline
    if tagline is not None:
        # Subtitle line (bold, dark); tagline_keywords already appended.
        text([(tagline.text, "B")], size=11)
    end(17.1)
    pdf.set_font(FAMILY, "", 9)
    pdf.set_text_color(*SLATE)
    for index, leaf in enumerate(cv.header.contact):
        if index:
            pdf.write(LEAD, " · ")
        if _is_link(leaf.text):
            pdf.set_text_color(*BLUE)
            pdf.write(LEAD, leaf.text, _link_target(leaf.text))
            pdf.set_text_color(*SLATE)
        else:
            pdf.write(LEAD, leaf.text)

    # ---- Summary
    section(blocks["summary"].title or "Summary", pre=19.2, consumed=0, first=18.7)
    for leaf in blocks["summary"].paragraphs:
        text(_bold_segments(leaf, plan))
        end(17.1)

    # ---- Experience (most recent first)
    section(
        blocks["experience"].title or "Experience",
        pre=19.6,
        consumed=17.1,
        first=18.7,
    )
    entries = blocks["experience"].entries
    for xi, entry in enumerate(entries):
        last_entry = xi == len(entries) - 1
        title_row(entry.title.text, entry.date, 12.9)
        if entry.subtitle is not None:
            text(entry.subtitle.text)
        end(15.6)
        if not entry.bullets:
            end(11.8 if last_entry else 15.6)
            continue
        for j, b in enumerate(entry.bullets):
            last = j == len(entry.bullets) - 1
            bullet(
                b.text,
                ind=3.2,
                next_pt=11.8 if (last and last_entry) else 15.6,
                bold=b.claim_id in plan.emphasis,
            )

    # ---- Projects
    section(
        blocks["projects"].title or "Projects",
        pre=19.7,
        consumed=11.8,
        first=20.9,
    )
    for project in blocks["projects"].projects:
        pdf.set_font(FAMILY, "BU", 10)
        pdf.set_text_color(*BLUE)
        pdf.write(LEAD, project.title.text, project.url or "")
        end(14.2)
        for b in project.bullets:
            bullet(b.text, ind=4.9, next_pt=12.2, bold=b.claim_id in plan.emphasis)
        if project.stack is not None:
            pdf.set_font(FAMILY, "B", 9.5)
            pdf.set_text_color(*SLATE)
            pdf.write(LEAD, "Stack: ")
            pdf.set_font(FAMILY, "", 9.5)
            pdf.write(LEAD, project.stack.text)
        end(17.6)

    # ---- Education (first entry 42-style, the rest school-style)
    section(
        blocks["education"].title or "Education",
        pre=19.6,
        consumed=17.6,
        first=18.3,
    )
    edu = blocks["education"].entries
    if edu:
        first = edu[0]
        title_row(first.title.text, first.date, 13.8)
        pdf.set_font(FAMILY, "I", 10)
        pdf.set_text_color(*SLATE)
        if first.subtitle is not None:
            pdf.write(LEAD, first.subtitle.text)
        if first.description is not None:
            pdf.set_font(FAMILY, "", 10)
            pdf.set_text_color(*NEAR)
            separator = " — " if first.subtitle is not None else ""
            pdf.write(LEAD, separator + first.description.text)
        end(14.3)
        if first.bullets:
            for b in first.bullets:
                bullet(
                    b.text,
                    ind=2.1,
                    next_pt=15.0,
                    bold=b.claim_id in plan.emphasis,
                )
        else:
            end(15.0 - 11.47)
    edu_consumed = 0.0  # gap already spent after the last education line
    school = edu[1:]
    for si, entry in enumerate(school):
        title_row(entry.title.text, entry.date, 13.7)
        if entry.subtitle is not None:
            text([(entry.subtitle.text, "I")], color=SLATE)
        end(13.7)
        if entry.description is not None:
            text(entry.description.text)
            if entry.bullets:
                end(14.3)
        for bi, b in enumerate(entry.bullets):
            last_line = si == len(school) - 1 and bi == len(entry.bullets) - 1
            # Same bullet style as the first entry; the final bullet closes
            # with the gap the certifications section header expects.
            bullet(
                b.text,
                ind=2.1,
                next_pt=11.8 if last_line else 15.0,
                bold=b.claim_id in plan.emphasis,
            )
            if last_line:
                edu_consumed = 11.8

    # ---- Certifications
    section(
        blocks["certifications"].title or "Certifications",
        pre=19.7,
        consumed=edu_consumed,
        first=18.7,
    )
    for i, leaf in enumerate(blocks["certifications"].certs):
        bullet(
            leaf.text,
            ind=2.1,
            next_pt=14.8 if i == 0 else 11.8,
            bold=leaf.claim_id in plan.emphasis,
        )

    # ---- Skills
    section(
        blocks["skills"].title or "Skills",
        pre=20.2,
        consumed=11.8,
        first=18.4,
    )
    rows = blocks["skills"].skills
    for i, row in enumerate(rows):
        pdf.set_font(FAMILY, "B", 10)
        pdf.set_text_color(*BLACK)
        pdf.write(LEAD, f"{row.label}: ")
        pdf.set_font(FAMILY, "", 10)
        pdf.write(LEAD, row.items.text)
        if i < len(rows) - 1:
            end(15.6)

    pdf.output(str(out))


__all__ = [
    "OUTPUT_DIR",
    "RenderResult",
    "company_slug",
    "generic_output_path",
    "output_path",
    "render",
    "tailored_output_path",
]
