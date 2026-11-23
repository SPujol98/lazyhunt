"""``/cvs``: the base CV catalog (tailored-cv T1, S3/S4(a)/S5).

One message lists every base CV slot (position × language), where it comes
from and, for the ones lazyhunt built, its template and sections; buttons
send the PDF, change the template or rebuild it from the answers. The owner's
install (hand-made PDFs, no structured source) is listed as "your own PDF"
with no adaptation wording at all.
"""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfReader

import bot
from cva.commands import COMMANDS, command_name, help_text
from cva.cvgen import cv_source as S
from cva.cvgen.base_pdfs import verified_base_pdf
from cva.families import BUILTIN_REGISTRY
from cva.i18n import Lang, t
from cva.onboarding import cv_catalog as C
from cva.onboarding import profile_edit
from tests.test_bot import FakeMessage, FakeQuery, FakeUpdate
from tests.test_cv_source import _confirmed_created

DEMO = Path(__file__).resolve().parents[1] / "profiles" / "demo"
ADAPT_WORDS = ("adapt", "Adapt", "adapta", "Adapta")


def _demo(tmp_path: Path) -> Path:
    copy = tmp_path / "demo"
    shutil.copytree(DEMO, copy)
    return copy


def _context(profile: Path | None, lang: Lang = "en") -> Any:
    return SimpleNamespace(
        bot_data={bot._LANG_KEY: lang, bot._PROFILE_DIR_KEY: profile}
    )


def _cvs(profile: Path | None, lang: Lang = "en") -> FakeMessage:
    message = FakeMessage("/cvs")
    asyncio.run(bot.cvs_command(FakeUpdate(message=message), _context(profile, lang)))
    return message


def _press(profile: Path | None, data: str, lang: Lang = "en") -> FakeQuery:
    query = FakeQuery(data)
    press = bot._Press(
        SimpleNamespace(), query, _context(profile, lang), BUILTIN_REGISTRY, lang, "cvs"
    )
    handled = asyncio.run(bot._cb_cvs(press, data.split(":")[1:]))
    assert handled
    return query


def _callbacks(markup: Any) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cvs_is_registered_unlisted_so_help_still_fits_one_message() -> None:
    (command,) = [c for c in COMMANDS if c.name == "cvs"]
    assert command.names == ("cvs",) and command.listed is False
    assert bot._COMMAND_HANDLERS["cvs"] is bot.cvs_command
    for lang in ("en", "es"):
        assert len(help_text(lang=lang)) < 4096  # type: ignore[arg-type]
        assert command_name("cvs", lang) == "/cvs"  # type: ignore[arg-type]


@pytest.mark.parametrize("lang", ["en", "es"])
def test_owner_install_lists_own_pdfs_without_any_adaptation_wording(
    tmp_path: Path, lang: Lang
) -> None:
    profile = _demo(tmp_path)
    entries = C.base_cvs(profile, BUILTIN_REGISTRY)
    assert [e.slot for e in entries] == [
        "AI_ES",
        "AI_EN",
        "AI_CA",
        "BACKEND_ES",
        "BACKEND_EN",
    ]
    assert {e.origin for e in entries} == {"own"}
    (reply,) = _cvs(profile, lang).replies
    assert t("bot.cvs.origin.own", lang) in reply
    assert "AI · ES" in reply and "Backend · EN" in reply
    for word in ADAPT_WORDS:
        assert word not in reply
    markup = _cvs(profile, lang).markups[0]
    assert _callbacks(markup) == [f"cvs:pdf:{e.slot}" for e in entries]


def test_legacy_install_without_profile_dir_reads_the_repo_base_pdfs() -> None:
    reply = _cvs(None).replies[0]
    for word in ADAPT_WORDS:
        assert word not in reply


@pytest.mark.parametrize("lang", ["en", "es"])
def test_a_created_cv_shows_origin_template_sections_and_its_buttons(
    tmp_path: Path, lang: Lang
) -> None:
    profile = _confirmed_created(tmp_path)
    (entry,) = C.base_cvs(profile, BUILTIN_REGISTRY)
    assert (entry.slot, entry.origin, entry.template) == ("AI_ES", "lazyhunt", "modern")
    assert entry.adaptable and not entry.stale
    assert dict(entry.sections)["skills"] >= 1
    message = _cvs(profile, lang)
    (reply,) = message.replies
    assert (
        t("bot.cvs.origin.lazyhunt", lang, template=t("onboarding.cv.tpl.modern", lang))
        in reply
    )
    assert _callbacks(message.markups[0]) == [
        "cvs:pdf:AI_ES",
        "cvs:tpl:AI_ES",
        "cvs:re:AI_ES",
    ]


