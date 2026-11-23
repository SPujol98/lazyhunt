"""Opt-in "Adapt to this offer" (tailored-cv T3, S1/S3/S4(b)(c)/S5/S6).

After "Generate CV" delivered the base PDF, CVs lazyhunt built itself (a
current structured source) get one extra button. Pressing it proposes a plan
(nothing written), "Use this CV" renders a DISTINCT adapted PDF and records
it with an ``ADAPTED:`` marker, "Redo CV" still delivers the base, and the
owner's install (hand-made PDFs, legacy layout) never shows the button.
All LLM calls are fakes.
"""

import asyncio
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pypdf import PdfReader

import bot
from cva import service as service_module
from cva.claimsdb import load_claims
from cva.cvgen.base_pdfs import verified_base_pdf
from cva.cvgen.tailor import SectionOrder, Substitution, TailorPlan
from cva.fetch import FetchResponse
from cva.i18n import Lang, t
from cva.match.extraction import (
    ExtractionCache,
    OfferExtraction,
    Requirement,
    cache_key,
)
from cva.match.scorer import classify_keyword, load_synonyms
from cva.notifier import sent_keyboard
from cva.profile_paths import resolve_profile_paths
from cva.service import BASE_CAMBIOS, JobService, ServiceError
from cva.tracker import OfferInput, OfferRepository
from tests.test_bot import FakeContext, FakeQuery, FakeUpdate
from tests.test_cv_source import _confirmed_created

DEMO = Path(__file__).resolve().parents[1] / "profiles" / "demo"
OFFER_ES = (
    "Buscamos AI Engineer con experiencia en SQL y Python para nuestro equipo "
    "de datos en Valencia. Trabajarás con bases de datos y modelos."
)
URL = "https://www.linkedin.com/jobs/view/4459800001/"


def _plan() -> TailorPlan:
    return TailorPlan(
        order=[
            SectionOrder(
                section="skills", claim_ids=["setup-skill-sql", "setup-skill-python"]
            )
        ],
        headline_skills=["setup-skill-sql"],
        substitutions=[
            Substitution(
                claim_id="setup-skill-python",
                from_text="Python",
                to_text="Pythonista",
                reason="invented",
            )
        ],
    )


class FakeLlm:
    """Zero-network stand-in: one crafted plan (or an error), calls recorded."""

    model = "fake-model"

    def __init__(self, reply: TailorPlan | None = None, error: bool = False) -> None:
        self.reply = reply if reply is not None else _plan()
        self.error = error
        self.models: list[str | None] = []

    async def json_completion[ModelT: BaseModel](
        self, system: str, user: str, schema: type[ModelT], *, model: str | None = None
    ) -> ModelT:
        self.models.append(model)
        if self.error:
            raise RuntimeError("provider down")
        return self.reply  # type: ignore[return-value]


class _Fetcher:
    async def get(self, url: str) -> FetchResponse:  # pragma: no cover
        raise AssertionError("no fetch expected")


def _service(
    tmp_path: Path,
    profile: Path | None,
    llm: FakeLlm,
    *,
    mirror: Path | None = None,
) -> tuple[JobService, OfferRepository]:
    paths = resolve_profile_paths(profile)
    conn = sqlite3.connect(tmp_path / "app.db", check_same_thread=False)
    repo = OfferRepository(conn)
    db = load_claims(paths.claims)
    svc = JobService(
        repo=repo,
        llm=llm,  # type: ignore[arg-type]
        fetcher=_Fetcher(),  # type: ignore[arg-type]
        db=db,
        syn=load_synonyms(paths.synonyms, db),
        cache=ExtractionCache(conn),
        profile_paths=paths,
        output_dir=tmp_path / "out",
        mirror_dir=mirror,
        extraction_model="extract-m",
        judge_model="judge-m",
    )
    return svc, repo


def _offer(repo: OfferRepository, text: str = OFFER_ES) -> int:
    return repo.upsert_offer(
        OfferInput(
            title="AI Engineer",
            company="Acme",
            url=URL,
            text=text,
            source="bridge-inline",
            profile="ai",
        )
    )


