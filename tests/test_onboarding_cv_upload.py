"""Step 9 of ``/setup``: base CV PDFs uploaded in Telegram (onboarding-v2 T5, S5).

Driven like ``tests/test_onboarding.py``: buttons and text through the bot's
handlers, the PDF itself through :func:`cva.onboarding.flow.on_document` with
the bot's own ``_onboarding_env`` (what the document handler calls).
"""

import asyncio
import io
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfWriter

import bot
from cva.config import Settings
from cva.cvgen.base_pdfs import (
    available_registry,
    deliverable_variants,
    verified_base_pdf,
)
from cva.fetch import FetchResponse
from cva.i18n import Lang, t
from cva.onboarding import cv_upload, flow
from cva.profile_prefs import load_preferences
from tests.test_bot import (
    FakeDocument,
    FakeFetcher,
    FakeMessage,
    FakeUpdate,
    make_service,
)
from tests.test_onboarding import (
    Driver,
    _full_app,
    _setup_mode,
    answer_background,
    tick_exactly,
)

PENDING = cv_upload.PENDING_DIR


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _send(d: Driver, document: FakeDocument | None) -> tuple[bool, FakeMessage]:
    message = FakeMessage()
    message.document = document  # type: ignore[attr-defined]
    taken = asyncio.run(
        flow.on_document(message, d.context.user_data, bot._onboarding_env(d.context))
    )
    d._seen(message.replies, message.markups)
    return taken, message


def _to_cv_step(d: Driver, lang: Lang = "en", custom: bool = True) -> None:
    """Every question up to step 9: AI plus own titles (``custom``) or plus
    Backend, offers in es + en."""
    d.start()
    d.press(f"go:{lang}")
    d.text("José Pérez")
    d.text("jose@example.com")
    d.press("skip")
    d.text("Valencia")
    d.text("linkedin.com/in/jose")
    wanted = ("ai",) if custom else ("ai", "backend")
    for fid in ("ai", "backend"):  # exactly these, whatever was prefilled
        chosen = d.context.user_data[flow.STATE_KEY]["a"].get("families") or []
        if (fid in wanted) != (fid in chosen):
            d.press(f"t:{fid}")
    if custom:
        d.press("other")
        d.text("Data Analyst")
    d.press("done")
    d.press("go:mid")
    answer_background(d)
    d.press("t:remote")
    d.press("done")
    d.press("keep")
    d.text("España")
    d.press("skip")  # no minimum salary
    tick_exactly(d, "offer_langs", ("es", "en"))
    d.press("done")
    d.press("done")  # no portals
    assert d.step == "cv_pdfs"


def _profile_of(d: Driver) -> Path:
    return Path(d.context.bot_data[bot._PROFILE_DIR_KEY])


# --- the step ------------------------------------------------------------------------