def test_an_uploaded_pdf_next_to_a_created_one_says_not_adaptable(
    tmp_path: Path,
) -> None:
    profile = _confirmed_created(tmp_path)
    base = profile / "cv_base_pdfs"
    manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    shutil.copyfile(
        DEMO / "cv_base_pdfs" / "CV_AI_Engineer_Alex_Rivera_EN.pdf", base / "up.pdf"
    )
    manifest["variants"]["AI_EN"] = {"file": "up.pdf", "sha256": _sha(base / "up.pdf")}
    (base / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    by_slot = {e.slot: e for e in C.base_cvs(profile, BUILTIN_REGISTRY)}
    assert by_slot["AI_EN"].origin == "uploaded" and not by_slot["AI_EN"].adaptable
    reply = _cvs(profile).replies[0]
    assert t("bot.cvs.origin.uploaded", "en") in reply


def test_view_pdf_sends_the_stored_pdf(tmp_path: Path) -> None:
    profile = _demo(tmp_path)
    query = _press(profile, "cvs:pdf:AI_EN")
    assert query.message.documents == ["CV_AI_Engineer_Alex_Rivera_EN.pdf"]


def test_change_template_rerenders_from_the_source_and_updates_the_manifest(
    tmp_path: Path,
) -> None:
    profile = _confirmed_created(tmp_path)
    base = profile / "cv_base_pdfs"
    before = verified_base_pdf("AI_ES", base)
    old_sha = _sha(before)
    query = _press(profile, "cvs:tpl:AI_ES")
    assert [f"cvs:set:AI_ES:{tpl}" for tpl in ("classic", "compact")] == [
        c for c in _callbacks(query.message.markups[0]) if c.startswith("cvs:set")
    ]
    query = _press(profile, "cvs:set:AI_ES:compact")
    pdf = verified_base_pdf("AI_ES", base)  # the manifest sha256 matches the new PDF
    assert pdf == before and _sha(pdf) != old_sha
    source = S.adaptable_source(profile, "AI_ES")
    assert source is not None and source.template == "compact"
    assert source.pdf_sha256 == _sha(pdf)
    assert (
        t(
            "bot.cvs.done.template",
            "en",
            slot="AI · ES",
            template=t("onboarding.cv.tpl.compact", "en"),
        )
        in query.message.replies[-1]
    )
    text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
    assert "José Pérez" in text


def test_rebuild_redraws_from_the_current_answers(tmp_path: Path) -> None:
    profile = _confirmed_created(tmp_path)
    answers_path = profile / "onboarding_answers.json"
    record = json.loads(answers_path.read_text("utf-8"))
    answers = record.get("answers", record)
    answers["skills"] = [*answers.get("skills", []), "Rust"]
    answers_path.write_text(json.dumps(record), "utf-8")
    _press(profile, "cvs:re:AI_ES")
    pdf = verified_base_pdf("AI_ES", profile / "cv_base_pdfs")
    text = "\n".join(p.extract_text() or "" for p in PdfReader(pdf).pages)
    assert "Rust" in text
    source = S.load_source(S.source_path(profile / "cv_base_pdfs", "AI_ES"))
    assert source.template == "modern"
    skills = source.section("skills")
    assert skills is not None and skills.items[-1].claim_id == "setup-skill-rust"


def test_actions_on_a_pdf_without_source_change_nothing(tmp_path: Path) -> None:
    profile = _demo(tmp_path)
    base = profile / "cv_base_pdfs"
    before = {p.name: p.read_bytes() for p in base.iterdir()}
    for data in ("cvs:set:AI_ES:modern", "cvs:re:AI_ES"):
        query = _press(profile, data)
        assert query.message.replies == [t("bot.cvs.err.not_created", "en")]
    assert {p.name: p.read_bytes() for p in base.iterdir()} == before


def test_profile_view_lists_the_base_cvs_read_only(tmp_path: Path) -> None:
    profile = _confirmed_created(tmp_path)
    view = profile_edit.profile_view(profile)
    assert view["base_cvs"] == [
        {
            "slot": "AI_ES",
            "label": "AI",
            "lang": "es",
            "origin": "lazyhunt",
            "template": "modern",
            "sections": len(
                S.load_source(S.source_path(profile / "cv_base_pdfs", "AI_ES")).sections
            ),
            "adaptable": True,
        }
    ]
    demo = profile_edit.profile_view(_demo(tmp_path))
    assert {e["origin"] for e in demo["base_cvs"]} == {"own"}
    assert all(e["template"] is None and e["sections"] == 0 for e in demo["base_cvs"])
