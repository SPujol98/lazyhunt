"""Cover letters as PDF files (S4): the chosen letter and /cartabase.

Portals sometimes ask for the letter as a file, so the bot sends it as a PDF
next to the text: name and contact from the identity claims, the date in the
letter's language, the text verbatim, the CV font, one page whenever it fits.
"""

import asyncio
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

import bot
from cva.claimsdb import load_claims
from cva.cvgen import engine as cvgen_engine
from cva.cvgen.letter_pdf import (
    BODY_SIZES,
    LetterIdentity,
    letter_date,
    letter_identity,
    render_letter_pdf,
)
from cva.i18n import Lang
from cva.match.cover_letter import assemble_open_letter
from cva.match.extraction import ExtractionCache
from cva.match.scorer import load_synonyms
from cva.profile_persona import load_letter_bases
from cva.service import JobService
from cva.tracker import OfferRepository
from cva.tracker.repo import OfferInput
from tests.demo import DEMO_CLAIMS, DEMO_DIR, DEMO_MASTERS, DEMO_SYNONYMS
from tests.test_bot import FakeContext, FakeMessage, FakeQuery, FakeUpdate
from tests.test_cover_letter_service import ES_LETTER, NOW, _Fetcher, _Llm

DAY = date(2026, 11, 19)
IDENTITY = LetterIdentity(
    name="Alex Demo",
    contact=("Valencia, España", "+34 600 000 000", "alex@example.com"),
)
LETTER = (
    "Hola equipo de Acme,\n\n"
    "Me interesa mucho el puesto de AI Engineer porque combina Python y RAG.\n\n"
    "He construido pipelines de recuperación evaluados con recall@k.\n\n"
    "Un saludo,\nAlex Demo"
)


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _render(tmp_path: Path, text: str = LETTER, language: str = "es") -> Path:
    return render_letter_pdf(
        text,
        identity=IDENTITY,
        language=language,
        out_path=tmp_path / "letter.pdf",
        date=DAY,
    )


def _font_sizes(path: Path) -> set[float]:
    sizes: set[float] = set()
    for page in PdfReader(path).pages:

        def visit(text, cm, tm, font, size) -> None:
            if text.strip():
                sizes.add(round(size * tm[0], 2))

        page.extract_text(visitor_text=visit)
    return sizes


# --- rendering -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("es", "19 de noviembre de 2026"),
        ("en", "19 November 2026"),
        ("ca", "19 de novembre de 2026"),
    ],
)
def test_date_is_written_in_the_letter_language(language: str, expected: str) -> None:
    assert letter_date(DAY, language) == expected


def test_catalan_date_elides_before_a_vowel() -> None:
    assert letter_date(date(2026, 4, 3), "ca") == "3 d'abril de 2026"


def test_letter_renders_one_page_with_name_date_and_every_paragraph(
    tmp_path: Path,
) -> None:
    path = _render(tmp_path)
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    text = _words(reader.pages[0].extract_text())
    assert "Alex Demo" in text
    assert "alex@example.com" in text and "+34 600 000 000" in text
    assert "19 de noviembre de 2026" in text
    for paragraph in LETTER.split("\n\n"):
        assert _words(paragraph) in text
    assert min(_font_sizes(path)) >= 10


def test_letter_uses_the_cv_font(tmp_path: Path) -> None:
    fonts = {
        str(font["/BaseFont"])
        for page in PdfReader(_render(tmp_path)).pages
        for font in page["/Resources"]["/Font"].values()
    }
    assert fonts and all("Carlito" in font for font in fonts)


def test_long_letter_continues_on_a_second_page_never_below_10pt(
    tmp_path: Path,
) -> None:
    paragraph = "Esta frase alarga la carta para comprobar el salto de página. " * 12
    long_text = "\n\n".join([paragraph] * 6)
    path = _render(tmp_path, long_text)
    assert len(PdfReader(path).pages) == 2
    assert min(_font_sizes(path)) >= min(BODY_SIZES) == 10


def test_bytes_are_deterministic_for_a_fixed_date(tmp_path: Path) -> None:
    first = _render(tmp_path / "a").read_bytes()
    second = _render(tmp_path / "b").read_bytes()
    assert first == second


def test_header_comes_from_the_identity_claims() -> None:
    claims = load_claims(DEMO_CLAIMS)
    header = letter_identity(claims, "Alex Demo", "en")
    contact_claim = claims.by_id["identity-contact_en"].text
    assert header.name == "Alex Demo"
    assert header.contact[0] == contact_claim.split("·")[1].strip()
    assert claims.by_id["identity-phone_en"].text in header.contact
    assert all(
        field in contact_claim or field.startswith("+") for field in header.contact
    )