def _created(
    tmp_path: Path, llm: FakeLlm | None = None, *, mirror: Path | None = None
) -> tuple[JobService, OfferRepository, Path, FakeLlm]:
    profile = _confirmed_created(tmp_path)
    fake = llm if llm is not None else FakeLlm()
    svc, repo = _service(tmp_path, profile, fake, mirror=mirror)
    return svc, repo, profile, fake


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob("*.pdf"))


def _pdf_text(path: Path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


# --- service ---------------------------------------------------------------


def test_proposal_writes_nothing_and_uses_the_judge_model(tmp_path: Path) -> None:
    svc, repo, _, llm = _created(tmp_path)
    oid = _offer(repo)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    before = repo.get_offer(oid)
    proposal = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert llm.models == ["judge-m"]
    assert list(proposal.changes) == [
        t("tailor.change.moved_top", "en", section="Skills", item="SQL"),
        t("tailor.change.headline", "en", skills="SQL"),
    ]
    assert [v.reason for v in proposal.dropped] == ["to_not_in_offer"]
    assert repo.get_offer(oid) == before  # nothing recorded yet
    assert _files(tmp_path / "out") == [base.name]  # nothing rendered yet


def test_use_renders_a_distinct_file_and_keeps_the_base_byte_identical(
    tmp_path: Path,
) -> None:
    svc, repo, profile, _ = _created(tmp_path)
    oid = _offer(repo)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    stored = verified_base_pdf("AI_ES", profile / "cv_base_pdfs")
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    adapted = asyncio.run(svc.accept_tailored(oid))
    assert adapted.parent == base.parent
    assert adapted.name == f"{base.stem}_adapted_{oid}.pdf"
    assert _sha(base) == _sha(stored)  # the base copy is untouched
    assert "AI Engineer · SQL" in _pdf_text(adapted)
    row = repo.get_offer(oid)
    assert row.cv_base == "AI_ES"
    assert row.cv_adaptado == adapted.as_posix()
    assert row.cambios == f"ADAPTED:{_sha(adapted)[:8]}:2"
    with pytest.raises(ServiceError) as caught:  # the proposal was consumed
        asyncio.run(svc.accept_tailored(oid))
    assert caught.value.key == "tailor.bot.expired"


def test_redo_after_use_delivers_the_base_again(tmp_path: Path) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    asyncio.run(svc.accept_tailored(oid))
    redo = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    row = repo.get_offer(oid)
    assert row.cv_adaptado == redo.as_posix()
    assert (row.cambios or "").startswith(BASE_CAMBIOS)


def test_mark_sent_moves_the_adapted_file_in_the_mirror(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    svc, repo, _, _ = _created(tmp_path, mirror=mirror)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    adapted = asyncio.run(svc.accept_tailored(oid))
    assert adapted.name in _files(mirror / "nuevos")
    repo.save_match_result(oid, score=80, threshold=65, breakdown_json="{}")
    svc.mark_sent(oid)
    assert _files(mirror / "enviados") == [adapted.name]
    assert adapted.name not in _files(mirror / "nuevos")
    row = repo.get_offer(oid)
    assert row.fecha_envio is not None
    assert (row.cambios or "").startswith("ADAPTED:")


def test_remove_one_change_and_discard(tmp_path: Path) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    proposal = svc.tailor_without(oid, 0, ui="es")
    assert list(proposal.changes) == [t("tailor.change.headline", "es", skills="SQL")]
    assert proposal.plan.order == []
    assert svc.discard_tailored(oid) is True
    assert svc.discard_tailored(oid) is False
    with pytest.raises(ServiceError) as caught:
        svc.tailor_without(oid, 0, ui="es")
    assert caught.value.key == "tailor.bot.expired"


def test_a_failed_final_check_never_produces_a_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    monkeypatch.setattr(service_module, "final_check", lambda *a, **k: ["Kubernetes"])
    with pytest.raises(ServiceError) as caught:
        asyncio.run(svc.accept_tailored(oid))
    assert caught.value.key == "tailor.bot.unsafe"
    with pytest.raises(ServiceError):
        asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert _files(tmp_path / "out") == [base.name]
    assert repo.get_offer(oid).cv_adaptado == base.as_posix()


def test_llm_failure_leaves_the_base_cv_untouched(tmp_path: Path) -> None:
    svc, repo, _, _ = _created(tmp_path, FakeLlm(error=True))
    oid = _offer(repo)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    before = repo.get_offer(oid)
    with pytest.raises(RuntimeError):
        asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert repo.get_offer(oid) == before
    assert _files(tmp_path / "out") == [base.name]


def test_page_limit_drops_headline_then_order_then_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    real = service_module.render_source
    rendered: list[str] = []

    def long_with_headline(source: Any, out: Path, **kw: Any) -> int:
        rendered.append(source.headline)
        real(source, out, **kw)
        return 2 if " · " in source.headline else 1

    monkeypatch.setattr(service_module, "render_source", long_with_headline)
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    adapted = asyncio.run(svc.accept_tailored(oid))
    assert rendered == ["AI Engineer · SQL", "AI Engineer"]
    assert repo.get_offer(oid).cambios == f"ADAPTED:{_sha(adapted)[:8]}:1"

    def always_long(source: Any, out: Path, **kw: Any) -> int:
        real(source, out, **kw)
        return 2

    monkeypatch.setattr(service_module, "render_source", always_long)
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    with pytest.raises(ServiceError) as caught:
        asyncio.run(svc.accept_tailored(oid))
    assert caught.value.key == "tailor.bot.too_long"
    assert repo.get_offer(oid).cv_adaptado == adapted.as_posix()  # previous kept
    assert len(list((tmp_path / "out").rglob("*.pdf"))) == 2  # base + first adapted


@pytest.mark.owner  # the legacy layout reads the owner's own claims
def test_owner_legacy_and_uploaded_slots_are_not_adaptable(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    shutil.copytree(DEMO, demo)
    for name, owner in (("owner", demo), ("legacy", None)):
        (tmp_path / name).mkdir()
        svc, _ = _service(tmp_path / name, owner, FakeLlm())
        assert svc.can_adapt("AI_ES") is False
    (tmp_path / "c").mkdir()
    svc, repo, profile, llm = _created(tmp_path / "c")
    assert svc.can_adapt("AI_ES") is True
    assert svc.can_adapt(None) is False
    base = profile / "cv_base_pdfs"
    shutil.copyfile(
        DEMO / "cv_base_pdfs" / "CV_AI_Engineer_Alex_Rivera.pdf", base / "up.pdf"
    )
    manifest = json.loads((base / "manifest.json").read_text("utf-8"))
    manifest["variants"]["AI_ES"] = {"file": "up.pdf", "sha256": _sha(base / "up.pdf")}
    (base / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    assert svc.can_adapt("AI_ES") is False
    oid = _offer(repo)
    with pytest.raises(ServiceError) as caught:
        asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert caught.value.key == "tailor.bot.not_adaptable"
    assert llm.models == []


# --- Telegram ----------------------------------------------------------------


def _context(svc: JobService, lang: Lang = "en") -> FakeContext:
    context = FakeContext(svc)
    context.bot_data[bot._LANG_KEY] = lang
    return context


def _press(svc: JobService, data: str, lang: Lang = "en") -> FakeQuery:
    query = FakeQuery(data)
    asyncio.run(bot.callback_router(FakeUpdate(query=query), _context(svc, lang)))
    return query


def _callbacks(markup: Any) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _labels(markup: Any) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_new_prefixes_are_registered() -> None:
    assert bot.CALLBACK_HANDLERS["cvt"] is bot._cb_tailor
    assert bot.CALLBACK_HANDLERS["cva"] is bot._cb_tailor_action


def test_sent_keyboard_is_unchanged_unless_adaptable() -> None:
    plain = sent_keyboard(7, cv_base="AI_ES", lang="en")
    assert not [c for c in _callbacks(plain) if c.startswith("cvt")]
    adapt = sent_keyboard(7, cv_base="AI_ES", adaptable=True, lang="en")
    assert _callbacks(adapt) == [*_callbacks(plain), "cvt:7:AI:es"]
    assert _labels(adapt)[-1] == t("tailor.bot.button.adapt", "en")
    assert _labels(sent_keyboard(7, cv_base="AI_ES", adaptable=True, lang="es"))[
        -1
    ] == t("tailor.bot.button.adapt", "es")


@pytest.mark.parametrize("lang", ["en", "es"])
def test_generate_cv_shows_the_adapt_button_for_a_created_cv(
    tmp_path: Path, lang: Lang
) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    query = _press(svc, f"cv:{oid}:AI:es", lang)
    assert query.message.documents  # the base PDF went out first
    assert f"cvt:{oid}:AI:es" in _callbacks(query.message.edited_markups[-1])


def test_owner_install_and_uploaded_pdf_show_no_adapt_button(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    shutil.copytree(DEMO, demo)
    svc, repo = _service(tmp_path, demo, FakeLlm())
    oid = _offer(repo)
    query = _press(svc, f"cv:{oid}:AI:es")
    assert query.message.documents
    assert not [
        c for c in _callbacks(query.message.edited_markups[-1]) if c.startswith("cvt")
    ]
    message = FakeQuery("x").message
    context = _context(svc)
    context.args = [str(oid), "AI", "es"]
    asyncio.run(bot.cv_command(FakeUpdate(message=message), context))
    assert not [c for c in _callbacks(message.markups[-1]) if c.startswith("cvt")]


def test_cv_command_shows_the_adapt_button_for_a_created_cv(tmp_path: Path) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    message = FakeQuery("x").message
    context = _context(svc)
    context.args = [str(oid), "AI", "es"]
    asyncio.run(bot.cv_command(FakeUpdate(message=message), context))
    assert f"cvt:{oid}:AI:es" in _callbacks(message.markups[-1])


@pytest.mark.parametrize("lang", ["en", "es"])
def test_adapt_previews_then_use_sends_the_adapted_pdf(
    tmp_path: Path, lang: Lang
) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    query = _press(svc, f"cvt:{oid}:AI:es", lang)
    first, preview = query.message.replies
    assert first == t("tailor.bot.preparing", lang)
    assert "1. " in preview and "2. " in preview
    assert t("tailor.bot.dropped.one", lang, n=1) in preview
    markup = query.message.markups[-1]
    assert _callbacks(markup) == [
        f"cva:{oid}:accept",
        f"cva:{oid}:rm:0",
        f"cva:{oid}:rm:1",
        f"cva:{oid}:discard",
    ]
    assert _labels(markup)[0] == t("tailor.bot.button.use", lang)
    assert _labels(markup)[1] == t("tailor.bot.button.remove", lang, n=1)
    used = _press(svc, f"cva:{oid}:accept", lang)
    (name,) = used.message.documents
    assert name.endswith(f"_adapted_{oid}.pdf")
    assert used.message.captions == [t("tailor.bot.adapted_caption", lang)]
    assert repo.get_offer(oid).cv_base == "AI_ES"  # Redo CV still builds the base


def test_remove_one_edits_the_preview_and_discard_drops_it(tmp_path: Path) -> None:
    svc, repo, _, _ = _created(tmp_path)
    oid = _offer(repo)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    _press(svc, f"cvt:{oid}:AI:es")
    removed = _press(svc, f"cva:{oid}:rm:0")
    assert t("tailor.change.headline", "en", skills="SQL") in removed.edited[-1]
    assert _callbacks(removed.edited_markups[-1]) == [
        f"cva:{oid}:accept",
        f"cva:{oid}:rm:0",
        f"cva:{oid}:discard",
    ]
    discarded = _press(svc, f"cva:{oid}:discard")
    assert discarded.edited[-1] == t("tailor.bot.discarded", "en")
    late = _press(svc, f"cva:{oid}:accept")
    assert late.message.replies == [t("tailor.bot.expired", "en")]
    assert late.message.documents == []


def test_no_changes_and_llm_failure_are_friendly(tmp_path: Path) -> None:
    svc, repo, _, llm = _created(tmp_path, FakeLlm(TailorPlan()))
    oid = _offer(repo)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    query = _press(svc, f"cvt:{oid}:AI:es")
    assert query.message.replies[-1] == t("tailor.bot.no_changes", "en")
    llm.error = True
    failed = _press(svc, f"cvt:{oid}:AI:es")
    assert failed.message.replies[-1] == t("tailor.bot.failed", "en")
    assert repo.get_offer(oid).cv_adaptado == base.as_posix()


@pytest.mark.parametrize("data", ["cvt:1", "cvt:x:AI:es", "cva:1", "cva:1:nope"])
def test_malformed_tailor_payloads_get_the_unknown_action_reply(
    tmp_path: Path, data: str
) -> None:
    svc, _, _, _ = _created(tmp_path)
    query = _press(svc, data)
    assert query.message.replies == [t("bot.unknown_action", "en")]


# --- proposed synonyms the user approves (S7) ---------------------------------

OFFER_SYN = OFFER_ES + " Imprescindible español materno."


def _syn_plan() -> TailorPlan:
    return TailorPlan(
        headline_skills=["setup-skill-sql"],
        substitutions=[
            Substitution(
                claim_id="setup-lang-es",
                from_text="Nativo",
                to_text="materno",
                reason="the offer says materno",
            )
        ],
    )


def _synonyms_file(profile: Path) -> str:
    return (profile / "synonyms.json").read_text("utf-8")


def test_using_a_proposed_synonym_saves_it_and_the_service_reloads(
    tmp_path: Path,
) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    proposal = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    assert proposal.changes[-1] == t(
        "tailor.change.new_synonym", "es", old="Nativo", new="Materno"
    )
    assert _synonyms_file(profile) == "{}\n"  # a proposal writes nothing
    adapted = asyncio.run(svc.accept_tailored(oid))
    assert "Español — Materno" in _pdf_text(adapted)
    assert json.loads(_synonyms_file(profile)) == {
        "materno": {"claim_id": "setup-lang-es", "strength": "weak"}
    }
    db = load_claims(profile / "claims.json")
    assert "materno" in load_synonyms(profile / "synonyms.json", db).entries
    # reloaded: scoring sees it, and the next adaptation applies it as approved
    assert classify_keyword("materno", svc._db, svc._syn) == "~"
    again = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    assert again.changes[-1] == t(
        "tailor.change.swap", "es", old="Nativo", new="Materno"
    )


def test_discarding_or_removing_a_proposed_synonym_saves_nothing(
    tmp_path: Path,
) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert svc.discard_tailored(oid) is True
    assert _synonyms_file(profile) == "{}\n"
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    left = svc.tailor_without(oid, 1, ui="en")  # 0 = headline, 1 = the swap
    assert left.plan.substitutions == []
    adapted = asyncio.run(svc.accept_tailored(oid))
    assert "Nativo" in _pdf_text(adapted)
    assert _synonyms_file(profile) == "{}\n"


@pytest.mark.owner  # the legacy layout reads the owner's own claims
def test_legacy_install_never_writes_synonyms(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path, None, FakeLlm())
    packaged = resolve_profile_paths(None).synonyms
    before = packaged.read_bytes()
    sub = _syn_plan().substitutions[0]
    assert svc._save_new_synonyms([sub]) == []
    assert packaged.read_bytes() == before


def test_bot_preview_marks_the_new_synonym_and_use_saves_it(tmp_path: Path) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    query = _press(svc, f"cvt:{oid}:AI:es", "es")
    preview = query.message.replies[-1]
    assert (
        "2. " + t("tailor.change.new_synonym", "es", old="Nativo", new="Materno")
        in preview
    )
    assert t("tailor.bot.new_synonym_note", "es") in preview
    _press(svc, f"cva:{oid}:accept", "es")
    assert "materno" in json.loads(_synonyms_file(profile))


def test_the_offers_tool_requirements_are_never_proposed_as_synonyms(
    tmp_path: Path,
) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    cache = ExtractionCache(repo._conn)
    extraction = OfferExtraction(
        requirements=[
            Requirement(
                name="Materno", category="tool", must_have=True, evidence="materno"
            )
        ]
    )
    cache.put(cache_key(OFFER_SYN, "extract-m"), extraction, model="extract-m")
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    proposal = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="en"))
    assert proposal.plan.substitutions == [] and proposal.new_synonyms == ()
    assert [v.reason for v in proposal.dropped] == ["synonym_technology"]
    asyncio.run(svc.accept_tailored(oid))
    assert _synonyms_file(profile) == "{}\n"


# --- hygiene end to end (verifier blockers B1, B2; advisory b) -----------------


def _one_swap(old: str, new: str, claim_id: str = "setup-lang-es") -> TailorPlan:
    return TailorPlan(
        substitutions=[
            Substitution(claim_id=claim_id, from_text=old, to_text=new, reason="")
        ]
    )


def test_an_approved_synonym_never_carries_foreign_text_into_the_pdf(
    tmp_path: Path,
) -> None:
    svc, repo, profile, llm = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN + " materno Кубернетес АЅ, aguante ★★★★★, ＡＷＳ.")
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    asyncio.run(svc.accept_tailored(oid))  # "materno" is now approved
    saved = _synonyms_file(profile)
    for junk in ("materno Кубернетес АЅ", "materno ★★★★★", "ＡＷＳ"):
        llm.reply = _one_swap("Nativo", junk)
        proposal = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
        assert proposal.plan.substitutions == [], junk
        assert len(proposal.dropped) == 1, junk
        if junk != "ＡＷＳ":  # NFKC turns that one into a plain (tool) word
            assert proposal.dropped[0].reason == "unsafe_text", junk
        adapted = asyncio.run(svc.accept_tailored(oid))
        text = _pdf_text(adapted)
        assert "Кубернетес" not in text and "★" not in text and "Nativo" in text
    assert _synonyms_file(profile) == saved


@pytest.mark.parametrize(
    ("old", "new", "claim_id"),
    [
        ("Nativo", "s\u0435ni\u043er", "setup-lang-es"),
        ("Nativo", "kuber\u200bnetes", "setup-lang-es"),
        ("Nativo", "cinco años", "setup-lang-es"),
        ("Nativo", "scrum", "setup-lang-es"),
        ("Nativo", "pandas", "setup-lang-es"),
        ("Nativo", "power bi", "setup-lang-es"),
        ("Nativo", "machine learning", "setup-lang-es"),
        ("Nativo", "ia", "setup-lang-es"),
        ("Nativo", "bi", "setup-lang-es"),
        ("Nativo", "mercadona", "setup-lang-es"),
        ("Acme", "almacén", "setup-experience"),
        ("3-5", "estable", "setup-experience"),
        ("Backend Developer", "estable", "setup-experience"),
    ],
)
def test_unsafe_proposed_synonyms_are_never_offered_nor_saved(
    tmp_path: Path, old: str, new: str, claim_id: str
) -> None:
    offer = (
        OFFER_SYN + f" Se valora {new} en un puesto estable de almacén, "
        "trabajo con mercadona."
    )
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_one_swap(old, new, claim_id)))
    oid = _offer(repo, offer)
    asyncio.run(svc.generate_cv(oid, "AI", "es"))
    proposal = asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    assert proposal.plan.substitutions == [] and proposal.new_synonyms == ()
    assert [v.reason for v in proposal.dropped] != ["to_not_in_offer"]
    asyncio.run(svc.accept_tailored(oid))
    assert _synonyms_file(profile) == "{}\n"


def test_a_failed_synonym_save_aborts_the_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))
    before = repo.get_offer(oid)

    def broken(subs: object) -> list[str]:
        raise OSError("disk full")

    monkeypatch.setattr(svc, "_save_new_synonyms", broken)
    with pytest.raises(OSError):
        asyncio.run(svc.accept_tailored(oid))
    assert repo.get_offer(oid) == before  # nothing recorded
    assert _files(tmp_path / "out") == [base.name]  # no adapted PDF
    assert _synonyms_file(profile) == "{}\n"


def test_a_failed_record_puts_the_synonyms_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, repo, profile, _ = _created(tmp_path, FakeLlm(_syn_plan()))
    oid = _offer(repo, OFFER_SYN)
    base = asyncio.run(svc.generate_cv(oid, "AI", "es"))
    asyncio.run(svc.tailor_cv(oid, "AI", "es", ui="es"))

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(repo, "record_cv", broken)
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(svc.accept_tailored(oid))
    assert _synonyms_file(profile) == "{}\n"
    assert "materno" not in svc._syn.entries
    assert _files(tmp_path / "out") == [base.name]