def test_step_nine_asks_one_pdf_per_family_and_language(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    assert t("onboarding.cv.send", "en", slot="AI · ES") in d.last
    for line in (
        "⏳ AI · ES 👈",
        "⏳ AI · EN",
        "⏳ Data Analyst · ES",
        "⏳ Data Analyst · EN",
    ):
        assert line in d.last
    buttons = [data for _, data in d.buttons()]
    for key in ("AI_ES", "AI_EN", "DATA_ANALYST_ES", "DATA_ANALYST_EN"):
        assert f"ob:cv_pdfs:pick:{key}" in buttons
    assert "ob:cv_pdfs:skip" in buttons and "ob:cv_pdfs:skip_all" in buttons
    assert "ob:cv_pdfs:back" in buttons and "ob:cv_pdfs:cancel" in buttons


@pytest.mark.parametrize("lang", ["en", "es"])
def test_uploaded_pdfs_are_stored_with_their_sha256_only_on_confirm(
    tmp_path: Path, lang: Lang
) -> None:
    d, profile, _ = _setup_mode(tmp_path, lang)
    _to_cv_step(d, lang)
    ai_es, ai_en = _pdf(1), _pdf(2)
    taken, message = _send(d, FakeDocument(ai_es, file_name="../../evil.pdf"))
    assert taken
    assert (
        t(
            "onboarding.cv.received",
            lang,
            slot="AI · ES",
            pages=t("onboarding.cv.pages.one", lang),
        )
        in message.replies[0]
    )
    assert "AI · EN 👈" in d.last  # the next open slot
    _send(d, FakeDocument(ai_en))
    assert not (profile / "cv_base_pdfs").exists()  # pending until Confirm
    assert sorted(p.name for p in (profile / PENDING).iterdir()) == [
        "AI_EN.pdf",
        "AI_ES.pdf",
    ]
    d.press("skip_all")
    if d.step == "cv_pdfs":  # beta3-ux T4: Skip all warns first
        d.press("skip_all_yes")
    assert d.step == "summary"
    summary = d.last
    assert t("onboarding.edit.cv_pdfs", lang) in summary
    assert "AI · ES, AI · EN" in summary
    assert (
        t(
            "onboarding.cv.summary_missing",
            lang,
            slots="Data Analyst · ES, Data Analyst · EN",
        )
        in summary
    )

    d.press("confirm")
    done = d.replies[-1]
    assert t("onboarding.cv.saved", lang, count=2) in done
    assert (
        t(
            "onboarding.cv.not_deliverable",
            lang,
            slots="Data Analyst · ES, Data Analyst · EN",
        )
        in done
    )
    assert str(tmp_path) not in done
    base = profile / "cv_base_pdfs"
    manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    assert manifest == {
        "variants": {
            "AI_ES": {
                "file": "CV_AI_Jose_Perez.pdf",
                "sha256": manifest["variants"]["AI_ES"]["sha256"],
            },
            "AI_EN": {
                "file": "CV_AI_Jose_Perez_EN.pdf",
                "sha256": manifest["variants"]["AI_EN"]["sha256"],
            },
        }
    }
    assert (base / "CV_AI_Jose_Perez.pdf").read_bytes() == ai_es
    assert (base / "CV_AI_Jose_Perez_EN.pdf").read_bytes() == ai_en
    assert sorted(p.name for p in base.iterdir()) == [
        "CV_AI_Jose_Perez.pdf",
        "CV_AI_Jose_Perez_EN.pdf",
        "manifest.json",
    ]
    assert not (profile / PENDING).exists()
    assert not (tmp_path / "evil.pdf").exists()
    # The delivery code accepts what was written.
    registry = load_preferences(profile / "preferences.toml").families
    assert deliverable_variants(base, registry) == {"AI_ES", "AI_EN"}
    assert verified_base_pdf("AI_EN", base) == base / "CV_AI_Jose_Perez_EN.pdf"
    cut = available_registry(registry, base)
    ai = cut.get("ai")
    assert ai is not None and ai.languages == ("es", "en")


def test_a_slot_button_chooses_which_slot_the_next_pdf_is_for(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("pick:DATA_ANALYST_EN")
    assert "Data Analyst · EN 👈" in d.last
    _send(d, FakeDocument(_pdf()))
    assert [p.name for p in (profile / PENDING).iterdir()] == ["DATA_ANALYST_EN.pdf"]
    assert "✅ Data Analyst · EN" in d.last and "AI · ES 👈" in d.last


def test_skip_one_moves_on_and_marks_it(tmp_path: Path) -> None:
    d, _, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("skip")
    assert "⏭ AI · ES" in d.last and "AI · EN 👈" in d.last
    assert d.step == "cv_pdfs"


# --- refusals --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "error"),
    [
        (FakeDocument(_pdf(), file_name="cv.docx"), "onboarding.err.cv_not_pdf"),
        (FakeDocument(_pdf(), mime_type="image/png"), "onboarding.err.cv_not_pdf"),
        (
            FakeDocument(_pdf(), file_size=cv_upload.MAX_BYTES + 1),
            "onboarding.err.cv_too_big",
        ),
    ],
)
def test_not_a_pdf_or_too_big_is_refused_before_downloading(
    tmp_path: Path, document: FakeDocument, error: str
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    taken, message = _send(d, document)
    assert taken and message.replies[0].startswith(
        t(error, "en", mb=10, pages=cv_upload.MAX_PAGES)
    )
    assert document.get_file_calls == 0
    assert not any((profile / PENDING).glob("*.pdf"))
    assert "AI · ES 👈" in d.last  # same slot asked again


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"PK\x03\x04 not a pdf", "onboarding.err.cv_not_pdf"),
        (b"%PDF-1.7 truncated garbage", "onboarding.err.cv_not_pdf"),
        (_pdf(cv_upload.MAX_PAGES + 1), "onboarding.err.cv_pages"),
    ],
)
def test_downloaded_file_must_be_a_readable_pdf_of_sane_length(
    tmp_path: Path, data: bytes, error: str
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    document = FakeDocument(data)
    _, message = _send(d, document)
    assert document.get_file_calls == 1
    assert message.replies[0].startswith(
        t(error, "en", mb=10, pages=cv_upload.MAX_PAGES)
    )
    assert list((profile / PENDING).iterdir()) == []  # temp file removed
    assert "⏳ AI · ES 👈" in d.last


def test_a_failed_download_is_reported_and_leaves_nothing(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    _, message = _send(d, FakeDocument(_pdf(), error=OSError("network")))
    assert message.replies[0].startswith(t("onboarding.err.cv_download", "en"))
    assert list((profile / PENDING).iterdir()) == []


# --- pending uploads never outlive the setup -----------------------------------------


def test_cancel_removes_pending_uploads(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    _send(d, FakeDocument(_pdf()))
    d.press("cancel")
    assert not (profile / PENDING).exists()
    assert not (profile / "cv_base_pdfs").exists()


def test_expiry_and_a_new_setup_remove_pending_uploads(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    _send(d, FakeDocument(_pdf()))
    d.context.user_data[flow.STATE_AT_KEY] = time.time() - flow.TTL_SECONDS - 1
    taken, _ = _send(d, FakeDocument(_pdf()))
    assert not taken and not (profile / PENDING).exists()

    _to_cv_step(d)
    _send(d, FakeDocument(_pdf()))
    d.start()  # starting over drops the previous run's uploads
    assert not (profile / PENDING).exists()


def test_a_document_outside_step_nine_is_left_to_the_bot(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    taken, _ = _send(d, FakeDocument(_pdf()))  # no setup running
    assert not taken
    d.start()
    taken, message = _send(d, FakeDocument(_pdf()))  # setup on step 1
    assert not taken and message.replies == []
    taken, _ = _send(d, None)  # a message without a document
    assert not taken
    assert not (profile / PENDING).exists()


# --- existing behaviour kept ----------------------------------------------------------


def test_questionnaire_without_uploads_writes_no_manifest(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    _to_cv_step(d)
    d.press("skip_all")
    if d.step == "cv_pdfs":  # beta3-ux T4: Skip all warns first
        d.press("skip_all_yes")
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert t("onboarding.cv.saved", "en", count=0) not in d.replies[-1]
    assert (profile / "preferences.toml").is_file()
    assert not (profile / "cv_base_pdfs").exists()


def test_replacing_one_variant_keeps_every_other_manifest_entry(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    base = profile / "cv_base_pdfs"
    manifest_path = base / "manifest.json"
    before = json.loads(manifest_path.read_text("utf-8"))
    before["variants"]["AI_EN"]["delivery_name"] = "AI_Engineer"
    manifest_path.write_text(json.dumps(before, indent=2) + "\n", encoding="utf-8")
    old_files = {p.name: p.read_bytes() for p in base.glob("*.pdf")}

    _to_cv_step(d, custom=False)  # AI + Backend, like the demo
    d.press("pick:AI_EN")
    new = _pdf(2)
    _send(d, FakeDocument(new))
    d.press("done")
    d.press("confirm")

    after = json.loads(manifest_path.read_text("utf-8"))
    assert after["environment"] == before["environment"]
    assert after["variants"]["AI_EN"]["file"] == "CV_AI_Jose_Perez_EN.pdf"
    assert after["variants"]["AI_EN"]["delivery_name"] == "AI_Engineer"
    assert {k: v for k, v in after["variants"].items() if k != "AI_EN"} == {
        k: v for k, v in before["variants"].items() if k != "AI_EN"
    }
    assert (base / "CV_AI_Jose_Perez_EN.pdf").read_bytes() == new
    for name, data in old_files.items():  # no existing PDF overwritten
        assert (base / name).read_bytes() == data
    registry = load_preferences(profile / "preferences.toml").families
    assert "AI_EN" in deliverable_variants(base, registry)
    verified_base_pdf("AI_EN", base)
    verified_base_pdf("AI_ES", base)


def test_an_unreadable_manifest_is_never_overwritten(tmp_path: Path) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    base = profile / "cv_base_pdfs"
    base.mkdir()
    (base / "manifest.json").write_text("{broken", encoding="utf-8")
    _to_cv_step(d)
    _send(d, FakeDocument(_pdf()))
    d.press("done")
    d.press("confirm")
    assert d.step == "summary"  # kept: the user can fix the file and confirm again
    assert t("onboarding.write_failed", "en", error="")[:20] in d.replies[-1]
    assert str(tmp_path) not in d.replies[-1]
    assert (base / "manifest.json").read_text("utf-8") == "{broken"


# --- wiring (bot.py) -------------------------------------------------------------------


def test_documents_reach_step_nine_in_both_apps(
    tmp_path: Path, clean_settings: Callable[..., Settings]
) -> None:
    profile = tmp_path / "me"
    profile.mkdir()
    setup = bot.build_setup_app(clean_settings(profile_dir=str(profile)))
    callbacks = [h.callback for h in setup.handlers[1]]
    assert callbacks.index(bot.setup_document) < callbacks.index(bot.setup_not_ready)
    service, _ = make_service(tmp_path, FakeFetcher(FetchResponse(200, "", "u")))
    full = bot.build_app(clean_settings(), service)
    assert any(h.callback is bot.document_handler for h in full.handlers[0])


def test_setup_mode_document_outside_step_nine_gets_the_not_ready_reply(
    tmp_path: Path,
) -> None:
    d, _, _ = _setup_mode(tmp_path)
    message = FakeMessage()
    message.document = FakeDocument(_pdf())  # type: ignore[attr-defined]
    asyncio.run(bot.setup_document(FakeUpdate(message=message), d.context))
    assert message.replies == [t("bot.setup.not_ready", "en")]

    _to_cv_step(d)
    message = FakeMessage()
    message.document = FakeDocument(_pdf())  # type: ignore[attr-defined]
    asyncio.run(bot.setup_document(FakeUpdate(message=message), d.context))
    assert message.replies[0].startswith(
        t(
            "onboarding.cv.received",
            "en",
            slot="AI · ES",
            pages=t("onboarding.cv.pages.one", "en"),
        )
    )


# --- Confirm is all-or-nothing (verifier B1) -------------------------------------------


def test_a_failed_confirm_saves_nothing_and_a_retry_works_without_reuploading(
    tmp_path: Path,
) -> None:
    d, profile, _ = _setup_mode(tmp_path)
    judge = profile / "judge"
    judge.mkdir()
    (judge / "rubric_persona.md").write_text("broken, no slots", encoding="utf-8")
    _to_cv_step(d)
    pdf = _pdf()
    _send(d, FakeDocument(pdf))
    d.press("done")
    d.press("confirm")

    assert d.step == "summary"
    assert t("onboarding.write_failed", "en", error="")[:20] in d.replies[-1]
    assert t("onboarding.saved", "en") not in d.replies[-1]
    assert sorted(p.name for p in profile.iterdir()) == [PENDING, "judge"]
    assert [p.name for p in judge.iterdir()] == ["rubric_persona.md"]
    assert (profile / PENDING / "AI_ES.pdf").read_bytes() == pdf  # kept for a retry

    shutil.rmtree(judge)  # the user fixes the cause
    d.press("confirm")
    assert d.replies[-1].startswith(t("onboarding.saved", "en"))
    assert t("onboarding.cv.saved", "en", count=1) in d.replies[-1]
    base = profile / "cv_base_pdfs"
    assert verified_base_pdf("AI_ES", base).read_bytes() == pdf
    assert not (profile / PENDING).exists()


def test_a_failed_confirm_over_a_profile_restores_every_file_it_touched(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")

    def files() -> dict[str, bytes]:
        return {
            str(p.relative_to(profile)): p.read_bytes()
            for p in sorted(profile.rglob("*"))
            if p.is_file() and PENDING not in p.parts
        }

    before = files()

    def broken_hook(profile_dir: Path, answers: dict[str, object]) -> None:
        raise flow.ProfileError("hook failed")

    monkeypatch.setattr(flow, "CONFIRM_HOOKS", [broken_hook])
    _to_cv_step(d, custom=False)
    d.press("pick:AI_EN")
    _send(d, FakeDocument(_pdf(2)))
    d.press("done")
    d.press("confirm")

    assert d.step == "summary"
    assert "hook failed" in d.replies[-1]
    assert files() == before  # preferences, profile, .bak, PDFs, manifest
    assert (profile / PENDING / "AI_EN.pdf").is_file()


# --- variants the profile already has (verifier B2) ------------------------------------


def test_rerun_counts_the_base_cvs_already_in_the_manifest(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, profile, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    base = profile / "cv_base_pdfs"
    manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    (base / manifest["variants"]["BACKEND_EN"]["file"]).write_bytes(_pdf(3))  # stale

    _to_cv_step(d, custom=False)  # AI + Backend in es + en
    existing = t("onboarding.cv.existing", "en")
    for line in ("AI · ES", "AI · EN", "Backend · ES"):
        assert f"✅ {line} {existing}" in d.last
    assert "⏳ Backend · EN" in d.last  # its PDF no longer matches the manifest
    assert "ob:cv_pdfs:pick:AI_EN" in [a for _, a in d.buttons()]

    _send(d, FakeDocument(_pdf()))  # the first open slot: Backend · EN
    d.press("done")
    d.press("confirm")
    assert t("onboarding.cv.saved", "en", count=1) in d.replies[-1]
    assert "No base CV" not in d.replies[-1]
    verified_base_pdf("BACKEND_EN", base)


def test_rerun_reports_only_the_slots_really_without_a_base_cv(
    tmp_path: Path,
    clean_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d, _, _, _ = _full_app(tmp_path, clean_settings, monkeypatch, "en")
    _to_cv_step(d)  # AI + Data Analyst (no CV yet) in es + en
    d.press("skip_all")
    if d.step == "cv_pdfs":  # beta3-ux T4: Skip all warns first
        d.press("skip_all_yes")
    d.press("confirm")
    assert (
        t(
            "onboarding.cv.not_deliverable",
            "en",
            slots="Data Analyst · ES, Data Analyst · EN",
        )
        in d.replies[-1]
    )