# --- bot: choosing a letter -------------------------------------------------------


def _service(tmp_path: Path, ui: Lang) -> tuple[JobService, OfferRepository, int]:
    conn = sqlite3.connect(tmp_path / "app.db")
    repo = OfferRepository(conn)
    db = load_claims(DEMO_CLAIMS)
    service = JobService(
        repo=repo,
        llm=_Llm(),  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(DEMO_SYNONYMS, db),
        cache=ExtractionCache(conn),
        masters_dir=DEMO_MASTERS,
        now_fn=lambda: NOW,
        ui_language=ui,
    )
    oid = repo.upsert_offer(
        OfferInput(
            title="AI Engineer",
            company="Acme",
            url="https://x/1",
            text="Buscamos AI Engineer en Valencia. Adjunta una carta de presentación. Python y RAG.",
            source="link",
            profile="ai",
        )
    )
    return service, repo, oid


def _choose(service: JobService, oid: int) -> FakeQuery:
    result = asyncio.run(service.cover_letters(oid))
    query = FakeQuery(f"clc:{oid}:{result.generation}:a")
    asyncio.run(bot.callback_router(FakeUpdate(query=query), FakeContext(service)))
    return query


@pytest.mark.parametrize(
    ("ui", "saved"),
    [("en", "Letter saved on the offer"), ("es", "Guardada la carta en la oferta")],
)
def test_choosing_a_letter_saves_it_and_sends_the_pdf(
    tmp_path: Path, ui: Lang, saved: str
) -> None:
    service, repo, oid = _service(tmp_path, ui)
    query = _choose(service, oid)
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER
    assert query.message.replies == [saved]
    # Letter language (Spanish offer), not UI language, names the file.
    name = f"{service._identity.file_slug}_Carta_Presentacion.pdf"
    assert query.message.documents == [name]
    path = cvgen_engine.OUTPUT_DIR / f"{oid}_Acme" / name
    text = _words(PdfReader(path).pages[0].extract_text())
    assert _words(ES_LETTER.split("\n\n")[1]) in text
    assert "13 de octubre de 2026" in text  # NOW, in the letter's language


@pytest.mark.parametrize(
    ("ui", "warning"),
    [
        ("en", "⚠️ Could not create the letter PDF: "),
        ("es", "⚠️ No se pudo crear el PDF de la carta: "),
    ],
)
def test_a_pdf_failure_keeps_the_saved_letter_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ui: Lang, warning: str
) -> None:
    service, repo, oid = _service(tmp_path, ui)

    def broken(self: JobService, offer_id: int) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(JobService, "cover_letter_pdf", broken)
    query = _choose(service, oid)
    assert repo.get_offer(oid).carta_presentacion == ES_LETTER
    assert len(query.message.replies) == 2
    assert query.message.replies[1].startswith(warning)
    assert query.message.documents == []


# --- bot: /cartabase ----------------------------------------------------------------


def test_cartabase_sends_the_letter_note_and_pdf(tmp_path: Path) -> None:
    service, _repo, _oid = _service(tmp_path, "es")
    message = FakeMessage()
    context = FakeContext(service, args=["en"])
    context.bot_data[bot._LETTER_BASE_KEY] = DEMO_DIR / "cover_letter_base.toml"
    asyncio.run(bot.cartabase_command(FakeUpdate(message=message), context))
    letter = assemble_open_letter(
        load_letter_bases(DEMO_DIR / "cover_letter_base.toml")["en"]
    )
    assert message.replies[0] == letter and len(message.replies) == 2
    name = f"{service._identity.file_slug}_Cover_Letter.pdf"
    assert message.documents == [name]
    path = cvgen_engine.OUTPUT_DIR / "base_letter_en" / name
    text = _words(PdfReader(path).pages[0].extract_text())
    assert "13 October 2026" in text
    assert _words(letter.split("\n\n")[-2]) in text


def test_cartabase_pdf_failure_keeps_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repo, _oid = _service(tmp_path, "en")

    def broken(self: JobService, text: str, language: str) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(JobService, "open_letter_pdf", broken)
    message = FakeMessage()
    context = FakeContext(service, args=["es"])
    context.bot_data[bot._LETTER_BASE_KEY] = DEMO_DIR / "cover_letter_base.toml"
    asyncio.run(bot.cartabase_command(FakeUpdate(message=message), context))
    assert len(message.replies) == 3
    assert message.replies[2].startswith("⚠️ Could not create the letter PDF: ")
    assert message.documents == []
